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
from pydantic import BaseModel

from calliope.agent.asset_tags import normalize_category, suggest_tags, tags_json
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


class AddToProject(BaseModel):
    project_id: int


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
    data = payload.model_dump(exclude_unset=True)
    if "tags" in data:
        data["tags"] = tags_json(data["tags"])
    if "category" in data:
        data["category"] = normalize_category(data["category"])
    conn = get_db(settings.db_path)
    try:
        if not conn.execute("SELECT id FROM library_assets WHERE id = ?", (asset_id,)).fetchone():
            raise HTTPException(status_code=404, detail="Library asset not found")
        if data:
            sets = ", ".join(f"{k} = :{k}" for k in data)
            conn.execute(f"UPDATE library_assets SET {sets}, updated_at = CURRENT_TIMESTAMP WHERE id = :id",
                         {**data, "id": asset_id})
            conn.commit()
        return _out(conn.execute("SELECT * FROM library_assets WHERE id = ?", (asset_id,)).fetchone())
    finally:
        conn.close()


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
