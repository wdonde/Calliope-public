"""Create a character/location/item from a description and/or reference pictures.

The user's pictures are kept with the asset (``source_images``). A vision
model (the "assets" role) looks at them and writes the fixed look in English
-- the same shape Draft Storyline asks for -- plus tags; without pictures the
description alone is turned into that look. The sheet/reference image is then
rendered with the pictures as references: at most two at full size (see
:func:`pick_refs`) -- a color picture for the colors and the full-body view.
"""
from __future__ import annotations

import base64
import io
import json
import shutil
import time
from pathlib import Path
from typing import Any

from PIL import Image

from calliope.prompt_library import text as prompt_text
from calliope.agent.asset_tags import ITEM_CATEGORIES, name_tags, normalize_category, tags_json
from calliope.agent.character_identity import character_tags, complete_sheet, identity_tags, story_fields
from calliope.agent.llm import generate_structured
from calliope.agent.prompts import (
    character_sheet_prompt,
    item_reference_prompt,
    location_reference_prompt,
)
from calliope.comfyui.parser import parse_dynamic_inputs
from calliope.comfyui.smart_fill import smart_fill_inputs
from calliope.config import settings
from calliope.db import get_db, row_to_dict
from calliope.queue.manager import queue_manager

TABLES = {"character": "characters", "location": "locations", "item": "items"}
MAX_IMAGES = 6
# References per render unless the asset says otherwise: two pictures (color face +
# full body) measured as fast and as close as six (13 instead of 3 minutes).
DEFAULT_MAX_REFS = 2
LOOK_HINT = {
    "character": ("appearance", "the character sheet below"),
    "location": ("description", "visual description for image generation: place, architecture or landscape, "
                 "materials, light, mood (40-70 words)"),
    "item": ("description", "visual description for image generation: shape, size, material, colors, "
             "distinctive details (30-50 words)"),
}
GRAY_HINT = ("The pictures are black-and-white: they show shapes, faces and clothing, not colors. Take the colors "
             "from the notes; where the notes say nothing, choose fitting colors. Always write explicit colors "
             "(skin tone, eye color, hair color, every garment) so the look stays the same in color.")
MIXED_HINT = ("Some pictures are in COLOR, the others black-and-white. Take every color (hair, eyes, skin, "
              "clothes) from the COLOR pictures -- they win over any guess; use the black-and-white ones for "
              "shapes, face, body and hairstyle. Write the colors explicitly.")


def context_block(context: str, has_pictures: bool) -> str:
    """The story and the look so far. With pictures, the pictures decide: an
    earlier text is only a draft (otherwise the model copies its old mistakes)."""
    if not context.strip():
        return ""
    if has_pictures:
        return ("Context (the story, and an earlier draft of the look that may be wrong -- whatever the pictures "
                f"show replaces it): {context[:2500]}")
    return f"Context (the story and what is known so far -- stay true to it): {context[:2500]}"


def color_hint(images: list[str]) -> str:
    gray = [is_grayscale(p) for p in images]
    if not images:
        return ""
    if all(gray):
        return GRAY_HINT
    return MIXED_HINT if any(gray) else ""


def pick_refs(images: list[str], limit: int = MAX_IMAGES) -> list[str]:
    """The asset's pictures in reference order, each at full size (a collage
    shrinks faces to nothing): image 1 the biggest color picture (it carries the
    colors), image 2 the tallest other one (usually the full body), then the
    rest, biggest first."""
    def area(p: str) -> int:
        with Image.open(p) as im:
            return im.width * im.height

    def tallness(p: str) -> float:
        with Image.open(p) as im:
            return im.height / max(1, im.width)

    color = sorted((p for p in images if not is_grayscale(p)), key=area, reverse=True)
    first = color[0] if color else max(images, key=area)
    rest = [p for p in images if p != first]
    second = [max(rest, key=tallness)] if rest else []
    others = sorted((p for p in rest if p not in second), key=area, reverse=True)
    return ([first] + second + others)[:limit]


