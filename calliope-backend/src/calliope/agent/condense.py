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
    text: str, *, language: str = "Deutsch", progress: Progress | None = None
) -> dict[str, str]:
    """Return ``{title, genre, tone, target_duration, idea}`` for ``text``.

    ``idea`` is guaranteed to be at most ``LONG_IDEA_CHARS`` characters.
    """
    client = LLMClient.for_role("story", timeout=600.0)
    try:
        chunks = split_text(text)
        summaries: list[str] = []
        for i, chunk in enumerate(chunks, 1):
            if progress:
                await progress(f"Reading part {i}/{len(chunks)} of the source text...")
            out = await _ask(client, (
                f"Fasse Abschnitt {i} von {len(chunks)} einer Geschichte in 4-6 Sätzen auf {language} zusammen. "
                "Nenne Hauptfiguren mit Namen und Aussehen, Schauplätze und die wichtigsten Ereignisse.\n"
                'Antwort: {"summary": "..."}\n\nABSCHNITT:\n' + chunk
            ))
            summaries.append(str(out.get("summary") or "").strip())

        if progress:
            await progress("Writing the condensed story idea...")
        numbered = "\n".join(f"{i}. {s}" for i, s in enumerate(summaries, 1))
        fields = await _ask(client, (
            "Hier sind Zusammenfassungen aller Abschnitte einer Geschichte. Fülle die Felder für ein Kurzfilm-Projekt:\n"
            "- title: Titel der Geschichte\n"
            '- genre: 1-3 Genres auf Englisch, z. B. "Sci-Fi, Action"\n'
            '- tone: Stimmung und Bildstil auf Englisch in wenigen Worten, z. B. "Cinematic, dark, atmospheric"\n'
            f"- target_duration: genau einer von {', '.join(AUTO_DURATIONS)} -- es wird ein KURZES Video, "
            "auch bei langen Romanen.\n"
            f"- idea: Kurzfassung der GANZEN Handlung auf {language}, {TARGET_CHARS - 600} bis {TARGET_CHARS} "
            "Zeichen, streng chronologisch: jeder Abschnitt bekommt mindestens einen Satz. Spannende Ereignisse "
            "(Konflikte, Gefahr, Wendepunkte, Überraschungen) haben Vorrang vor Erklärungen. Hauptfiguren und "
            "wichtige Nebenfiguren mit Namen und kurzem Aussehen, Schauplätze. Das Ende genau wiedergeben, "
            "einschließlich letzter Wendung, Andeutung oder offener Frage. Keine allgemeinen Schlusssätze.\n"
            'Antwort: {"title": "...", "genre": "...", "tone": "...", "target_duration": "...", "idea": "..."}\n\n'
            "ZUSAMMENFASSUNGEN:\n" + numbered
        ))
        idea = str(fields.get("idea") or "").strip()
        for _ in range(2):
            if len(idea) <= TARGET_CHARS + 200:
                break
            if progress:
                await progress(f"Shortening the idea ({len(idea)} characters)...")
            short = str((await _ask(client, (
                f"Schreibe den folgenden Text gekürzt neu: höchstens {TARGET_CHARS - 200} Zeichen, gleiche "
                "Reihenfolge, alle Figuren und Wendepunkte bleiben, nur Details fallen weg. "
                'Antwort: {"idea": "<der vollständige gekürzte Text>"}\n\nTEXT:\n' + idea
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
