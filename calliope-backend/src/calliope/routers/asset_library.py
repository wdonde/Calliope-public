"""Asset library across projects: characters, locations and items with their
images, saved from one project and added to another.

A library asset is a copy (fields in ``data_json``, images copied under
assets/library/<id>/), so editing or deleting it never touches a project,
and a project keeps working when its library source is removed. Tags and
the item category are what the library is searched by; ``alias:`` tags go
along into the project and help panels find the character there.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from calliope.agent.asset_create import (
    LOOK_HINT,
    create_asset,
    create_library_asset,
    describe,
    enqueue_from_images,
    project_context,
)
from calliope.agent.asset_tags import merge_tags, normalize_category, suggest_tags, tags_json
from calliope.config import settings
from calliope.db import get_db, row_to_dict

router = APIRouter()

Kind = Literal["character", "location", "item"]
TABLES = {"character": "characters", "location": "locations", "item": "items"}
# Text fields copied with the asset, and the image columns that travel along.
FIELDS = {
    "character": ("role", "age", "appearance", "personality", "consistency_prompt"),
    "location": ("description", "consistency_prompt"),
    "item": ("description", "consistency_prompt"),
}
IMAGES = {
    "character": ("sheet_path", "portrait_path"),
    "location": ("reference_image_path",),
    "item": ("reference_image_path",),
}


class LibraryUpdate(BaseModel):
    name: str | None = None
    category: str | None = None
    tags: list[str] | None = None
    # Text fields of the asset (appearance/description, role, age, personality,
    # consistency_prompt = the image prompt); only known fields of its kind are kept.
    data: dict[str, str | None] | None = None
    # The asset's own pictures in order; new paths (uploads, other Calliope images) are copied in.
    source_images: list[str] | None = None


class AddToProject(BaseModel):
    project_id: int


class DescribeRequest(BaseModel):
    notes: str = ""  # extra hints, any language


class NewAsset(BaseModel):
    kind: Kind
    name: str = Field(..., min_length=1)
    prompt: str = ""  # the user's notes, any language
    image_paths: list[str] = []  # uploaded pictures (Playground uploads)
    generate: bool = True


def _library_dir(asset_id: int) -> Path:
    return Path(settings.assets_dir) / "library" / str(asset_id)


def _copy(src: str | None, dest_dir: Path) -> str | None:
    if not src or not Path(src).is_file():
        return None
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / Path(src).name
    if Path(src).resolve() != dest.resolve():
        shutil.copy2(src, dest)
    return str(dest)


def _out(row) -> dict[str, Any]:
    d = row_to_dict(row)
    d["data"] = json.loads(d.pop("data_json") or "{}")
    return d


def _add_projects(assets: list[dict[str, Any]]) -> None:
    """Where each asset came from and which projects hold a copy (tag library:<id>)."""
    conn = get_db(settings.db_path)
    try:
        titles = {r["id"]: r["title"] for r in conn.execute("SELECT id, title FROM projects")}
        used: dict[int, set[int]] = {}
        for table in TABLES.values():
            for row in conn.execute(f"SELECT project_id, tags FROM {table} WHERE tags LIKE '%library:%'"):
                for tag in json.loads(row["tags"] or "[]"):
                    if tag.startswith("library:") and tag[8:].isdigit():
                        used.setdefault(int(tag[8:]), set()).add(row["project_id"])
    finally:
        conn.close()
    for a in assets:
        src = a.get("source_project_id")
        a["source_project"] = {"id": src, "title": titles[src]} if src in titles else None
        a["used_in"] = [{"id": p, "title": titles[p]} for p in sorted(used.get(a["id"], ())) if p in titles]


def _matches(asset: dict[str, Any], q: str) -> bool:
    text = " ".join([asset["name"], asset.get("category") or "", *asset["tags"],
                     *(str(v) for v in asset["data"].values() if isinstance(v, str))]).lower()
    return all(word in text for word in q.lower().split())


@router.get("/asset-library")
async def list_library(kind: Kind | None = None, q: str | None = None, tag: str | None = None) -> list[dict[str, Any]]:
    conn = get_db(settings.db_path)
    try:
        rows = conn.execute("SELECT * FROM library_assets ORDER BY kind, name COLLATE NOCASE").fetchall()
    finally:
        conn.close()
    assets = [_out(r) for r in rows]
    _add_projects(assets)
    if kind:
        assets = [a for a in assets if a["kind"] == kind]
    if tag:
        assets = [a for a in assets if tag.lower() in (t.lower() for t in a["tags"])]
    if q:
        assets = [a for a in assets if _matches(a, q)]
    return assets


@router.post("/projects/{project_id}/{kind}/{entity_id}/to-library")
async def save_to_library(project_id: int, kind: Kind, entity_id: int) -> dict[str, Any]:
    conn = get_db(settings.db_path)
    try:
        row = conn.execute(f"SELECT * FROM {TABLES[kind]} WHERE id = ? AND project_id = ?",
                           (entity_id, project_id)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Asset not found")
        e = row_to_dict(row)
        cur = conn.execute(
            "INSERT INTO library_assets (kind, name, category, tags, source_project_id) VALUES (?, ?, ?, ?, ?)",
            (kind, e["name"], e.get("category"), tags_json(e.get("tags")), project_id),
        )
        lib_id = cur.lastrowid
        data = {f: e.get(f) for f in FIELDS[kind]}
        for col in IMAGES[kind]:
            data[col] = _copy(e.get(col), _library_dir(lib_id))
        data["source_images"] = [c for c in (_copy(p, _library_dir(lib_id) / "source")
                                             for p in e.get("source_images") or []) if c]
        image = next((data[c] for c in IMAGES[kind] if data.get(c)), None)
        conn.execute("UPDATE library_assets SET data_json = ?, image_path = ? WHERE id = ?",
                     (json.dumps(data, ensure_ascii=False), image, lib_id))
        conn.commit()
        return _out(conn.execute("SELECT * FROM library_assets WHERE id = ?", (lib_id,)).fetchone())
    finally:
        conn.close()


@router.post("/asset-library/{asset_id}/to-project")
async def add_to_project(asset_id: int, payload: AddToProject) -> dict[str, Any]:
    conn = get_db(settings.db_path)
    try:
        row = conn.execute("SELECT * FROM library_assets WHERE id = ?", (asset_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Library asset not found")
        if not conn.execute("SELECT id FROM projects WHERE id = ?", (payload.project_id,)).fetchone():
            raise HTTPException(status_code=404, detail="Project not found")
        a = _out(row)
        kind = a["kind"]
        values: dict[str, Any] = {"project_id": payload.project_id, "name": a["name"],
                                  "tags": tags_json(a["tags"] + [f"library:{asset_id}"])}
        values.update({f: a["data"].get(f) for f in FIELDS[kind]})
        dest = Path(settings.assets_dir) / str(payload.project_id) / "library" / str(asset_id)
        for col in IMAGES[kind]:
            values[col] = _copy(a["data"].get(col), dest)
        values["source_images"] = json.dumps(
            [c for c in (_copy(p, dest / "source") for p in a["data"].get("source_images") or []) if c])
        if kind == "item":
            values["category"] = a.get("category")
        cols = ", ".join(values)
        cur = conn.execute(f"INSERT INTO {TABLES[kind]} ({cols}) VALUES ({', '.join(':' + c for c in values)})", values)
        conn.commit()
        return row_to_dict(conn.execute(f"SELECT * FROM {TABLES[kind]} WHERE id = ?", (cur.lastrowid,)).fetchone())
    finally:
        conn.close()


@router.patch("/asset-library/{asset_id}")
async def update_library_asset(asset_id: int, payload: LibraryUpdate) -> dict[str, Any]:
    fields = payload.model_dump(exclude_unset=True)
    conn = get_db(settings.db_path)
    try:
        row = conn.execute("SELECT * FROM library_assets WHERE id = ?", (asset_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Library asset not found")
        asset = _out(row)
        sets: dict[str, Any] = {}
        if fields.get("name"):
            sets["name"] = fields["name"].strip()
        if "category" in fields:
            sets["category"] = normalize_category(fields["category"])
        if "tags" in fields:
            sets["tags"] = tags_json(fields["tags"], sets.get("name") or asset["name"])
        data = dict(asset["data"])
        for key, value in (fields.get("data") or {}).items():
            if key in FIELDS[asset["kind"]]:
                data[key] = value
        if "source_images" in fields:
            _check_upload_paths(fields["source_images"] or [])
            keep = _library_dir(asset_id) / "source"
            data["source_images"] = [c for c in (p if Path(p).parent == keep else _copy(p, keep)
                                                 for p in fields["source_images"] or []) if c]
        if "data" in fields or "source_images" in fields:
            sets["data_json"] = json.dumps(data, ensure_ascii=False)
        if sets:
            cols = ", ".join(f"{k} = :{k}" for k in sets)
            conn.execute(f"UPDATE library_assets SET {cols}, updated_at = CURRENT_TIMESTAMP WHERE id = :id",
                         {**sets, "id": asset_id})
            conn.commit()
        out = _out(conn.execute("SELECT * FROM library_assets WHERE id = ?", (asset_id,)).fetchone())
    finally:
        conn.close()
    _add_projects([out])
    return out


@router.delete("/asset-library/{asset_id}")
async def delete_library_asset(asset_id: int) -> dict[str, bool]:
    conn = get_db(settings.db_path)
    try:
        if conn.execute("DELETE FROM library_assets WHERE id = ?", (asset_id,)).rowcount == 0:
            raise HTTPException(status_code=404, detail="Library asset not found")
        conn.commit()
    finally:
        conn.close()
    shutil.rmtree(_library_dir(asset_id), ignore_errors=True)
    return {"ok": True}


@router.post("/projects/{project_id}/assets/suggest-tags")
async def suggest_project_tags(project_id: int) -> dict[str, int]:
    return await suggest_tags(project_id)


@router.post("/projects/{project_id}/assets/new")
async def new_asset(project_id: int, payload: NewAsset) -> dict[str, Any]:
    conn = get_db(settings.db_path)
    try:
        if not conn.execute("SELECT id FROM projects WHERE id = ?", (project_id,)).fetchone():
            raise HTTPException(status_code=404, detail="Project not found")
    finally:
        conn.close()
    _check_upload_paths(payload.image_paths)
    return await create_asset(project_id, kind=payload.kind, name=payload.name.strip(), prompt=payload.prompt,
                              image_paths=payload.image_paths, generate=payload.generate)


@router.post("/projects/{project_id}/{kind}/{entity_id}/from-images")
async def regenerate_from_images(project_id: int, kind: Kind, entity_id: int) -> dict[str, Any]:
    """Render the sheet/reference again from the asset's own pictures."""
    conn = get_db(settings.db_path)
    try:
        row = conn.execute(f"SELECT * FROM {TABLES[kind]} WHERE id = ? AND project_id = ?",
                           (entity_id, project_id)).fetchone()
    finally:
        conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="Asset not found")
    entity = row_to_dict(row)
    if not entity.get("source_images"):
        raise HTTPException(status_code=422, detail="This asset has no pictures of its own.")
    job = enqueue_from_images(project_id, kind, entity)
    if job is None:
        raise HTTPException(status_code=422, detail="No enabled image workflow.")
    return {"job_id": job["id"]}