def max_refs(entity: dict[str, Any]) -> int:
    """How many of its pictures an asset sends as references (its own setting, 1..6)."""
    try:
        return max(1, min(MAX_IMAGES, int(entity.get("max_refs") or DEFAULT_MAX_REFS)))
    except (TypeError, ValueError):
        return DEFAULT_MAX_REFS


DESIGNER_SYSTEM = "You are a character and production designer. Respond with a single valid JSON object."
EXTRACT_SYSTEM = "You extract facts into JSON. Respond with a single valid JSON object."
OUTFIT_RULES = """Fill the outfit fields with what the description says about clothing, gear and carried items, in English.
Copy each value word for word as a whole phrase from the description, with every adjective it has
(color, material, condition) -- e.g. "a red wool scarf with frayed ends", never just "scarf".
Leave a field empty when the description does not mention it. Do not invent anything."""
PICTURES_SYSTEM = "You describe reference pictures briefly and exactly. Respond with a single valid JSON object."
PICTURES_RULES = """These {count} pictures are references of the {what} "{name}".
For each picture write which view of the {what} it shows in 3-8 English words: framing and angle only
(e.g. "face close-up, front view", "full body, side view"). No facial expression, no emotion, no sweat or tears,
no props or accessories held for the moment.
Then name the art style the pictures are drawn in (medium, line work, shading; 3-10 words, e.g. "Japanese manga, clean ink lines, screentone shading")."""
PICTURES_CLOTHES = ("Then the main outfit in 5-15 words: the clothes the person wears in most pictures. "
                    "Leave out underwear, straps, sunglasses and anything only seen once.")
SHEET_LAYOUT = ("Layout: front full body, side full body, back full body and a head close-up, same person in every "
                "view, neutral standing pose, neutral expression, plain light background, no text, no other people.")


def chosen_refs(entity: dict[str, Any]) -> list[str] | None:
    """The pictures the user picked as references, in picture order (image 1, 2, ...);
    None when nothing is picked (then Calliope chooses)."""
    images = entity.get("source_images") or []
    picked = [i for i in entity.get("ref_indices") or [] if isinstance(i, int) and 0 <= i < len(images)]
    if not picked:
        return None
    return [images[i] for i in sorted(set(picked)) if Path(images[i]).is_file()][:MAX_IMAGES]


def usable_refs(images: list[str], kind: str, limit: int = DEFAULT_MAX_REFS,
                chosen: list[str] | None = None, style: dict[str, Any] | None = None) -> tuple[list[str], int | None]:
    """(references, workflow): the user's chosen pictures, or up to ``limit``
    picked by Calliope; as many as an enabled workflow takes (fewer when none
    has that many image inputs); none -> text workflow."""
    refs = list(chosen) if chosen else (pick_refs(images, limit) if images else [])
    while refs:
        wf = _pick_workflow(len(refs), kind, style)
        if wf is not None:
            return refs, wf
        refs = refs[:-1]
    return [], _pick_workflow(0, kind, style)


TAG_KEYS = {
    "character": "alias (other names or nicknames), role (protagonist, villain, mentor, ...)",
    "location": "type (interior, exterior, landscape, vehicle interior), region (world or area), alias",
    "item": "alias, owner (the character it belongs to), material or era if it matters",
}
# The fixed look of a character, section by section: what every sheet and panel
# must keep the same. Style is left out on purpose -- it comes from the project,
# so the same character can be drawn as manga, webtoon or 3D.
CHARACTER_SHEET = (
    ("CHARACTER INFORMATION", (
        ("Alias", "other names or nicknames, comma separated, or none"),
        ("Role", "role in the story: protagonist, antagonist, mentor, sidekick, ... or none"),
        ("Age", "age in years or a range"),
        ("Gender", "gender"),
        ("Species", "only if not human, else leave empty"),
        ("Height", "height in cm"),
        ("Build", "overall body type and proportions"),
        ("Muscles", "muscle definition and tone"),
        ("Chest", "chest or bust size and shape, in neutral terms"),
        ("Waist and hips", "waist and hip proportions"),
        ("Arms", "arm length, thickness and musculature"),
        ("Legs", "leg length, thickness and shape"),
        ("Skin", "skin tone and texture"),
        ("Face", "face shape, jawline, nose, lips"),
        ("Eyes", "eye color and shape"),
        ("Hair", "color, length, hairstyle"),
        ("Distinctive features", "freckles, scars, tattoos, or none"),
    )),
    ("OUTFIT", (
        ("Headwear", "hat, helmet, hood, or none"),
        ("Outerwear", "jacket, vest, armor, coat, or none"),
        ("Top", "garment, fit, color, material"),
        ("Bottom", "garment, fit, color, material"),
        ("Footwear", "type, color, material"),
        ("Accessories", "specific accessories or none"),
        ("Signature items", "what the character always carries (weapon, bag, glasses), or none"),
        ("Color palette", "2-3 key colors of the whole look"),
    )),
)


