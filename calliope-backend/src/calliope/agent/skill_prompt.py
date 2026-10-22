"""Video prompts for workflows whose prompt format is ``skill``.

The rules live in a skill (``workflows.prompt_skill``, e.g. wan22-video-prompt)
rather than in code, so a new model family needs a skill, not a profile:

* :func:`base_prompt` -- instant, no LLM: the clip's own shot description, the
  characters by appearance and a style suffix from the project's tone. No
  dialogue (quoted speech makes image models paint speech bubbles) and no
  screenplay heading.
* :func:`rewrite_prompt` -- on Regenerate: the "video" role's model rewrites
  that base by the skill's rules. Any failure returns the base prompt.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from calliope.agent.llm import LLMClient
from calliope.agent.prompts import appearance_line
from calliope.agent.skills_store import ensure_builtin_skills, read_skill_file

logger = logging.getLogger(__name__)

STYLE_SUFFIXES = (
    (("anime", "manga", "cel"), "anime style, cel-shaded 2D animation, clean line art, rich but balanced colors, detailed background art"),
    (("3d", "cgi", "pixar"), "cinematic 3D CGI, soft global illumination, balanced exposure, detailed materials"),
)
DEFAULT_SUFFIX = "cinematic film still, natural lighting, balanced exposure"


def style_suffix(tone: str | None, genre: str | None = None) -> str:
    """Style words for the project's look, judged from its tone and genre."""
    text = f"{tone or ''} {genre or ''}".lower()
    for keys, suffix in STYLE_SUFFIXES:
        if any(re.search(rf"\b{re.escape(k)}\b", text) for k in keys):
            return suffix
    return DEFAULT_SUFFIX


# "wide shot. Wide shot: ..." -- the clip's shot size prefixed to a description
# that already opens with its own shot label.
_DOUBLE_SHOT = re.compile(r"^\w+ shot\.\s+(?=[\w -]{0,30}\bshot\b)", re.IGNORECASE)
# Quoted speech inside a description; image models paint it as text.
_QUOTED = re.compile(r"[:,]?\s*(?<![\w])['\"\u201c\u201e\u2018][^'\"\u201c\u201d\u2018\u2019]{8,}['\"\u201d\u2019](?![\w])")
# A style sentence at the front of an appearance; the suffix adds it once.
_STYLE_LEAD = re.compile(r"^(anime style|cinematic 3d cgi)[^.]*\.\s*", re.IGNORECASE)


def base_prompt(scene: dict[str, Any], characters: list[dict[str, Any]], project: dict[str, Any]) -> str:
    """The deterministic prompt: shot + action, characters by look, style."""
    action = _DOUBLE_SHOT.sub("", (scene.get("action") or "").strip())
    action = _QUOTED.sub("", action).strip().rstrip(".")
    looks = [
        f"{c.get('name')}: {_STYLE_LEAD.sub('', appearance_line(c.get('appearance'))).rstrip('.')}"
        for c in characters
        if (c.get("appearance") or "").strip()
    ]
    suffix = style_suffix(project.get("tone"), project.get("genre"))
    parts = [action]
    if looks:
        parts.append("Characters -- " + "; ".join(looks))
    if suffix.split(",")[0].lower() not in action.lower():
        parts.append(suffix)
    return ". ".join(p for p in parts if p) + "."


async def rewrite_prompt(
    skill: str,
    scene: dict[str, Any],
    characters: list[dict[str, Any]],
    project: dict[str, Any],
    *,
    has_start_image: bool,
) -> str:
    """LLM rewrite of :func:`base_prompt` by the skill's rules."""
    base = base_prompt(scene, characters, project)
    ensure_builtin_skills()
    doc = read_skill_file(skill)
    if not doc.get("ok"):
        logger.warning("skill prompt: skill %r not found; using the base prompt", skill)
        return base
    brief = "\n".join([
        f"Mode: {'image-to-video (a start image is given)' if has_start_image else 'text-to-video'}",
        f"Shot: {scene.get('action') or ''}",
        "Characters: " + ("; ".join(f"{c.get('name')} = {appearance_line(c.get('appearance'))}" for c in characters) or "none"),
        f"Dialogue spoken in this shot (show the speaking, never quote it): {scene.get('dialog') or 'none'}",
        f"Project tone: {project.get('tone') or ''}; genre: {project.get('genre') or ''}",
        f"Style suffix to use: {style_suffix(project.get('tone'), project.get('genre'))}",
        f"Current draft: {base}",
    ])
    messages = [
        {"role": "system", "content": (
            "You write ONE video-generation prompt following the skill below. "
            "Output only the prompt text: no quotes, no markdown, no commentary.\n\n" + doc["content"]
        )},
        {"role": "user", "content": brief},
    ]
    client = LLMClient.for_role("video", timeout=120.0)
    try:
        text = (await client.chat(messages, temperature=0.5)).strip()
    except Exception as exc:  # a dead endpoint must not block Generate
        logger.warning("skill prompt: rewrite failed (%s); using the base prompt", exc)
        return base
    finally:
        await client.close()
    text = re.sub(r"^```\w*\n?|\n?```$", "", text).strip().strip('"').strip()
    return text or base
