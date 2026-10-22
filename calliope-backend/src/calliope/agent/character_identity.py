"""A character's identity, apart from what a project dresses it in.

The library keeps only the CHARACTER INFORMATION part of a character sheet
(body, face, hair, marks -- agent/asset_create.py); outfit, personality and
the role in the story belong to each project. Identity tags ("gender:male",
"age:45", "hair:grey military cut", "mark:scar on left cheek") plus alias and
role tags let a project find the library character its own character is,
and :func:`apply_identity` puts that identity into the project character
while keeping the project's outfit.
"""
from __future__ import annotations

import re
from typing import Any

IDENTITY = "CHARACTER INFORMATION"
# Sheet label -> tag key; the values say who the character is, not what they wear.
IDENTITY_TAGS = {
    "Gender": "gender",
    "Age": "age",
    "Species": "species",
    "Height": "height",
    "Build": "build",
    "Skin": "skin",
    "Eyes": "eyes",
    "Hair": "hair",
    "Distinctive features": "mark",
}


def sections(text: str | None) -> dict[str, list[str]]:
    """A sheet split at its upper-case headings; prose comes back as one section ''."""
    out: dict[str, list[str]] = {}
    current = ""
    for raw in (text or "").splitlines():
        line = raw.strip()
        if line and line.isupper() and ":" not in line:
            current = line
            out.setdefault(current, [])
        elif line:
            out.setdefault(current, []).append(line)
    return out


def identity_text(appearance: str | None) -> str:
    """Only the CHARACTER INFORMATION block; prose (no sheet) is kept as it is."""
    parts = sections(appearance)
    if IDENTITY not in parts:
        return (appearance or "").strip()
    return "\n".join([IDENTITY, *parts[IDENTITY]])


def outfit_text(appearance: str | None) -> str:
    """Everything after CHARACTER INFORMATION (OUTFIT, ...) of a sheet."""
    parts = sections(appearance)
    blocks = ["\n".join([head, *lines]) for head, lines in parts.items() if head and head != IDENTITY]
    return "\n\n".join(blocks)


def apply_identity(appearance: str | None, identity: str) -> str:
    """The project's appearance with its identity replaced (its outfit kept)."""
    outfit = outfit_text(appearance)
    return f"{identity.strip()}\n\n{outfit}".strip() if outfit else identity.strip()


def fields(identity: str | None) -> dict[str, str]:
    out = {}
    for line in sections(identity).get(IDENTITY, []):
        label, sep, value = line.partition(":")
        if sep:
            out[label.strip()] = value.strip()
    return out


def _short(value: str, limit: int = 48) -> str:
    """Whole comma-separated phrases up to ``limit`` characters, lower case."""
    out = ""
    for part in re.split(r"[;.]", value)[0].split(","):
        part = part.strip()
        candidate = f"{out}, {part}" if out else part
        if out and len(candidate) > limit:
            break
        out = candidate
    return out.lower()


def identity_tags(identity: str | None) -> list[str]:
    tags = []
    for label, key in IDENTITY_TAGS.items():
        value = fields(identity).get(label, "")
        if value and value.lower() not in ("none", "n/a", "-", "human"):
            tags.append(f"{key}:{_short(value)}")
    return tags


def _age(value: str) -> int | None:
    m = re.search(r"\d+", value or "")
    return int(m.group()) if m else None


def _gender(text: str) -> str | None:
    t = (text or "").lower()
    if re.search(r"\b(female|woman|girl|she)\b", t):
        return "female"
    if re.search(r"\b(male|man|boy|he)\b", t):
        return "male"
    return None


def _names(name: str, tags: list[str]) -> set[str]:
    from calliope.agent.comic import NAME_FILLERS

    words = set()
    for n in [name, *(t.split(":", 1)[1] for t in tags if t.lower().startswith("alias:"))]:
        words |= {w for w in re.findall(r"[^\W\d_]+", n.lower()) if len(w) > 2 and w not in NAME_FILLERS}
    return words


def match(project_char: dict[str, Any], asset: dict[str, Any]) -> tuple[int, list[str]]:
    """How well a library character fits a project character, and why."""
    score, why = 0, []
    p_tags, a_tags = project_char.get("tags") or [], asset.get("tags") or []
    shared = _names(project_char["name"], p_tags) & _names(asset["name"], a_tags)
    if shared:
        score += 5
        why.append("name: " + ", ".join(sorted(shared)))
    a_role = next((t.split(":", 1)[1].lower() for t in a_tags if t.lower().startswith("role:")), None)
    if a_role and a_role == (project_char.get("role") or "").lower():
        score += 1
        why.append(f"role: {a_role}")
    a_fields = fields(asset.get("data", {}).get("appearance"))
    p_text = project_char.get("appearance") or ""
    pg, ag = _gender(fields(p_text).get("Gender") or p_text), _gender(a_fields.get("Gender", ""))
    if pg and ag:
        score += 1 if pg == ag else -4
        why.append(f"gender: {ag}" if pg == ag else "gender differs")
    pa, aa = _age(fields(p_text).get("Age") or project_char.get("age") or ""), _age(a_fields.get("Age", ""))
    if pa and aa:
        if abs(pa - aa) <= 10:
            score += 1
            why.append(f"age: {aa}")
        elif abs(pa - aa) > 20:
            score -= 2
    return score, why