def sheet_text(name: str, look: dict[str, Any], *, identity_only: bool = False, outfit_only: bool = False) -> str:
    """The character sheet as text (stored in characters.appearance)."""
    lines: list[str] = []
    wanted = CHARACTER_SHEET[:1] if identity_only else CHARACTER_SHEET[1:] if outfit_only else CHARACTER_SHEET
    for section, fields in wanted:
        if lines:
            lines.append("")
        lines.append(section)
        if section == "CHARACTER INFORMATION":
            lines.append(f"Name: {name}")
        for label, _ in fields:
            raw = look.get(label)
            value = (", ".join(str(v) for v in raw) if isinstance(raw, list) else str(raw or "")).strip()
            if label == "Height" and value.isdigit():
                value += " cm"
            empty = value.lower() in ("none", "-", "n/a", "unknown")
            if value and not ((label == "Species" and value.lower() == "human")
                              or (label in ("Species", "Alias", "Role") and empty)):
                lines.append(f"{label}: {value}")
    return "\n".join(lines)


def _data_url(path: str, size: int = 768) -> str | None:
    try:
        im = Image.open(path).convert("RGB")
    except OSError:
        return None
    im.thumbnail((size, size))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=85)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def is_grayscale(path: str) -> bool:
    """True for black-and-white pictures (manga pages, ink drawings, grey photos)."""
    try:
        im = Image.open(path).convert("RGB")
    except OSError:
        return False
    im.thumbnail((128, 128))
    sat = im.convert("HSV").getchannel("S")
    hist = sat.histogram()
    colored = sum(hist[40:])  # pixels with noticeable saturation
    return colored / max(1, sum(hist)) < 0.03


def keep_images(project_id: int, kind: str, paths: list[str]) -> list[str]:
    """Copy the user's pictures next to the project, so the upload area can be cleaned."""
    dest = Path(settings.assets_dir) / str(project_id) / "refs" / f"{kind}-{int(time.time() * 1000)}"
    out = []
    for p in [p for p in paths if p and Path(p).is_file()][:MAX_IMAGES]:
        dest.mkdir(parents=True, exist_ok=True)
        target = dest / Path(p).name
        shutil.copy2(p, target)
        out.append(str(target))
    return out


