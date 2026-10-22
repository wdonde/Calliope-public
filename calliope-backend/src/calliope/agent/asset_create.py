"""Create a character/location/item from a description and/or reference pictures.

The user's pictures are kept with the asset (``source_images``). A vision
model (the "assets" role) looks at them and writes the fixed look in English
-- the same shape Draft Storyline asks for -- plus tags; without pictures the
description alone is turned into that look. The sheet/reference image is then
rendered with the pictures as references: one or two go in directly, more are
tiled into one collage so a one-reference workflow can take them.
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

from calliope.agent.asset_tags import ITEM_CATEGORIES, name_tags, normalize_category, tags_json
from calliope.agent.llm import generate_structured
from calliope.agent.prompts import (
    character_image_prompt,
    character_sheet_prompt,
    item_image_prompt,
    item_reference_prompt,
    location_image_prompt,
    location_reference_prompt,
)
from calliope.comfyui.parser import parse_dynamic_inputs
from calliope.comfyui.smart_fill import smart_fill_inputs
from calliope.config import settings
from calliope.db import get_db, row_to_dict
from calliope.queue.manager import queue_manager

TABLES = {"character": "characters", "location": "locations", "item": "items"}
MAX_IMAGES = 6
LOOK_HINT = {
    "character": ("appearance", "fixed look for image generation: age, build, face, hair color and style, "
                  "one distinctive mark, and ONE default outfit incl. headwear (40-70 words)"),
    "location": ("description", "visual description for image generation: place, architecture or landscape, "
                 "materials, light, mood (40-70 words)"),
    "item": ("description", "visual description for image generation: shape, size, material, colors, "
             "distinctive details (30-50 words)"),
}


def _data_url(path: str, size: int = 768) -> str | None:
    try:
        im = Image.open(path).convert("RGB")
    except OSError:
        return None
    im.thumbnail((size, size))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=85)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


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


def collage(paths: list[str], out: Path, size: int = 1536) -> str:
    """All pictures side by side on white, in one image (for a one-reference workflow)."""
    ims = [Image.open(p).convert("RGB") for p in paths]
    cols = 2 if len(ims) <= 4 else 3
    rows = -(-len(ims) // cols)
    cell = size // cols
    sheet = Image.new("RGB", (cell * cols, cell * rows), "white")
    for i, im in enumerate(ims):
        im.thumbnail((cell - 16, cell - 16))
        x, y = (i % cols) * cell, (i // cols) * cell
        sheet.paste(im, (x + (cell - im.width) // 2, y + (cell - im.height) // 2))
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out)
    return str(out)


async def describe(kind: str, name: str, prompt: str, images: list[str]) -> dict[str, Any]:
    """The asset's look (English), tags and -- for items -- category, from text and pictures."""
    field, hint = LOOK_HINT[kind]
    category = f',\n  "category": "one of {", ".join(ITEM_CATEGORIES)} or your own word"' if kind == "item" else ""
    text = f"""Create a {kind} asset for a story.
Name: {name}
User notes (any language): {prompt or '(none)'}
{f'The {len(images)} attached picture(s) show this {kind}; describe what they show, and let the notes override them.' if images else ''}

Write in English:
- "{field}": {hint}. Concrete and visual, no story events.
- "tags": 1-5 short "key:value" tags with English keys (alias for other names or nicknames, faction, type, owner).

Respond ONLY with JSON:
{{
  "{field}": "...",
  "tags": ["alias:..."]{category}
}}"""
    parts: list[dict[str, Any]] = [{"type": "text", "text": text}]
    for p in images:
        url = _data_url(p)
        if url:
            parts.append({"type": "image_url", "image_url": {"url": url}})
    messages = [
        {"role": "system", "content": "You are a character and production designer. Respond with a single valid JSON object."},
        {"role": "user", "content": parts if len(parts) > 1 else text},
    ]
    try:
        result = await generate_structured(messages, temperature=0.4, role="assets" if images else "writer")
    except Exception:  # noqa: BLE001 -- the notes still make a usable asset
        result = {}
    return {
        field: (result.get(field) or prompt or name).strip(),
        "tags": result.get("tags") or [],
        "category": normalize_category(result.get("category")) if kind == "item" else None,
    }


