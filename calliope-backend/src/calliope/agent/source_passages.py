"""Original novel passages for the script stage.

Draft Storyline spreads beats over the novel's parts in order, so scene k of
S sits in roughly the same place in the novel as part k*P/S. The script
generator gets that stretch of the original text -- never the whole novel --
so dialogue and staging come from the book instead of being invented.
"""
from __future__ import annotations

import json
import math
from typing import Any

from calliope.agent.condense import split_text
from calliope.config import settings

# Upper bound per script call: two parts of ~6,000 characters.
MAX_PASSAGE_CHARS = 12000


def novel_parts(project: dict[str, Any]) -> list[str]:
    """The stored novel cut the way its outline was made (merged groups included)."""
    text = project.get("source_text") or ""
    if not text.strip():
        return []
    chunks = split_text(text)
    try:
        outline = json.loads(project.get("source_summaries") or "[]")
    except (TypeError, ValueError):
        outline = []
    n = len(outline) if isinstance(outline, list) and outline else len(chunks)
    if len(chunks) <= n:
        return chunks
    size = math.ceil(len(chunks) / n)
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
    return (
        f"=== ORIGINAL NOVEL TEXT for these scenes ({label} of {len(parts)}) ===\n"
        + "\n\n".join(text)
        + "\n=== END OF ORIGINAL TEXT ===\n"
        "Use it: stage these scenes from this text. Take dialogue lines verbatim from it (in the novel's "
        "language) wherever the beat has one; keep names, places and details as written. Do not add events "
        "the beats do not cover."
    )