async def describe(kind: str, name: str, prompt: str, images: list[str], *, context: str = "",
                   identity_only: bool = False, outfit_only: bool = False) -> dict[str, Any]:
    """The asset's look (English), tags and -- for items -- category, from text and pictures.
    Characters get the filled-in CHARACTER_SHEET; missing notes are fine, the
    model then invents a fitting, specific look from the name and ``context``
    (the story it belongs to, the look written so far). ``identity_only`` asks
    for the CHARACTER INFORMATION part alone (library characters), ``outfit_only``
    for the OUTFIT part alone (a project character whose identity is linked)."""
    field, hint = LOOK_HINT[kind]
    category = f',\n  "category": "one of {", ".join(ITEM_CATEGORIES)} or your own word"' if kind == "item" else ""
    if kind == "character":
        wanted = CHARACTER_SHEET[:1] if identity_only else CHARACTER_SHEET[1:] if outfit_only else CHARACTER_SHEET
        sheet = "\n".join(f'    "{label}": "{h}",' for _, fields in wanted for label, h in fields)
        shape = f'  "look": {{\n{sheet.rstrip(",")}\n  }}'
        ask = ('- "look": fill every field of the character sheet, concrete and specific enough that the '
               "character is recognisable in every picture; invent fitting details the notes and pictures "
               "leave open.")
        if images:
            # Invented scars or freckles would be drawn into every sheet and panel.
            ask += (" With pictures, describe what they show; for Distinctive features name only marks that "
                    "are clearly visible in them or given in the notes, otherwise write none.")
    else:
        shape = f'  "{field}": "..."'
        ask = f'- "{field}": {hint}. Concrete and visual, no story events.'
    text = f"""Create a {kind} asset for a story.
Name: {name}
{context_block(context, bool(images))}
User notes (any language): {prompt or '(none)'}
{f'The {len(images)} attached picture(s) show this {kind}; describe what they show, and let the notes override them.' if images else ''}
{color_hint(images)}

Write in English:
{ask}
- "tags": 1-5 short "key:value" tags with English keys: {TAG_KEYS[kind]}.

Respond ONLY with JSON:
{{
{shape},
  "tags": ["alias:..."]{category}
}}"""
    parts: list[dict[str, Any]] = [{"type": "text", "text": text}]
    for i, p in enumerate(images, 1):
        url = _data_url(p)
        if url:
            kind_of = "black-and-white" if is_grayscale(p) else "COLOR"
            parts.append({"type": "text", "text": f"Picture {i} ({kind_of}):"})
            parts.append({"type": "image_url", "image_url": {"url": url}})
    messages = [
        {"role": "system", "content": prompt_text("assets.designer_system")},
        {"role": "user", "content": parts if len(parts) > 1 else text},
    ]
    try:
        result = await generate_structured(messages, temperature=0.4, role="assets" if images else "writer")
    except Exception:  # noqa: BLE001 -- the notes still make a usable asset
        result = {}
    if kind == "character":
        look = result.get("look") if isinstance(result.get("look"), dict) else {}
        value = (sheet_text(name, look, identity_only=identity_only, outfit_only=outfit_only) if look
                 else (result.get(field) or prompt or name))
    else:
        value = result.get(field) or prompt or name
    return {
        field: str(value).strip(),
        "tags": result.get("tags") or [],
        "category": normalize_category(result.get("category")) if kind == "item" else None,
    }


async def outfit_from_text(name: str, text: str) -> str:
    """The OUTFIT block read out of a prose appearance (what the text says, nothing invented)."""
    sheet = "\n".join(f'    "{label}": "{h}",' for label, h in CHARACTER_SHEET[1][1])
    messages = [
        {"role": "system", "content": prompt_text("assets.extract_system")},
        {"role": "user", "content": f"""Character: {name}
Description: {text}

{prompt_text("assets.outfit_rules")}

Respond ONLY with JSON:
{{
  "look": {{
{sheet.rstrip(",")}
  }}
}}"""},
    ]
    try:
        result = await generate_structured(messages, temperature=0.1, role="writer")
    except Exception:  # noqa: BLE001 -- keep the prose rather than lose it
        result = {}
    look = result.get("look") if isinstance(result.get("look"), dict) else {}
    outfit = sheet_text(name, look, outfit_only=True) if any(str(v).strip() for v in look.values()) else ""
    # Nothing usable came back: the prose itself stays, as project notes.
    return outfit if outfit.count("\n") >= 1 else f"OUTFIT\nNotes: {text.strip()}"


def project_context(project_id: int | None) -> str:
    """The story an asset belongs to: title, genre, tone and idea of its project."""
    if not project_id:
        return ""
    conn = get_db(settings.db_path)
    try:
        row = conn.execute("SELECT title, genre, tone, idea FROM projects WHERE id = ?", (project_id,)).fetchone()
    finally:
        conn.close()
    if not row:
        return ""
    p = row_to_dict(row)
    head = " / ".join(str(p[k]) for k in ("title", "genre", "tone") if p.get(k))
    return f"{head}. {(p.get('idea') or '')[:1500]}".strip()


