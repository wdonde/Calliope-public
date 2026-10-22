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
    outfit_from_text,
    create_library_asset,
    describe,
    enqueue_from_images,
    project_context,
)
from calliope.agent.asset_tags import merge_tags, normalize_category, suggest_tags, tags_json
from calliope.agent.character_identity import (
    IDENTITY,
    IDENTITY_TAGS,
    character_tags,
    complete_sheet,
    story_fields,
    sync_story_fields,
    tag_matches,
    identity_tags,
    identity_text,
    is_sheet,
    match,
    outfit_text,
)
from calliope.agent.prompts import character_sheet_prompt
from calliope.config import settings
from calliope.db import get_db, row_to_dict

router = APIRouter()

Kind = Literal["character", "location", "item"]
TABLES = {"character": "characters", "location": "locations", "item": "items"}
# Text fields copied with the asset, and the image columns that travel along.
FIELDS = {
    # Characters keep only their identity (CHARACTER INFORMATION); outfit,
    # personality and role belong to each project (role travels as a tag).
    "character": ("alias", "role", "appearance", "consistency_prompt"),
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
    # The dialog's current Alias/Role: kept in the new sheet unless empty.
    alias: str | None = None
    role: str | None = None


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
    """Where each asset came from and which projects hold a copy (tag library:<id>);
    a project is outdated when it took the identity before the library last changed."""
    conn = get_db(settings.db_path)
    try:
        titles = {r["id"]: r["title"] for r in conn.execute("SELECT id, title FROM projects")}
        used: dict[int, dict[int, str]] = {}
        for table in TABLES.values():
            synced_col = ", library_synced_at" if table == "characters" else ""
            for row in conn.execute(f"SELECT project_id, tags{synced_col} FROM {table} WHERE tags LIKE '%library:%'"):
                for tag in json.loads(row["tags"] or "[]"):
                    if tag.startswith("library:") and tag[8:].isdigit():
                        synced = (row["library_synced_at"] if synced_col else None) or ""
                        seen = used.setdefault(int(tag[8:]), {})
                        seen[row["project_id"]] = min(seen.get(row["project_id"], synced), synced)
    finally:
        conn.close()
    for a in assets:
        src = a.get("source_project_id")
        a["source_project"] = {"id": src, "title": titles[src]} if src in titles else None
        a["used_in"] = [
            {"id": p, "title": titles[p],
             "outdated": a["kind"] == "character" and bool(synced) and (a.get("updated_at") or "") > synced}
            for p, synced in sorted(used.get(a["id"], {}).items()) if p in titles
        ]


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
        assets = [a for a in assets if any(tag_matches(t, tag) for t in a["tags"])]
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
        tags = list(e.get("tags") or [])
        if kind == "character":
            # Older characters kept alias/role as tags: they move into the sheet.
            aliases = [t.split(":", 1)[1] for t in tags if t.lower().startswith("alias:")]
            e["appearance"] = complete_sheet(identity_text(e.get("appearance")), e["name"], e.get("role"), aliases)
            e["appearance"], e["alias"], e["role"] = sync_story_fields(e["appearance"], e.get("alias"), e.get("role"))
            e["consistency_prompt"] = character_sheet_prompt({"name": e["name"], "appearance": e["appearance"]})
            tags = character_tags(e["appearance"]) or tags
        cur = conn.execute(
            "INSERT INTO library_assets (kind, name, category, tags, source_project_id) VALUES (?, ?, ?, ?, ?)",
            (kind, e["name"], e.get("category"), tags_json(tags, e["name"]), project_id),
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
        if kind == "character":
            values["role"] = next((t.split(":", 1)[1] for t in a["tags"] if t.lower().startswith("role:")), None)
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
        if asset["kind"] == "character":
            # Per-project fields from older library entries do not belong here
            # (role is a library field: it is linked into the sheet).
            for key in ("age", "personality"):
                data.pop(key, None)
        if asset["kind"] == "character" and "appearance" in (fields.get("data") or {}):
            # Alias/role of older entries lived in tags; they move into the sheet.
            data["appearance"] = complete_sheet(
                identity_text(data["appearance"]), sets.get("name") or asset["name"],
                role=next((t[5:] for t in asset["tags"] if t.lower().startswith("role:")), None),
                aliases=[t[6:] for t in asset["tags"] if t.lower().startswith("alias:")])
        if asset["kind"] == "character" and ({"appearance", "alias", "role"} & set(fields.get("data") or {})):
            # Alias/Role live in their own fields; the sheet's lines follow them.
            data["appearance"], data["alias"], data["role"] = sync_story_fields(
                data.get("appearance"), data.get("alias"), data.get("role"))
            derived = character_tags(data["appearance"])
            if derived is not None:
                sets["tags"] = json.dumps(derived, ensure_ascii=False)
        if asset["kind"] == "character" and "appearance" in (fields.get("data") or {}):
            if not (data.get("consistency_prompt") or "").strip():
                data["consistency_prompt"] = character_sheet_prompt({"name": sets.get("name") or asset["name"],
                                                                     "appearance": data["appearance"]})
            derived = character_tags(data["appearance"])
            if derived is not None:
                sets["tags"] = json.dumps(derived, ensure_ascii=False)
        if asset["kind"] == "character" and "tags" in sets and is_sheet(data.get("appearance")):
            # A character's tags come from its sheet; typed tags are not kept.
            sets["tags"] = json.dumps(character_tags(data["appearance"]), ensure_ascii=False)
        if "source_images" in fields:
            _check_upload_paths(fields["source_images"] or [])
            keep = _library_dir(asset_id) / "source"
            data["source_images"] = [c for c in (p if Path(p).parent == keep else _copy(p, keep)
                                                 for p in fields["source_images"] or []) if c]
        if "data" in fields or "source_images" in fields or asset["kind"] == "character":
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
    pictures = [p for p in a["data"].get("source_images") or [] if Path(p).is_file()]
    # With pictures the old text is left out: it would only pass on its guesses.
    known = "" if pictures else a["data"].get(field) or ""
    look = await describe(a["kind"], a["name"], (payload.notes if payload else "") or "",
                          pictures,
                          context=f"{project_context(a.get('source_project_id'))}\n"
                                  f"{'Earlier draft: ' + known if known else ''}".strip(),
                          identity_only=a["kind"] == "character")
    if a["kind"] == "character":
        look[field] = complete_sheet(look[field], a["name"],
                                     aliases=[t[6:] for t in a["tags"] if t.lower().startswith("alias:")],
                                     role=next((t[5:] for t in a["tags"] if t.lower().startswith("role:")), None))
    alias = role = None
    if a["kind"] == "character":
        keep_alias = payload.alias if payload and payload.alias is not None else a["data"].get("alias")
        keep_role = payload.role if payload and payload.role is not None else a["data"].get("role")
        look[field], alias, role = sync_story_fields(look[field], keep_alias, keep_role)
    tags = character_tags(look[field]) if a["kind"] == "character" else list(look["tags"])
    tags = tags if tags is not None else list(look["tags"])
    return {"field": field, "text": look[field], "tags": tags, "category": look["category"], "alias": alias, "role": role}


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
    pictures = [p for p in e.get("source_images") or [] if Path(p).is_file()]
    # With pictures the old look is left out (it would only pass on its guesses); role etc. stay.
    known = " ".join(f"{k}: {e[k]}" for k in ("role", "age", "personality", *(() if pictures else (field,)))
                     if e.get(k))
    # A character linked to the library keeps that identity; only its outfit is (re)written.
    locked = kind == "character" and any(t.startswith("library:") for t in e.get("tags") or [])
    look = await describe(kind, e["name"], "", [p for p in e.get("source_images") or [] if Path(p).is_file()],
                          context=f"{project_context(project_id)}\n{known}".strip(), outfit_only=locked)
    text = look[field]
    if locked:
        text = f"{identity_text(e.get(field))}\n\n{text}".strip()
    if kind == "character":
        text = complete_sheet(text, e["name"], e.get("role"),
                              [t[6:] for t in e.get("tags") or [] if t.lower().startswith("alias:")])
        text, e["alias"], e["role"] = sync_story_fields(text, e.get("alias"), e.get("role"))
    derived = character_tags(text, e.get("tags")) if kind == "character" else None
    sets = {field: text, "tags": json.dumps(derived, ensure_ascii=False) if derived is not None
            else tags_json(merge_tags(e.get("tags") or [], list(look["tags"])), e["name"])}
    if kind == "item" and not e.get("category") and look["category"]:
        sets["category"] = look["category"]
    if kind == "character":
        sets["alias"], sets["role"] = e.get("alias") or None, e.get("role") or None
    conn = get_db(settings.db_path)
    try:
        conn.execute(f"UPDATE {table} SET {', '.join(f'{k} = :{k}' for k in sets)} WHERE id = :id",
                     {**sets, "id": entity_id})
        conn.commit()
        return row_to_dict(conn.execute(f"SELECT * FROM {table} WHERE id = ?", (entity_id,)).fetchone())
    finally:
        conn.close()


@router.get("/projects/{project_id}/library-matches")
async def library_matches(project_id: int) -> list[dict[str, Any]]:
    """For each project character, the library characters it may be (best first)."""
    conn = get_db(settings.db_path)
    try:
        chars = [row_to_dict(r) for r in conn.execute("SELECT * FROM characters WHERE project_id = ?", (project_id,))]
        lib = [_out(r) for r in conn.execute("SELECT * FROM library_assets WHERE kind = 'character'")]
    finally:
        conn.close()
    out = []
    for c in chars:
        linked = {t[8:] for t in c.get("tags") or [] if t.startswith("library:")}
        found = []
        for a in lib:
            score, why = match(c, a)
            is_linked = str(a["id"]) in linked
            if score >= 3 or is_linked:
                found.append({"asset_id": a["id"], "name": a["name"], "score": score, "why": why,
                              "image_path": a["image_path"], "linked": is_linked,
                              # The library changed since the project took this identity.
                              "outdated": is_linked and (a.get("updated_at") or "") > (c.get("library_synced_at") or "")})
        if found:
            found.sort(key=lambda m: (not m["linked"], -m["score"]))
            out.append({"character_id": c["id"], "character_name": c["name"], "matches": found[:3]})
    return out


class ApplyRequest(BaseModel):
    # Replace a prompt the user edited by hand (otherwise it is kept and reported).
    replace_prompt: bool = False


@router.post("/asset-library/{asset_id}/apply/{project_id}/{character_id}")
async def apply_library_identity(asset_id: int, project_id: int, character_id: int,
                                 payload: ApplyRequest | None = None) -> dict[str, Any]:
    """Give a project character the library identity: its CHARACTER INFORMATION is
    replaced and the project's outfit stays (read out of a prose appearance
    first), the library pictures become its reference pictures, alias/identity
    tags join its tags. A hand-edited image prompt is only replaced on request."""
    conn = get_db(settings.db_path)
    try:
        row = conn.execute("SELECT * FROM library_assets WHERE id = ? AND kind = 'character'", (asset_id,)).fetchone()
        char = conn.execute("SELECT * FROM characters WHERE id = ? AND project_id = ?",
                            (character_id, project_id)).fetchone()
    finally:
        conn.close()
    if not row or not char:
        raise HTTPException(status_code=404, detail="Character not found")
    a, c = _out(row), row_to_dict(char)
    identity = a["data"].get("appearance") or ""
    current = (c.get("appearance") or "").strip()
    outfit = outfit_text(current)
    if not outfit and current and not current.startswith(IDENTITY):
        outfit = await outfit_from_text(c["name"], current)  # prose from Draft Storyline
    appearance = f"{identity.strip()}\n\n{outfit}".strip() if outfit else identity.strip()
    # The project character keeps its own alias/role; empty ones take the library's.
    appearance, alias, role = sync_story_fields(appearance, c.get("alias"), c.get("role"))

    own_prompt = (c.get("consistency_prompt") or "").strip()
    edited = bool(own_prompt) and own_prompt != character_sheet_prompt(c).strip()
    replace = not edited or bool(payload and payload.replace_prompt)
    prompt = character_sheet_prompt({**c, "appearance": appearance}) if replace else own_prompt

    dest = Path(settings.assets_dir) / str(project_id) / "library" / str(asset_id) / "source"
    sources = [a.get("image_path"), *(a["data"].get("source_images") or [])]
    pictures = [p for p in (_copy(x, dest) for x in sources) if p]
    keep = [t for t in c.get("tags") or [] if t.split(":", 1)[0].lower() not in set(IDENTITY_TAGS.values())
            and not t.startswith("library:")]
    tags = json.dumps(character_tags(appearance, [f"library:{asset_id}"]) or keep + [f"library:{asset_id}"],
                      ensure_ascii=False)
    conn = get_db(settings.db_path)
    try:
        conn.execute(
            "UPDATE characters SET appearance = ?, tags = ?, source_images = ?, consistency_prompt = ?, "
            "library_synced_at = ?, alias = ?, role = ? WHERE id = ?",
            (appearance, tags, json.dumps(list(dict.fromkeys(pictures))), prompt, a["updated_at"], alias, role,
             character_id),
        )
        conn.commit()
        out = row_to_dict(conn.execute("SELECT * FROM characters WHERE id = ?", (character_id,)).fetchone())
    finally:
        conn.close()
    return {"character": out, "prompt_kept": not replace}


@router.post("/projects/{project_id}/character/{character_id}/update-from-library")
async def update_from_library(project_id: int, character_id: int, payload: ApplyRequest | None = None) -> dict[str, Any]:
    """Take the linked library identity again (after it changed in the library)."""
    conn = get_db(settings.db_path)
    try:
        row = conn.execute("SELECT tags FROM characters WHERE id = ? AND project_id = ?",
                           (character_id, project_id)).fetchone()
    finally:
        conn.close()
    linked = [t[8:] for t in json.loads(row["tags"] or "[]") if t.startswith("library:") and t[8:].isdigit()] if row else []
    if not linked:
        raise HTTPException(status_code=404, detail="This character is not linked to the library.")
    return await apply_library_identity(int(linked[-1]), project_id, character_id, payload)