@router.post("/asset-library/new")
async def new_library_asset(payload: NewAsset) -> dict[str, Any]:
    _check_upload_paths(payload.image_paths)
    return await create_library_asset(kind=payload.kind, name=payload.name.strip(), prompt=payload.prompt,
                                      image_paths=payload.image_paths, generate=payload.generate)


@router.post("/asset-library/{asset_id}/from-images")
async def library_from_images(asset_id: int) -> dict[str, Any]:
    from calliope.routers.playground import ensure_playground_project

    conn = get_db(settings.db_path)
    try:
        row = conn.execute("SELECT * FROM library_assets WHERE id = ?", (asset_id,)).fetchone()
        scratch = ensure_playground_project(conn)
    finally:
        conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="Library asset not found")
    a = _out(row)
    # Without own pictures this renders from the prompt alone.
    job = enqueue_from_images(scratch, a["kind"], {"id": asset_id, "name": a["name"], **a["data"]},
                              {"library_asset_id": asset_id})
    if job is None:
        raise HTTPException(status_code=422, detail="No enabled image workflow.")
    _set_pending(asset_id, a["data"], job["id"])
    return {"job_id": job["id"]}


def _set_pending(asset_id: int, data: dict[str, Any], job_id: int) -> None:
    conn = get_db(settings.db_path)
    try:
        conn.execute("UPDATE library_assets SET data_json = ? WHERE id = ?",
                     (json.dumps({**data, "pending_job": job_id}, ensure_ascii=False), asset_id))
        conn.commit()
    finally:
        conn.close()