def _pick_workflow(n_refs: int, kind: str = "character", style: dict[str, Any] | None = None) -> int | None:
    """The asset workflow for this many own pictures: the project's render style,
    else Settings > Workflows roles, else Auto."""
    from calliope.render_styles import workflow_for

    if style and style.get("workflows") and n_refs > 2:
        return None  # style workflows take up to two references
    if n_refs:
        return workflow_for(style, n_refs, f"asset_refs_{min(n_refs, MAX_IMAGES)}")
    return workflow_for(style, 0, {"character": "asset_sheet", "location": "asset_location", "item": "asset_item"}[kind])


COLOR_LINES = ("Hair", "Eyes", "Skin", "Color palette")


def _colors(entity: dict[str, Any]) -> list[str]:
    """Color facts from a character sheet ('Hair: dark blue, ...')."""
    from calliope.agent.character_identity import fields as sheet_fields, identity_text

    known = {**sheet_fields(identity_text(entity.get("appearance"))),
             **{line.split(":", 1)[0].strip(): line.split(":", 1)[1].strip()
                for line in (entity.get("appearance") or "").splitlines() if ":" in line}}
    return [f"{label.lower()} {known[label]}" for label in COLOR_LINES if known.get(label)]


def reference_prompt(kind: str, entity: dict[str, Any], n_refs: int, all_gray: bool, *, legacy: bool = False) -> str:
    """Short prompt when the asset's own pictures go in as references: the
    pictures lead (face, body, clothes, style); only colors come from the
    text, so nothing in it can contradict the pictures. The pictures are named
    'image 1' / 'image 2' in the order they go in, as in the FLUX.2 Klein
    multi-reference template. ``legacy`` gives the earlier wording, so prompts
    Calliope made before still count as automatic."""
    if legacy:
        pics = "the reference picture" if n_refs == 1 else "the reference pictures"
        roles = ("" if n_refs < 2 else
                 " The first picture shows the face and the colors, the second the full body.")
        where = f"the person in {pics}.{roles}"
        place, thing = f"the place in {pics}.", f"the object in {pics}."
    else:
        more = "" if n_refs < 3 else (" and image 3 (more views)" if n_refs == 3 else
                                      f" and images 3 to {n_refs} (more views)")
        where = ("the person in image 1." if n_refs == 1 else
                 f"the person in image 1 (face and colors), image 2 (full body){more}." if n_refs > 2 else
                 "the person in image 1 (face and colors) and image 2 (full body).")
        span = "image 1" if n_refs == 1 else "image 1 and image 2" if n_refs == 2 else f"images 1 to {n_refs}"
        place, thing = f"the place in {span}.", f"the object in {span}."
    refs = ("the reference pictures" if legacy else
            "image 1" if n_refs == 1 else "image 1 and image 2" if n_refs == 2 else f"images 1 to {n_refs}")
    gray = (f" {'The reference pictures are' if legacy else refs[0].upper() + refs[1:] + (' is' if n_refs == 1 else ' are')} "
            f"black-and-white line art: keep {'its' if n_refs == 1 and not legacy else 'their'} shapes and render "
            "in full color." if all_gray else "")
    if kind == "character":
        colors = _colors(entity)
        return (
            f"Character reference sheet of {entity.get('name') or 'the character'}, {where}\n"
            "Keep exactly: face, hairstyle, body shape and proportions, clothes, art style and line work.\n"
            + (f"Colors: {'; '.join(colors)}.\n" if colors else "")
            + "Layout: front full body, side full body, back full body and a head close-up, same person in every "
              "view, neutral standing pose, plain light background, no text, no other people." + gray
        )
    if kind == "location":
        return (f"Reference image of {entity.get('name') or 'the place'}, {place}\n"
                "Keep exactly: layout, architecture, materials, light and art style.\n"
                "Show it as a clear establishing view with room for characters, no people, no text." + gray)
    return (f"Reference image of {entity.get('name') or 'the object'}, {thing}\n"
            "Keep exactly: shape, colors, materials, details and art style.\n"
            "Single object, centered, plain light background, no people, no text." + gray)


