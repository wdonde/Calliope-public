"""Condense a long source text (novel) into a pitch the built-in generators accept.

The built-in generators refuse an ``idea`` above ``LONG_IDEA_CHARS``
(``idea_guard.py``) because they put it into a single prompt. This module
closes that gap the other way round: it reads the novel in chunks with the
"story" role's model, then writes one chronological summary short enough to
prompt with. The novel itself is kept in ``projects.source_text``.
"""
from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable
from typing import Any

from calliope.agent.idea_guard import LONG_IDEA_CHARS
from calliope.agent.llm import LLMClient, extract_json

logger = logging.getLogger(__name__)

CHUNK_CHARS = 6000
# Leaves room under LONG_IDEA_CHARS so a user can still add a line by hand.
TARGET_CHARS = 1600
DURATIONS = ("30 seconds", "1 minute", "2 minutes", "5 minutes", "10 minutes")
# What the model may pick: longer films mean hours of rendering per minute, so
# 5 and 10 minutes stay a deliberate choice in the Story settings.
AUTO_DURATIONS = DURATIONS[:3]

Progress = Callable[[str], Awaitable[None]]


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
        {"role": "system", "content": "You answer with a single JSON object and nothing else."},
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
    """Return ``{title, genre, tone, target_duration, idea}`` for ``text``.

    The prompts are English (models follow English instructions most reliably);
    ``idea`` is written in ``language``, by default the source text's own
    language. ``idea`` is guaranteed to be at most ``LONG_IDEA_CHARS`` characters.
    """
    lang = language or "the same language as the source text"
    client = LLMClient.for_role("story", timeout=600.0)
    try:
        chunks = split_text(text)
        summaries: list[str] = []
        for i, chunk in enumerate(chunks, 1):
            if progress:
                await progress(f"Reading part {i}/{len(chunks)} of the source text...")
            out = await _ask(client, (
                f"Summarize part {i} of {len(chunks)} of a story in 4-6 sentences, written in {lang}. "
                "Name the main characters with their appearance, the locations and the key events.\n"
                'Answer: {"summary": "..."}\n\nPART:\n' + chunk
            ))
            summaries.append(str(out.get("summary") or "").strip())

        if progress:
            await progress("Writing the condensed story idea...")
        numbered = "\n".join(f"{i}. {s}" for i, s in enumerate(summaries, 1))
        fields = await _ask(client, (
            "Below are summaries of every part of a story, in order. Fill in the fields for a short-film project:\n"
            f"- title: the story's title, in {lang}\n"
            '- genre: 1-3 genres in English, e.g. "Sci-Fi, Action"\n'
            '- tone: mood and visual style in a few English words, e.g. "Cinematic, dark, atmospheric"\n'
            f"- target_duration: exactly one of {', '.join(AUTO_DURATIONS)} -- this becomes a SHORT video, "
            "even for a long novel.\n"
            f"- idea: a summary of the WHOLE plot in {lang}, {TARGET_CHARS - 600} to {TARGET_CHARS} "
            "characters, strictly chronological: every part gets at least one sentence. Exciting events "
            "(conflict, danger, turning points, surprises) take priority over explanations. Name the main and "
            "important side characters with a short appearance, and the locations. Render the ending exactly, "
            "including its last twist, hint or open question. No generic closing sentences.\n"
            'Answer: {"title": "...", "genre": "...", "tone": "...", "target_duration": "...", "idea": "..."}\n\n'
            "SUMMARIES:\n" + numbered
        ))
        idea = str(fields.get("idea") or "").strip()
        for _ in range(2):
            if len(idea) <= TARGET_CHARS + 200:
                break
            if progress:
                await progress(f"Shortening the idea ({len(idea)} characters)...")
            short = str((await _ask(client, (
                f"Rewrite the following text shorter: at most {TARGET_CHARS - 200} characters, same order and "
                "same language, keep every character and turning point, drop only details. "
                'Answer: {"idea": "<the complete shortened text>"}\n\nTEXT:\n' + idea
            ))).get("idea") or "").strip()
            if len(short) < 300:  # a comment instead of the text -- keep the longer one
                break
            idea = short
        if len(idea) > LONG_IDEA_CHARS:
            idea = idea[: LONG_IDEA_CHARS - 50].rsplit(". ", 1)[0] + "."
        if not idea:
            raise ValueError("The model returned no summary -- check the Story model in Settings.")

        duration = str(fields.get("target_duration") or "").strip()
        return {
            "title": str(fields.get("title") or "").strip(),
            "genre": str(fields.get("genre") or "").strip(),
            "tone": str(fields.get("tone") or "").strip(),
            "target_duration": duration if duration in AUTO_DURATIONS else "1 minute",
            "idea": idea,
        }
    finally:
        await client.close()
