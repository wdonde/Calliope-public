"""Condense a long source text (novel) into a pitch the built-in generators accept.

The built-in generators refuse an ``idea`` above ``LONG_IDEA_CHARS``
(``idea_guard.py``) because they put it into a single prompt. This module
closes that gap the other way round: it reads the novel in chunks with the
"story" role's model, then writes one chronological summary short enough to
prompt with. The novel itself is kept in ``projects.source_text``.

The prompt templates live in the ``novel-condense`` skill (Settings -> Agent
skills) so they can be edited without touching code; a missing or broken
section falls back to the built-in template below.
"""
from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable
from string import Template
from typing import Any

from calliope.prompt_library import text as prompt_text
from calliope.agent.idea_guard import LONG_IDEA_CHARS
from calliope.agent.llm import LLMClient, extract_json
from calliope.agent.prompts import guess_language
from calliope.agent.skills_store import ensure_builtin_skills, read_skill_file

logger = logging.getLogger(__name__)

CHUNK_CHARS = 6000
# More parts than this get merged in groups before Draft Storyline sees them,
# so a very long novel still fits the story model's prompt.
MAX_OUTLINE_PARTS = 20
# Leaves room under LONG_IDEA_CHARS so a user can still add a line by hand.
TARGET_CHARS = 1600
DURATIONS = ("30 seconds", "1 minute", "2 minutes", "5 minutes", "10 minutes")
# What the model may pick: longer films mean hours of rendering per minute, so
# 5 and 10 minutes stay a deliberate choice in the Story settings.
AUTO_DURATIONS = DURATIONS[:3]

Progress = Callable[[str], Awaitable[None]]

SKILL = "novel-condense"
JSON_ONLY = "You answer with a single JSON object and nothing else."
# Built-in templates; the skill file ships the same text as its starting point.
DEFAULT_TEMPLATES = {
    "part-summary": (
        "Summarize part $part of $parts of a story in 4-6 sentences, written in $lang. "
        "Name the main characters with their appearance, the locations and the key events.\n"
        'Answer: {"summary": "..."}\n\nPART:\n$text'
    ),
    "project-fields": (
        "Below are summaries of every part of a story, in order. Fill in the fields for a short-film project:\n"
        "- title: the story's title, in $lang\n"
        '- genre: 1-3 genres in English, e.g. "Sci-Fi, Action"\n'
        '- tone: mood and visual style in a few English words, e.g. "Cinematic, dark, atmospheric"\n'
        "- target_duration: exactly one of $durations -- this becomes a SHORT video, even for a long novel.\n"
        "- idea: a summary of the WHOLE plot in $lang, $min_chars to $max_chars characters, strictly "
        "chronological: every part gets at least one sentence. Exciting events (conflict, danger, turning "
        "points, surprises) take priority over explanations. Name the main and important side characters "
        "with a short appearance, and the locations. Render the ending exactly, including its last twist, "
        "hint or open question. No generic closing sentences.\n"
        'Answer: {"title": "...", "genre": "...", "tone": "...", "target_duration": "...", "idea": "..."}\n\n'
        "SUMMARIES:\n$text"
    ),
    "merge-parts": (
        "Merge these consecutive part summaries of a story into ONE summary of 5-7 sentences, written in "
        "$lang. Keep every named character, location and turning point, in order.\n"
        'Answer: {"summary": "..."}\n\nSUMMARIES:\n$text'
    ),
    "shorten": (
        "Rewrite the following text shorter: at most $max_chars characters, same order and same language, "
        "keep every character and turning point, drop only details.\n"
        'Answer: {"idea": "<the complete shortened text>"}\n\nTEXT:\n$text'
    ),
}
_SECTION_RE = re.compile(r"^##\s+([\w-]+)\s*\n+```[\w-]*\n(.*?)\n```", re.MULTILINE | re.DOTALL)


def load_templates() -> dict[str, Template]:
    """Templates from the ``novel-condense`` skill, built-ins where it has none.

    A section only counts when it still contains ``$text`` -- without it the
    model would never see the novel, which is a broken edit, not a choice.
    """
    templates = dict(DEFAULT_TEMPLATES)
    try:
        ensure_builtin_skills()
        skill = read_skill_file(SKILL)
    except OSError:
        skill = {"ok": False}
    if skill.get("ok"):
        for name, body in _SECTION_RE.findall(skill["content"]):
            if name in templates and "$text" in body:
                templates[name] = body.strip()
            elif name in templates:
                logger.warning("condense: skill section %r has no $text; using the built-in one", name)
    return {name: Template(body) for name, body in templates.items()}