async def prompt_from_pictures(kind: str, entity: dict[str, Any]) -> dict[str, Any]:
    """An image prompt written from what the reference pictures show. The vision
    model only fills short parts (what each picture shows, the art style, the
    clothes); Calliope puts them into the fixed structure and layout, so the
    prompt stays short and cannot drift from the pictures."""
    images = [p for p in entity.get("source_images") or [] if Path(p).is_file()]
    refs, _ = usable_refs(images, kind, max_refs(entity), chosen_refs(entity))
    if not refs:
        raise ValueError("No reference pictures to write from.")
    what = {"character": "person", "location": "place", "item": "object"}[kind]
    rules = prompt_text("assets.pictures_rules", count=len(refs), what=what, name=entity.get("name") or "")
    clothes_rule = prompt_text("assets.pictures_clothes") if kind == "character" else ""
    parts: list[dict[str, Any]] = [{"type": "text", "text": f"""{rules}
{clothes_rule}
Respond ONLY with JSON: {{"pictures": ["..."], "style": "..."{', "clothes": "..."' if kind == 'character' else ''}}}"""}]
    for i, p in enumerate(refs, 1):
        url = _data_url(p)
        if url:
            parts.append({"type": "text", "text": f"Picture {i}:"})
            parts.append({"type": "image_url", "image_url": {"url": url}})
    result = await generate_structured(
        [{"role": "system", "content": prompt_text("assets.pictures_system")},
         {"role": "user", "content": parts}], temperature=0.2, role="assets")
    shows = [str(x).strip().rstrip(".") for x in (result.get("pictures") or []) if str(x).strip()]
    style = str(result.get("style") or "").strip().rstrip(".")
    clothes = str(result.get("clothes") or "").strip().rstrip(".")
    named = [f"image {i} ({shows[i - 1][:1].lower() + shows[i - 1][1:] if i <= len(shows) else 'reference'})"
             for i in range(1, len(refs) + 1)]
    roles = named[0] if len(named) == 1 else ", ".join(named[:-1]) + " and " + named[-1]
    gray = all(is_grayscale(p) for p in images)
    name = entity.get("name") or f"the {what}"
    lines = []
    if kind == "character":
        colors = _colors(entity)
        lines = [
            f"Character reference sheet of {name}, the person in {roles}.",
            f"Art style: {style}{', rendered in full color' if gray else ''}." if style else "Art style: the same as the reference pictures.",
            "Keep exactly: face, hairstyle, body shape and proportions, clothes"
            + (f" ({clothes[:1].lower() + clothes[1:]})" if clothes else "") + ", art style and line work.",
        ]
        if colors:
            lines.append(f"Colors: {'; '.join(colors)}.")
        lines.append(prompt_text("assets.sheet_layout"))
    elif kind == "location":
        lines = [f"Reference image of {name}, the place in {roles}.",
                 f"Art style: {style}." if style else "Art style: the same as the reference pictures.",
                 "Keep exactly: layout, architecture, materials and light.",
                 "Show it as a clear establishing view with room for characters, no people, no text."]
    else:
        lines = [f"Reference image of {name}, the object in {roles}.",
                 f"Art style: {style}." if style else "Art style: the same as the reference pictures.",
                 "Keep exactly: shape, colors, materials and details.",
                 "Single object, centered, plain light background, no people, no text."]
    return {"prompt": "\n".join(lines), "refs": refs, "pictures": shows, "style": style}


def text_prompt(kind: str, entity: dict[str, Any]) -> str:
    """The detailed template from the description (no pictures)."""
    if kind == "character":
        return character_sheet_prompt(entity)
    return location_reference_prompt(entity) if kind == "location" else item_reference_prompt(entity)


def image_prompt(kind: str, entity: dict[str, Any]) -> tuple[str, list[str]]:
    """(prompt, reference pictures) as they go to ComfyUI for this asset."""
    images = [p for p in entity.get("source_images") or [] if Path(p).is_file()]
    refs, _ = usable_refs(images, kind, max_refs(entity), chosen_refs(entity))
    if not refs:
        return text_prompt(kind, entity), []
    return reference_prompt(kind, entity, len(refs), all(is_grayscale(p) for p in images)), refs