def _pick_workflow(n_refs: int) -> int | None:
    from calliope.agent.comic import _pick_workflow as pick  # Klein-first, by reference count

    return pick(n_refs)


def enqueue_from_images(project_id: int, kind: str, entity: dict[str, Any],
                        target: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """Queue the sheet/reference render with the asset's own pictures as references.
    ``target`` says where the worker writes the result (default: the project asset)."""
    images = [p for p in entity.get("source_images") or [] if Path(p).is_file()]
    refs = images
    if len(images) > 2:
        refs = [collage(images, Path(images[0]).parent / "collage.png")]
    wf = _pick_workflow(len(refs))
    if wf is None:
        refs, wf = [], _pick_workflow(0)
    if wf is None:
        return None
    if kind == "character":
        prompt = character_image_prompt(entity, kind="sheet")
        if refs:
            prompt += (" The character looks exactly like the person in the reference picture(s): same face, "
                       "hair, build and outfit; draw them in the sheet layout described above.")
    elif kind == "location":
        prompt = location_image_prompt(entity)
        if refs:
            prompt += " Show the same place as the reference picture(s): same layout, materials and light."
    else:
        prompt = item_image_prompt(entity)
        if refs:
            prompt += " Show the same object as the reference picture(s): same shape, colors and details."
    conn = get_db(settings.db_path)
    try:
        wf_json = json.loads(conn.execute("SELECT workflow_json FROM workflows WHERE id = ?", (wf,)).fetchone()[0])
    finally:
        conn.close()
    inputs = parse_dynamic_inputs(wf_json)
    image_nodes = [str(i["nodeId"]) for i in inputs if i.get("kind") == "image"]
    values = smart_fill_inputs(inputs, prompt=prompt, extra=dict(zip(image_nodes, refs)))
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
    look = await describe(kind, name, prompt, images)
    tags = tags_json(name_tags(name) + list(look["tags"]))
    conn = get_db(settings.db_path)
    try:
        if kind == "character":
            seed = {"name": name, "appearance": look["appearance"], "role": "", "age": "", "personality": ""}
            cur = conn.execute(
                "INSERT INTO characters (project_id, name, appearance, consistency_prompt, tags, source_images) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (project_id, name, look["appearance"], character_sheet_prompt(seed), tags, json.dumps(images)),
            )
        elif kind == "location":
            seed = {"name": name, "description": look["description"]}
            cur = conn.execute(
                "INSERT INTO locations (project_id, name, description, consistency_prompt, tags, source_images) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (project_id, name, look["description"], location_reference_prompt(seed), tags, json.dumps(images)),
            )
        else:
            seed = {"name": name, "description": look["description"]}
            cur = conn.execute(
                "INSERT INTO items (project_id, name, description, consistency_prompt, tags, category, source_images) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (project_id, name, look["description"], item_reference_prompt(seed), tags, look["category"],
                 json.dumps(images)),
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
    look = await describe(kind, name, prompt, images)
    field = LOOK_HINT[kind][0]
    seed = {"name": name, field: look[field], "role": "", "age": "", "personality": ""}
    consistency = (character_sheet_prompt(seed) if kind == "character"
                   else location_reference_prompt(seed) if kind == "location" else item_reference_prompt(seed))
    data: dict[str, Any] = {field: look[field], "consistency_prompt": consistency, "source_images": images}
    entity = {"id": lib_id, "name": name, **data}
    job = enqueue_from_images(scratch, kind, entity, {"library_asset_id": lib_id}) if generate else None
    if job:
        data["pending_job"] = job["id"]
    conn = get_db(settings.db_path)
    try:
        conn.execute("UPDATE library_assets SET category = ?, tags = ?, data_json = ? WHERE id = ?",
                     (look["category"], tags_json(name_tags(name) + list(look["tags"])),
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