def split_text(text: str, size: int = CHUNK_CHARS) -> list[str]:
    """Chunks of at most ~``size`` characters, cut at paragraph boundaries."""
    paras = [p.strip() for p in re.split(r"\n\s*\n|\r?\n(?=\S)", text) if p.strip()]
    chunks: list[str] = []
    cur = ""
    for p in paras:
        if cur and len(cur) + len(p) > size:
            chunks.append(cur)
            cur = ""
        cur += p + "\n\n"
    if cur.strip():
        chunks.append(cur)
    return chunks


async def _ask(client: LLMClient, prompt: str) -> dict[str, Any]:
    messages = [
        {"role": "system", "content": prompt_text("common.json_only")},
        {"role": "user", "content": prompt},
    ]
    try:
        return extract_json(await client.chat(messages, temperature=0.4))
    except ValueError as exc:
        logger.warning("condense: unusable reply (%s); retrying with json_object mode", exc)
        return extract_json(
            await client.chat(messages, temperature=0.4, response_format={"type": "json_object"})
        )


async def condense_source(
    text: str, *, language: str | None = None, progress: Progress | None = None
) -> dict[str, str]:
    """Return ``{title, genre, tone, target_duration, idea, outline}`` for ``text``.

    ``outline`` is the novel part by part (at most ``MAX_OUTLINE_PARTS``
    entries), kept for Draft Storyline.

    ``idea`` is written in ``language``, by default the source text's own
    language, and is guaranteed to be at most ``LONG_IDEA_CHARS`` characters.
    """
    tpl = load_templates()
    common = {
        # Named explicitly: "the source's language" alone gets English back from small models.
        "lang": language or guess_language(text[:20000]) or "the same language as the source text",
        "durations": ", ".join(AUTO_DURATIONS),
        "min_chars": TARGET_CHARS - 600,
        "max_chars": TARGET_CHARS,
    }
    client = LLMClient.for_role("writer", timeout=600.0)
    try:
        chunks = split_text(text)
        summaries: list[str] = []
        for i, chunk in enumerate(chunks, 1):
            if progress:
                await progress(f"Reading part {i}/{len(chunks)} of the source text...")
            out = await _ask(client, tpl["part-summary"].safe_substitute(
                common, part=i, parts=len(chunks), text=chunk
            ))
            summaries.append(str(out.get("summary") or "").strip())

        if progress:
            await progress("Writing the condensed story idea...")
        numbered = "\n".join(f"{i}. {s}" for i, s in enumerate(summaries, 1))
        fields = await _ask(client, tpl["project-fields"].safe_substitute(common, text=numbered))
        idea = str(fields.get("idea") or "").strip()
        for _ in range(2):
            if len(idea) <= TARGET_CHARS + 200:
                break
            if progress:
                await progress(f"Shortening the idea ({len(idea)} characters)...")
            short = str((await _ask(client, tpl["shorten"].safe_substitute(
                common, max_chars=TARGET_CHARS - 200, text=idea
            ))).get("idea") or "").strip()
            if len(short) < 300:  # a comment instead of the text -- keep the longer one
                break
            idea = short
        if len(idea) > LONG_IDEA_CHARS:
            idea = idea[: LONG_IDEA_CHARS - 50].rsplit(". ", 1)[0] + "."
        if not idea:
            raise ValueError("The model returned no summary -- check the Story model in Settings.")

        outline = summaries
        if len(outline) > MAX_OUTLINE_PARTS:
            size = -(-len(outline) // MAX_OUTLINE_PARTS)  # ceil
            merged = []
            for g in range(0, len(outline), size):
                if progress:
                    await progress(f"Condensing the outline ({g // size + 1}/{-(-len(outline) // size)})...")
                group = "\n".join(f"- {s}" for s in outline[g:g + size])
                out = await _ask(client, tpl["merge-parts"].safe_substitute(common, text=group))
                merged.append(str(out.get("summary") or "").strip() or group)
            outline = merged

        duration = str(fields.get("target_duration") or "").strip()
        return {
            "title": str(fields.get("title") or "").strip(),
            "genre": str(fields.get("genre") or "").strip(),
            "tone": str(fields.get("tone") or "").strip(),
            "target_duration": duration if duration in AUTO_DURATIONS else "1 minute",
            "idea": idea,
            "outline": outline,
        }
    finally:
        await client.close()