def prompt_is_auto(kind: str, entity: dict[str, Any]) -> bool:
    """Whether the stored image prompt is one Calliope made (so it may be rebuilt),
    not one the user wrote: empty, or equal to any template for this asset."""
    stored = (entity.get("consistency_prompt") or "").strip()
    if not stored:
        return True
    made = {text_prompt(kind, entity).strip()}
    for n in range(1, MAX_IMAGES + 1):
        for gray in (False, True):
            for legacy in (False, True):
                made.add(reference_prompt(kind, entity, n, gray, legacy=legacy).strip())
    return stored in made


def enqueue_from_images(project_id: int, kind: str, entity: dict[str, Any],
                        target: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """Queue the sheet/reference render with the asset's own pictures as references.
    ``target`` says where the worker writes the result (default: the project asset)."""
    from calliope.render_styles import for_project, negative_values, sheet_prompt_tags, styled_prompt, uses_tags

    style = for_project(project_id)
    images = [p for p in entity.get("source_images") or [] if Path(p).is_file()]
    refs, wf = usable_refs(images, kind, max_refs(entity), chosen_refs(entity), style)
    if wf is None:
        return None
    # What is sent is what the Image prompt field shows: the user's own text, or
    # the prompt Calliope builds (reference mode with pictures, text mode without).
    built, _ = image_prompt(kind, {**entity, "source_images": refs})
    prompt = (entity.get("consistency_prompt") or "").strip()
    if not prompt or prompt_is_auto(kind, entity):
        prompt = built
    conn = get_db(settings.db_path)
    try:
        wf_json = json.loads(conn.execute("SELECT workflow_json FROM workflows WHERE id = ?", (wf,)).fetchone()[0])
    finally:
        conn.close()
    inputs = parse_dynamic_inputs(wf_json)
    image_nodes = [str(i["nodeId"]) for i in inputs if i.get("kind") == "image"]
    if kind == "character" and uses_tags(style):
        prompt = sheet_prompt_tags(entity) or prompt
    prompt = styled_prompt(prompt, style)
    values = smart_fill_inputs(inputs, prompt=prompt,
                               extra={**negative_values(inputs, style), **dict(zip(image_nodes, refs))})
    return queue_manager.enqueue(
        project_id=project_id,
        kind="image",
        workflow_id=wf,
        payload={"input_values": values, **(target or {f"{kind}_id": entity["id"]}), "asset_target": "sheet",
                 "prompt": prompt},
    )


async def create_asset(project_id: int, *, kind: str, name: str, prompt: str = "", image_paths: list[str] | None = None,
                       generate: bool = True) -> dict[str, Any]:
    images = keep_images(project_id, kind, image_paths or [])
    look = await describe(kind, name, prompt, images, context=project_context(project_id))
    if kind == "character":
        look["appearance"] = complete_sheet(look["appearance"], name)
    derived = character_tags(look["appearance"]) if kind == "character" else None
    tags = json.dumps(derived) if derived is not None else tags_json(name_tags(name) + list(look["tags"]), name)
    conn = get_db(settings.db_path)
    try:
        if kind == "character":
            seed = {"name": name, "appearance": look["appearance"], "role": "", "age": "", "personality": ""}
            alias, role = story_fields(look["appearance"])
            cur = conn.execute(
                "INSERT INTO characters (project_id, name, appearance, consistency_prompt, tags, source_images, alias, role) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (project_id, name, look["appearance"], image_prompt(kind, {**seed, "source_images": images})[0], tags,
                 json.dumps(images), alias or None, role or None),
            )
        elif kind == "location":
            seed = {"name": name, "description": look["description"]}
            cur = conn.execute(
                "INSERT INTO locations (project_id, name, description, consistency_prompt, tags, source_images) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (project_id, name, look["description"], image_prompt(kind, {**seed, "source_images": images})[0], tags,
                 json.dumps(images)),
            )
        else:
            seed = {"name": name, "description": look["description"]}
            cur = conn.execute(
                "INSERT INTO items (project_id, name, description, consistency_prompt, tags, category, source_images) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (project_id, name, look["description"], image_prompt(kind, {**seed, "source_images": images})[0], tags,
                 look["category"], json.dumps(images)),
            )
        conn.commit()
        entity = row_to_dict(conn.execute(f"SELECT * FROM {TABLES[kind]} WHERE id = ?", (cur.lastrowid,)).fetchone())
    finally:
        conn.close()
    entity["source_images"] = images
    job = enqueue_from_images(project_id, kind, entity) if generate else None
    return {"asset": entity, "job_id": job["id"] if job else None}



