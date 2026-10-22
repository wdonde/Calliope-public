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
    "Alias": "alias",
    "Role": "role",
    "Gender": "gender",
    "Age": "age",
    "Species": "species",
    "Height": "height",
    "Build": "build",
    "Muscles": "muscles",
    "Chest": "chest",
    "Waist and hips": "hips",
    "Arms": "arms",
    "Legs": "legs",
    "Skin": "skin",
    "Face": "face",
    "Eyes": "eyes",
    "Hair": "hair",
    "Distinctive features": "mark",
}
# Sheet lines that say who the character is in the story, not how they look:
# kept in the sheet (and its tags), left out of image prompts.
STORY_ONLY = ("Alias", "Role")


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


def tag_values(value: str, limit: int = 5) -> list[str]:
    """A sheet value as separate, filterable tag values: its comma-separated
    parts, lower case ('large, full bust' -> ['large', 'full bust'])."""
    first = re.split(r"[;.]", value)[0]
    parts = [p.strip().lower() for p in first.split(",") if p.strip()]
    return [p if len(p) <= 40 else p[:40].rsplit(" ", 1)[0] for p in parts][:limit]


def tag_matches(tag: str, wanted: str) -> bool:
    """Whether a tag fits a filter: 'chest:large' matches 'chest:large|full bust'."""
    key, _, values = tag.lower().partition(":")
    wkey, _, wvalue = wanted.lower().partition(":")
    if not wvalue:
        return tag.lower() == wanted.lower()
    return key == wkey and (values == wvalue or wvalue in values.split("|"))


def identity_tags(identity: str | None) -> list[str]:
    """Tags read from the CHARACTER INFORMATION block only (one alias tag per alias)."""
    tags = []
    known = fields(identity)
    for label, key in IDENTITY_TAGS.items():
        value = known.get(label, "")
        if not value or value.lower() in ("none", "n/a", "-", "human", "unknown"):
            continue
        if label == "Alias":
            tags += [f"alias:{a.strip()}" for a in re.split(r"[,;/]", value) if a.strip()]
        else:
            tags.append(f"{key}:{'|'.join(tag_values(value))}")
    return tags


def with_story_fields(identity: str, aliases: list[str], role: str | None) -> str:
    """Add Alias/Role lines to a sheet that lacks them (older sheets kept these as tags)."""
    if IDENTITY not in sections(identity):
        return identity
    have = fields(identity)
    lines = identity.splitlines()
    at = next((i + 1 for i, line in enumerate(lines) if line.strip().lower().startswith("name:")), 1)
    extra = []
    if aliases and "Alias" not in have:
        extra.append(f"Alias: {', '.join(aliases)}")
    if role and "Role" not in have:
        extra.append(f"Role: {role.strip().lower()}")
    return "\n".join(lines[:at] + extra + lines[at:])


def for_image(appearance: str | None) -> str:
    """The appearance without its story-only lines (alias, role) -- for image prompts."""
    return "\n".join(line for line in (appearance or "").splitlines()
                     if line.split(":", 1)[0].strip() not in STORY_ONLY)


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


def is_sheet(appearance: str | None) -> bool:
    return (appearance or "").lstrip().startswith(IDENTITY)


def complete_sheet(appearance: str, name: str, role: str | None = None, aliases: list[str] | None = None) -> str:
    """A sheet with Alias (from a quoted nickname in the name, or given) and Role filled in."""
    from calliope.agent.asset_tags import name_tags

    found = [t.split(":", 1)[1] for t in name_tags(name)] + list(aliases or [])
    return with_story_fields(appearance, list(dict.fromkeys(found)), role) if is_sheet(appearance) else appearance


def character_tags(appearance: str | None, tags: list[str] | None = None) -> list[str] | None:
    """A character's tags: read from its CHARACTER INFORMATION (plus its library
    link). None when the appearance is no sheet -- then its tags stay as they are."""
    if not is_sheet(appearance):
        return None
    links = [t for t in tags or [] if t.startswith("library:")]
    return identity_tags(identity_text(appearance)) + links


def story_fields(appearance: str | None) -> tuple[str, str]:
    """(alias, role) as written in a sheet; empty when missing or 'none'."""
    known = fields(identity_text(appearance))
    def clean(v: str) -> str:
        return "" if v.strip().lower() in ("none", "-", "n/a", "unknown") else v.strip()
    return clean(known.get("Alias", "")), clean(known.get("Role", ""))


def set_story_fields(appearance: str, alias: str, role: str) -> str:
    """The sheet with its Alias/Role lines replaced by these values (removed when empty)."""
    if not is_sheet(appearance):
        return appearance
    lines = [line for line in appearance.splitlines()
             if line.split(":", 1)[0].strip() not in STORY_ONLY]
    at = next((i + 1 for i, line in enumerate(lines) if line.strip().lower().startswith("name:")), 1)
    extra = ([f"Alias: {alias.strip()}"] if alias.strip() else []) + ([f"Role: {role.strip()}"] if role.strip() else [])
    return "\n".join(lines[:at] + extra + lines[at:])


def sync_story_fields(appearance: str | None, alias: str | None, role: str | None) -> tuple[str, str, str]:
    """Alias and Role are kept in their own fields and linked into the sheet.
    A filled field wins over whatever a new sheet says; an empty field takes
    the sheet's value (newly suggested). Returns (appearance, alias, role)."""
    text = appearance or ""
    sheet_alias, sheet_role = story_fields(text)
    alias = (alias or "").strip() or sheet_alias
    role = (role or "").strip() or sheet_role
    return set_story_fields(text, alias, role), alias, role