def _check_upload_paths(paths: list[str]) -> None:
    assets_root = Path(settings.assets_dir).resolve()
    for p in paths:
        try:
            Path(p).resolve().relative_to(assets_root)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=f"Image outside the assets folder: {p}") from exc


@router.post("/asset-library/{asset_id}/describe")
async def describe_library_asset(asset_id: int, payload: DescribeRequest | None = None) -> dict[str, Any]:
    """Draft the look from the asset's pictures and notes (not saved -- the Edit dialog fills it in)."""
    conn = get_db(settings.db_path)
    try:
        row = conn.execute("SELECT * FROM library_assets WHERE id = ?", (asset_id,)).fetchone()
    finally:
        conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="Library asset not found")
    a = _out(row)
    field = LOOK_HINT[a["kind"]][0]
    known = a["data"].get(field) or ""
    look = await describe(a["kind"], a["name"], (payload.notes if payload else "") or "",
                          [p for p in a["data"].get("source_images") or [] if Path(p).is_file()],
                          context=f"{project_context(a.get('source_project_id'))}\nCurrent look: {known}".strip())
    return {"field": field, "text": look[field], "tags": look["tags"], "category": look["category"]}


@router.post("/projects/{project_id}/{kind}/{entity_id}/describe")
async def describe_project_asset(project_id: int, kind: Kind, entity_id: int) -> dict[str, Any]:
    """Write the missing look of a project asset from its name, notes and own pictures, and save it."""
    table = TABLES[kind]
    conn = get_db(settings.db_path)
    try:
        row = conn.execute(f"SELECT * FROM {table} WHERE id = ? AND project_id = ?", (entity_id, project_id)).fetchone()
    finally:
        conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="Asset not found")
    e = row_to_dict(row)
    field = LOOK_HINT[kind][0]
    known = " ".join(f"{k}: {e[k]}" for k in ("role", "age", "personality", field) if e.get(k))
    look = await describe(kind, e["name"], "", [p for p in e.get("source_images") or [] if Path(p).is_file()],
                          context=f"{project_context(project_id)}\n{known}".strip())
    sets = {field: look[field], "tags": tags_json(merge_tags(e.get("tags") or [], list(look["tags"])), e["name"])}
    if kind == "item" and not e.get("category") and look["category"]:
        sets["category"] = look["category"]
    conn = get_db(settings.db_path)
    try:
        conn.execute(f"UPDATE {table} SET {', '.join(f'{k} = :{k}' for k in sets)} WHERE id = :id",
                     {**sets, "id": entity_id})
        conn.commit()
        return row_to_dict(conn.execute(f"SELECT * FROM {table} WHERE id = ?", (entity_id,)).fetchone())
    finally:
        conn.close()