async def create_library_asset(*, kind: str, name: str, prompt: str = "", image_paths: list[str] | None = None,
                               generate: bool = True) -> dict[str, Any]:
    """Same as :func:`create_asset`, straight into the asset library. The render
    runs in the hidden Playground project; the worker copies the result over."""
    from calliope.routers.playground import ensure_playground_project

    conn = get_db(settings.db_path)
    try:
        cur = conn.execute("INSERT INTO library_assets (kind, name) VALUES (?, ?)", (kind, name))
        lib_id = cur.lastrowid
        conn.commit()
        scratch = ensure_playground_project(conn)
    finally:
        conn.close()
    lib_dir = Path(settings.assets_dir) / "library" / str(lib_id)
    images = []
    for p in [p for p in image_paths or [] if p and Path(p).is_file()][:MAX_IMAGES]:
        (lib_dir / "source").mkdir(parents=True, exist_ok=True)
        images.append(str(shutil.copy2(p, lib_dir / "source" / Path(p).name)))
    # Library characters keep only who they are; each project adds its own outfit.
    look = await describe(kind, name, prompt, images, identity_only=kind == "character")
    field = LOOK_HINT[kind][0]
    if kind == "character":
        look[field] = complete_sheet(look[field], name)
    seed = {"name": name, field: look[field], "role": "", "age": "", "personality": ""}
    consistency = image_prompt(kind, {**seed, "source_images": images})[0]
    data: dict[str, Any] = {field: look[field], "consistency_prompt": consistency, "source_images": images}
    if kind == "character":
        data["alias"], data["role"] = story_fields(look[field])
    entity = {"id": lib_id, "name": name, **data}
    job = enqueue_from_images(scratch, kind, entity, {"library_asset_id": lib_id}) if generate else None
    if job:
        data["pending_job"] = job["id"]
    conn = get_db(settings.db_path)
    try:
        conn.execute("UPDATE library_assets SET category = ?, tags = ?, data_json = ? WHERE id = ?",
                     (look["category"],
                      json.dumps(identity_tags(look[field])) if kind == "character"
                      else tags_json(name_tags(name) + list(look["tags"]), name),
                      json.dumps(data, ensure_ascii=False), lib_id))
        conn.commit()
        row = row_to_dict(conn.execute("SELECT * FROM library_assets WHERE id = ?", (lib_id,)).fetchone())
    finally:
        conn.close()
    return {"asset_id": row["id"], "job_id": job["id"] if job else None}


def library_render_done(asset_id: int, output: str) -> None:
    """Worker hook: keep a copy of a finished library render with the asset."""
    conn = get_db(settings.db_path)
    try:
        row = conn.execute("SELECT kind, data_json FROM library_assets WHERE id = ?", (asset_id,)).fetchone()
        if not row or not Path(output).is_file():
            return
        dest = Path(settings.assets_dir) / "library" / str(asset_id)
        dest.mkdir(parents=True, exist_ok=True)
        image = str(shutil.copy2(output, dest / Path(output).name))
        data = json.loads(row["data_json"] or "{}")
        data.pop("pending_job", None)
        data["sheet_path" if row["kind"] == "character" else "reference_image_path"] = image
        conn.execute("UPDATE library_assets SET image_path = ?, data_json = ?, updated_at = CURRENT_TIMESTAMP "
                     "WHERE id = ?", (image, json.dumps(data, ensure_ascii=False), asset_id))
        conn.commit()
    finally:
        conn.close()
