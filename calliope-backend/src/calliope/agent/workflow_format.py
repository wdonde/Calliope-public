"""Check a workflow's prompt format, and draft a prompt skill for a new model.

Known model families are recognised by node type (comfyui/profiles.py) with no
LLM call. Anything else goes to the "main" role's model, which sees only the
workflow's node types and model file names -- never prompts or user data.
"""
from __future__ import annotations

import datetime as dt
import re
from typing import Any

from calliope.prompt_library import text as prompt_text
from calliope.agent.llm import LLMClient, extract_json
from calliope.agent.skills_store import list_skills, skills_root
from calliope.comfyui.profiles import detect_prompt_profile, detect_prompt_skill

MODEL_FILE_RE = re.compile(r"\.(safetensors|gguf|ckpt|pt|pth|bin)$", re.IGNORECASE)
# Skills that are not prompt rules for a video model.
NOT_PROMPT_SKILLS = {"novel-condense", "shot-composer-blockout", "scene-to-video", "character-consistency"}


SKILL_DRAFT_SYSTEM = "You write concise technical Markdown. Output only the document."


def workflow_fingerprint(workflow_json: dict[str, Any]) -> dict[str, list[str]]:
    """Node types and model file names -- what identifies a model family."""
    nodes = [n for n in workflow_json.values() if isinstance(n, dict)]
    classes = sorted({str(n.get("class_type", "")) for n in nodes if n.get("class_type")})
    files = sorted({
        str(v) for n in nodes for v in (n.get("inputs") or {}).values()
        if isinstance(v, str) and MODEL_FILE_RE.search(v)
    })
    return {"node_types": classes, "model_files": files}


def _prompt_skills() -> list[dict[str, Any]]:
    """Skills a 'skill'-format workflow can use. The H3 skills belong to the
    minimax_h3_ref format, so offering them here only invites a wrong match."""
    return [
        s for s in list_skills()
        if s["name"] not in NOT_PROMPT_SKILLS and not s["name"].startswith("h3-")
    ]


async def _ask(prompt: str) -> dict[str, Any]:
    client = LLMClient.for_role("main", timeout=180.0)
    try:
        messages = [
            {"role": "system", "content": prompt_text("common.json_only")},
            {"role": "user", "content": prompt},
        ]
        try:
            return extract_json(await client.chat(messages, temperature=0.2))
        except ValueError:
            return extract_json(
                await client.chat(messages, temperature=0.2, response_format={"type": "json_object"})
            )
    except ValueError as exc:
        raise ValueError(f"The model gave no usable answer: {exc}") from exc
    finally:
        await client.close()


async def check_format(workflow_json: dict[str, Any], *, kind: str) -> dict[str, Any]:
    """Suggested ``{profile, skill, family, source, reason}`` for a workflow."""
    if kind != "video":
        return {"profile": "prose", "skill": None, "family": None, "source": "nodes",
                "reason": "Prompt format only matters for video workflows."}
    if detect_prompt_profile(workflow_json) == "minimax_h3_ref":
        return {"profile": "minimax_h3_ref", "skill": None, "family": "MiniMax H3", "source": "nodes",
                "reason": "The workflow contains MiniMaxH3 nodes."}
    skill = detect_prompt_skill(workflow_json)
    if skill:
        return {"profile": "skill", "skill": skill, "family": "Wan 2.2", "source": "nodes",
                "reason": f"Recognised by its nodes; '{skill}' holds the prompt rules."}

    fp = workflow_fingerprint(workflow_json)
    skills = _prompt_skills()
    answer = await _ask(
        "A ComfyUI video workflow uses these node types and model files:\n"
        f"node_types: {', '.join(fp['node_types'])}\n"
        f"model_files: {', '.join(fp['model_files']) or 'none'}\n\n"
        "Available prompt skills (name: description):\n"
        + "\n".join(f"- {s['name']}: {s['description']}" for s in skills)
        + "\n\nWhich video model family is this (e.g. Wan 2.1, Wan 2.2, HunyuanVideo, LTX-Video, "
        "CogVideoX, Mochi)? Pick the skill whose rules fit that family, or null when none fits -- "
        "a skill for a different model family does NOT fit.\n"
        'Answer: {"family": "...", "skill": "<skill name or null>", "reason": "<one sentence>"}'
    )
    names = {s["name"] for s in skills}
    picked = answer.get("skill") if answer.get("skill") in names else None
    return {
        "profile": "skill" if picked else "prose",
        "skill": picked,
        "family": str(answer.get("family") or "").strip() or None,
        "source": "llm",
        "reason": str(answer.get("reason") or "").strip()
        or ("" if picked else "No existing skill fits -- draft one."),
    }


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "video"


async def draft_skill(workflow_json: dict[str, Any], *, kind: str, workflow_name: str) -> dict[str, Any]:
    """Write a new ``<family>-video-prompt`` skill draft; returns its name.

    The "writer" role writes it -- a writing task, answered as plain Markdown
    (a long document packed into JSON is where small models fail).
    """
    if kind != "video":
        raise ValueError("Prompt skills are only drafted for video workflows.")
    check = await check_format(workflow_json, kind=kind)
    family = check.get("family") or workflow_name
    fp = workflow_fingerprint(workflow_json)
    client = LLMClient.for_role("writer", timeout=300.0)
    try:
        body = (await client.chat([
            {"role": "system", "content": prompt_text("workflows.skill_draft_system")},
            {"role": "user", "content": (
                f"Write prompt-writing rules for the video model family '{family}', used through a ComfyUI "
                f"workflow named '{workflow_name}' (node types: {', '.join(fp['node_types'])}; model files: "
                f"{', '.join(fp['model_files']) or 'none'}).\n"
                "Write in English with exactly these sections: '## Overview', '## When to Use', "
                "'## Prompt Structure' (separate guidance for text-to-video and image-to-video if the model "
                "supports both), '## Rules', '## Negative Prompt' (one ```text block), '## Examples' (two "
                "```text prompts). Only state what you are confident is true for this model and say so where "
                "unsure. Always include these rules: no dialogue or quoted speech in the prompt, describe "
                "characters by appearance, one action per clip. Do not add a title or front matter."
            )},
        ], temperature=0.4)).strip()
    finally:
        await client.close()
    body = re.sub(r"^```\w*\n|\n```$", "", body).strip()
    if len(body) < 200 or "## " not in body:
        raise ValueError("The model returned no usable skill text.")
    name = f"{re.sub(r'-video$', '', _slug(family))}-video-prompt"
    root = skills_root()
    candidate, n = name, 2
    while (root / candidate).exists():
        candidate, n = f"{name}-{n}", n + 1
    text = (
        "---\n"
        f"name: {candidate}\n"
        f'description: "Use when writing video prompts for {family} workflows (LLM draft, review before use)."\n'
        "version: 0.1.0\n"
        "metadata:\n"
        "  author: drafted by the Calliope story model -- review before use\n"
        f"  tags: [video, prompt-engineering, draft, {_slug(family)}]\n"
        "---\n\n"
        f"> Draft generated {dt.date.today().isoformat()} for workflow \"{workflow_name}\". "
        "Check every rule before relying on it.\n\n"
        f"# {family} Video Prompts\n\n{body}\n"
    )
    (root / candidate).mkdir(parents=True)
    (root / candidate / "SKILL.md").write_text(text, encoding="utf-8")
    return {"name": candidate, "family": family, "path": f"{candidate}/SKILL.md"}
