"""Tags and item categories for project assets (characters, locations, items).

A tag is a short label, either plain ("villain") or "key:value"
("alias:Ripper Eddy", "faction:Union Guard", "type:exterior"). Keys are
lower-case English; values keep the story's spelling. ``alias:`` tags also
help comic panels recognise a character the script calls by a nickname
(agent/comic.py). Draft Storyline asks the model for tags; for older
projects :func:`suggest_tags` fills them in afterwards.
"""
from __future__ import annotations

import json
import re
from typing import Any

from calliope.prompt_library import text as prompt_text
from calliope.activity import tracked
from calliope.agent.llm import generate_structured
from calliope.config import settings
from calliope.db import get_db, row_to_dict

TAGS_SYSTEM = "You label story assets so they are easy to find. Respond with a single valid JSON object."
ITEM_CATEGORIES = ("weapon", "vehicle", "device", "furniture", "clothing", "tool", "creature", "prop")
MAX_TAGS = 20
ALIAS_KEYS = ("alias", "name", "nickname")
# Option lists from the prompt schemas that models sometimes echo back as a value.
# ("a|b" is otherwise a real tag: several values of one key, e.g. "chest:large|full bust".)
SCHEMA_PLACEHOLDERS = {
    "interior|exterior|landscape|vehicle interior",
    "weapon|vehicle|device|furniture|clothing|tool|creature|prop",
    "protagonist|antagonist|supporting",
}


def normalize_tags(tags: Any) -> list[str]:
    """Clean, de-duplicated tags; "Key : Value" becomes "key:Value"."""
    if isinstance(tags, str):
        tags = re.split(r"[,\n]", tags)
    out: list[str] = []
    seen: set[str] = set()
    for raw in tags or []:
        if not isinstance(raw, str):
            continue
        tag = " ".join(raw.split()).strip(" ,;")
        if ":" in tag:
            key, _, value = tag.partition(":")
            key, value = key.strip().lower(), value.strip()
            if not key or not value or value.lower() in SCHEMA_PLACEHOLDERS \
                    or value.lower().startswith(("other names", "every other", "group or")):
                continue  # empty or a schema placeholder echoed back
            tag = f"{key}:{value}"
        if tag and tag.lower() not in seen:
            seen.add(tag.lower())
            out.append(tag[:80])
    return out[:MAX_TAGS]


def tags_json(tags: Any, name: str | None = None) -> str:
    """Stored form; with ``name``, an alias that only repeats the name is dropped."""
    out = normalize_tags(tags)
    if name:
        out = [t for t in out if t.lower() != f"alias:{name.strip().lower()}"]
    return json.dumps(out, ensure_ascii=False)


def merge_tags(current: list[str], suggested: list[str]) -> list[str]:
    """Add suggestions only for keys the asset has no tag for yet (aliases may repeat)."""
    def key(tag: str) -> str:
        return tag.split(":", 1)[0].lower() if ":" in tag else tag.lower()

    have = {key(t) for t in current}
    return current + [t for t in normalize_tags(suggested) if key(t) == "alias" or key(t) not in have]


def name_tags(name: str) -> list[str]:
    """Aliases readable from the name itself: a quoted nickname ('Ripper Eddy')."""
    return [f"alias:{m.strip()}" for m in re.findall(r"['\"„“‚‘]([^'\"„“‚‘]{2,40})['\"“”‘’]", name or "")]


def aliases(entity: dict[str, Any]) -> list[str]:
    """Every other name an asset goes by (from its alias/name/nickname tags)."""
    out = []
    for tag in entity.get("tags") or []:
        key, sep, value = tag.partition(":")
        if sep and key.lower() in ALIAS_KEYS and value.strip():
            out.append(value.strip())
    return out


def normalize_category(value: Any) -> str | None:
    text = str(value or "").strip().lower()
    if not text or "|" in text:
        return None
    return text[:40]


# ── suggestions for existing projects ──

_KINDS = (("characters", "appearance"), ("locations", "description"), ("items", "description"))


def _load(project_id: int) -> dict[str, list[dict[str, Any]]]:
    conn = get_db(settings.db_path)
    try:
        return {
            table: [row_to_dict(r) for r in conn.execute(f"SELECT * FROM {table} WHERE project_id = ?", (project_id,))]
            for table, _ in _KINDS
        }
    finally:
        conn.close()


@tracked("tags", "Suggesting asset tags")
async def suggest_tags(project_id: int) -> dict[str, int]:
    """Ask the model for tags (and item categories); existing tags are kept,
    suggestions only add. Returns how many assets changed per kind."""
    conn = get_db(settings.db_path)
    try:
        project = row_to_dict(conn.execute("SELECT title, idea FROM projects WHERE id = ?", (project_id,)).fetchone())
    finally:
        conn.close()
    assets = _load(project_id)
    listing = []
    for table, text_col in _KINDS:
        for e in assets[table]:
            listing.append(f"- {table[:-1]} id={e['id']} {e['name']}: {(e.get(text_col) or '')[:200]}"
                           + (f" [category: {e.get('category')}]" if table == "items" and e.get("category") else "")
                           + (f" [tags: {', '.join(e['tags'])}]" if e.get("tags") else ""))
    if not listing:
        return {table: 0 for table, _ in _KINDS}
    messages = [
        {"role": "system", "content": prompt_text("assets.tags_system")},
        {"role": "user", "content": f"""Story: {project.get('title')} -- {(project.get('idea') or '')[:1500]}

Assets:
{chr(10).join(listing)}

For every asset give 1-5 short tags as "key:value" with English keys:
- characters: alias (each other name or nickname the story uses), faction, role (protagonist, villain, ...)
- locations: type (interior, exterior, landscape, vehicle interior), region or world, alias
- items: alias, owner (character name), material or era if it matters
Values keep the story's spelling. Also give each item one category from: {", ".join(ITEM_CATEGORIES)}.

Respond ONLY with JSON:
{{"characters": [{{"id": 1, "tags": ["alias:..."]}}],
  "locations": [{{"id": 1, "tags": ["type:exterior"]}}],
  "items": [{{"id": 1, "category": "weapon", "tags": ["owner:..."]}}]}}"""},
    ]
    result = await generate_structured(messages, temperature=0.3, role="writer")
    changed = {table: 0 for table, _ in _KINDS}
    conn = get_db(settings.db_path)
    try:
        for table, _ in _KINDS:
            by_id = {e["id"]: e for e in assets[table]}
            for s in result.get(table) or []:
                e = by_id.get(s.get("id")) if isinstance(s, dict) else None
                if not e:
                    continue
                if table == "characters" and e.get("appearance", "").lstrip().startswith("CHARACTER INFORMATION"):
                    continue  # tags come from the character's sheet
                tags = json.loads(tags_json(merge_tags((e.get("tags") or []) + name_tags(e["name"]),
                                                       list(s.get("tags") or [])), e["name"]))
                category = e.get("category") if table == "items" else None
                if table == "items" and not category:
                    category = normalize_category(s.get("category"))
                if tags != (e.get("tags") or []) or (table == "items" and category != e.get("category")):
                    if table == "items":
                        conn.execute("UPDATE items SET tags = ?, category = ? WHERE id = ?",
                                     (json.dumps(tags, ensure_ascii=False), category, e["id"]))
                    else:
                        conn.execute(f"UPDATE {table} SET tags = ? WHERE id = ?",
                                     (json.dumps(tags, ensure_ascii=False), e["id"]))
                    changed[table] += 1
        conn.commit()
    finally:
        conn.close()
    return changed
