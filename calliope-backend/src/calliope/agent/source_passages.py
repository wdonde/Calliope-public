"""Original novel passages for the script stage.

Draft Storyline spreads beats over the novel's parts in order, so scene k of
S sits in roughly the same place in the novel as part k*P/S. The script
generator gets that stretch of the original text -- never the whole novel --
so dialogue and staging come from the book instead of being invented.
"""
from __future__ import annotations

import math
from typing import Any

from calliope.agent.condense import MAX_OUTLINE_PARTS, split_text
from calliope.agent.prompts import guess_language
from calliope.config import settings

# Upper bound per script call: two parts of ~6,000 characters.
MAX_PASSAGE_CHARS = 12000


def novel_parts(project: dict[str, Any]) -> list[str]:
    """The stored novel cut the way condense_source cut it (merged groups included).

    Derived from the text alone, never from the outline's length: the user may
    add or delete outline parts, which must not shift scenes onto other text.
    """
    text = project.get("source_text") or ""
    if not text.strip():
        return []
    chunks = split_text(text)
    if len(chunks) <= MAX_OUTLINE_PARTS:
        return chunks
    size = math.ceil(len(chunks) / MAX_OUTLINE_PARTS)
    return ["".join(chunks[i:i + size]) for i in range(0, len(chunks), size)]


# Room kept for the model's answer (a few scenes of JSON).
ANSWER_TOKENS = 4500


def passage_budget(prompt_tokens: int) -> int:
    """Characters of original text that still fit next to a prompt of
    ``prompt_tokens`` (as Calliope estimates it) and the answer."""
    spare = settings.context_window_tokens() - prompt_tokens - ANSWER_TOKENS
    cpt = max(float(settings.llm_chars_per_token or 1.6), 0.5)
    return max(0, min(MAX_PASSAGE_CHARS, int(spare * cpt)))


def passage_for_scenes(
    project: dict[str, Any], first: int, last: int, total: int, *, prompt_tokens: int = 0
) -> str | None:
    """Original text for scenes first..last of total, as a prompt block."""
    parts = novel_parts(project)
    budget = passage_budget(prompt_tokens)
    if not parts or budget < 1500 or total < 1:
        return None
    lo = min(len(parts) - 1, int((first - 1) / total * len(parts)))
    hi = min(len(parts) - 1, max(lo, math.ceil(last / total * len(parts)) - 1))
    text, used = [], 0
    for i in range(lo, hi + 1):
        piece = parts[i].strip()
        room = budget - used
        if room < 500:
            break
        text.append(f"[Part {i + 1}]\n{piece[:room]}")
        used += min(len(piece), room)
    label = f"part {lo + 1}" if lo == hi else f"parts {lo + 1}-{hi + 1}"
    lang = guess_language(project.get("source_text") or "")
    language_rule = (
        f"LANGUAGE: write heading place names, action and dialogue in {lang}; never translate the novel's "
        "dialogue. " if lang and lang != "English" else ""
    )
    return (
        f"=== ORIGINAL NOVEL TEXT for these scenes ({label} of {len(parts)}) ===\n"
        + "\n\n".join(text)
        + "\n=== END OF ORIGINAL TEXT ===\n"
        "Use it: stage these scenes from this text. Take dialogue lines verbatim from it (in the novel's "
        "language) wherever the beat has one, and give each line to the character who says it in the novel; "
        "keep names, places and details as written. Do not add events the beats do not cover. Every scene "
        "shows a different moment: never repeat a scene or a dialogue line already used in the scenes "
        "written before. "
        + language_rule
    )
