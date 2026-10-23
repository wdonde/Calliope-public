"""A project's dependency map: everything it is made of and how it hangs together.

Nodes are the project's beats, characters, locations, items, scenes, clips,
panels, the library assets its characters come from, its render style, the
workflows that rendered or will render it and -- on request -- the prompts
that write each kind of node. An edge ``source -> target`` means "target is
built from / uses source", so everything reachable downstream of a node is
what a change to it affects.

:func:`findings` names what looks wrong: assets nothing uses, the same
character or place twice, scenes without shots, panels without a picture or
with missing references, outdated library links, disabled workflows.
:func:`merge` and :func:`unlink` are the fixes the map offers.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from calliope.config import settings
from calliope.db import get_db, row_to_dict

# Columns of the map, left to right (where a node type is drawn).
COLUMNS = {"library": 0, "style": 0, "prompt": 0, "beat": 1, "character": 1, "location": 1, "item": 1,
           "scene": 2, "clip": 3, "panel": 4, "workflow": 5}
# Prompts that write a node type (shown when the map is asked for prompts).
PROMPTS_FOR = {
    "beat": ["story.system"],
    "scene": ["script.system", "script.comic_system"],
    "clip": ["script.coverage_system"],
    "panel": ["panels.no_text", "panels.frame", "panels.translate"],
    "character": ["assets.designer_system", "assets.pictures_rules", "assets.sheet_layout"],
    "location": ["assets.designer_system"],
    "item": ["assets.designer_system"],
}


def _rows(conn, sql: str, *args) -> list[dict[str, Any]]:
    return [row_to_dict(r) for r in conn.execute(sql, args)]


def _mentions(text: str | None, name: str) -> bool:
    """A whole-word mention of the name (or of its last word, for 'Captain Reed')."""
    text = (text or "").lower()
    words = [w for w in re.findall(r"[^\W\d_]+", name.lower()) if len(w) > 2]
    return any(re.search(rf"\b{re.escape(w)}\b", text) for w in ([name.lower()] + words[-1:]))


def build(project_id: int, *, with_prompts: bool = False) -> dict[str, Any]:
    conn = get_db(settings.db_path)
    try:
        project = row_to_dict(conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone())
        beats = _rows(conn, "SELECT * FROM story_beats WHERE project_id = ? ORDER BY order_index", project_id)
        chars = _rows(conn, "SELECT * FROM characters WHERE project_id = ? ORDER BY id", project_id)
        locs = _rows(conn, "SELECT * FROM locations WHERE project_id = ? ORDER BY id", project_id)
        items = _rows(conn, "SELECT * FROM items WHERE project_id = ? ORDER BY id", project_id)
        scenes = _rows(conn, "SELECT * FROM scenes WHERE project_id = ? ORDER BY order_index", project_id)
        clips = _rows(conn, "SELECT c.* FROM clips c JOIN scenes s ON s.id = c.scene_id WHERE c.project_id = ? "
                            "ORDER BY s.order_index, c.order_index", project_id)
        panels = _rows(conn, "SELECT * FROM panels WHERE project_id = ?", project_id)
        cast = _rows(conn, "SELECT sc.* FROM scene_characters sc JOIN scenes s ON s.id = sc.scene_id "
                           "WHERE s.project_id = ?", project_id)
        jobs = _rows(conn, "SELECT id, workflow_id, status, payload_json, clip_id, scene_id FROM jobs "
                           "WHERE project_id = ? ORDER BY id", project_id)
        workflows = {w["id"]: w for w in _rows(conn, "SELECT id, name, is_enabled FROM workflows")}
        library = {a["id"]: a for a in _rows(conn, "SELECT id, name, updated_at, image_path FROM library_assets")}
    finally:
        conn.close()
    if project is None:
        raise KeyError(project_id)
    from calliope.render_styles import for_project

    style = for_project(project_id)
    nodes: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, Any]] = []

    def node(nid: str, kind: str, label: str, **extra: Any) -> None:
        nodes[nid] = {"id": nid, "kind": kind, "label": label, "column": COLUMNS[kind], **extra}

    def edge(src: str, dst: str, rel: str, **extra: Any) -> None:
        if src in nodes and dst in nodes and not any(e["source"] == src and e["target"] == dst for e in edges):
            edges.append({"id": f"{src}>{dst}", "source": src, "target": dst, "rel": rel, **extra})

    for b in beats:
        node(f"beat:{b['id']}", "beat", f"{b['order_index']}. {b['title']}", entity_id=b["id"],
             link=f"/project/{project_id}?stage=story")
    for c in chars:
        tags = json.loads(c["tags"]) if isinstance(c.get("tags"), str) else (c.get("tags") or [])
        node(f"character:{c['id']}", "character", c["name"], entity_id=c["id"], image=c.get("sheet_path"),
             link=f"/project/{project_id}?stage=assets", alias=c.get("alias"), tags=tags,
             has_sheet=bool(c.get("sheet_path") and Path(c["sheet_path"]).exists()),
             library_synced_at=c.get("library_synced_at"))
        for t in tags:
            if t.startswith("library:") and t[8:].isdigit() and int(t[8:]) in library:
                a = library[int(t[8:])]
                node(f"library:{a['id']}", "library", a["name"], entity_id=a["id"], image=a.get("image_path"),
                     link="/library", updated_at=a.get("updated_at"))
                edge(f"library:{a['id']}", f"character:{c['id']}", "identity",
                     outdated=bool(c.get("library_synced_at")) and (a.get("updated_at") or "") > c["library_synced_at"])
    for loc in locs:
        node(f"location:{loc['id']}", "location", loc["name"], entity_id=loc["id"],
             image=loc.get("reference_image_path"), link=f"/project/{project_id}?stage=assets")
    for it in items:
        node(f"item:{it['id']}", "item", it["name"], entity_id=it["id"], image=it.get("reference_image_path"),
             link=f"/project/{project_id}?stage=assets")
    for s in scenes:
        node(f"scene:{s['id']}", "scene", f"#{s['order_index']} {s.get('heading') or ''}".strip(), entity_id=s["id"],
             link=f"/project/{project_id}?stage=script")
        if s.get("beat_id"):
            edge(f"beat:{s['beat_id']}", f"scene:{s['id']}", "beat")
        if s.get("location_id"):
            edge(f"location:{s['location_id']}", f"scene:{s['id']}", "location", configurable="scene_location")
        text = " ".join(str(s.get(k) or "") for k in ("heading", "action", "dialog"))
        for it in items:
            if _mentions(text, it["name"]):
                edge(f"item:{it['id']}", f"scene:{s['id']}", "mentioned")
    for sc in cast:
        edge(f"character:{sc['character_id']}", f"scene:{sc['scene_id']}", "cast", configurable="scene_character")
    panel_by_clip = {p["clip_id"]: p for p in panels}
    sheet_stems = {Path(c["sheet_path"]).stem: c["id"] for c in chars if c.get("sheet_path")}
    for c in clips:
        node(f"clip:{c['id']}", "clip", f"{c['order_index']}. {(c.get('description') or '')[:40]}", entity_id=c["id"],
             link=f"/project/{project_id}?stage={'script' if c['id'] in panel_by_clip else 'video'}")
        edge(f"scene:{c['scene_id']}", f"clip:{c['id']}", "shot")
        p = panel_by_clip.get(c["id"])
        if p:
            refs = json.loads(p.get("refs_json") or "[]")
            node(f"panel:{p['id']}", "panel", f"Panel {c['order_index']} · scene {c['scene_id']}", entity_id=p["id"],
                 image=p.get("image_path"), link=f"/project/{project_id}?stage=video",
                 rendered=bool(p.get("image_path")), missing_refs=[r for r in refs if not Path(r).exists()])
            edge(f"clip:{c['id']}", f"panel:{p['id']}", "panel")
            for r in refs:
                stem = Path(r).stem.rsplit("_", 1)[0]
                if stem in sheet_stems:
                    edge(f"character:{sheet_stems[stem]}", f"panel:{p['id']}", "reference")
    # Workflows: what rendered each thing, and what the render style brings.
    def wf_node(wid: int | None) -> str | None:
        w = workflows.get(wid) if wid else None
        if not w:
            return None
        node(f"workflow:{w['id']}", "workflow", w["name"], entity_id=w["id"], enabled=bool(w["is_enabled"]),
             link="/settings?tab=workflows")
        return f"workflow:{w['id']}"

    panel_by_job = {p["job_id"]: p for p in panels if p.get("job_id")}
    for j in jobs:
        wid = wf_node(j.get("workflow_id"))
        if not wid:
            continue
        payload = json.loads(j.get("payload_json") or "{}")
        if j["id"] in panel_by_job:
            edge(wid, f"panel:{panel_by_job[j['id']]['id']}", "rendered")
        elif payload.get("character_id"):
            edge(wid, f"character:{payload['character_id']}", "rendered")
        elif payload.get("location_id"):
            edge(wid, f"location:{payload['location_id']}", "rendered")
        elif j.get("clip_id"):
            edge(wid, f"clip:{j['clip_id']}", "rendered")
    if style:
        node(f"style:{style['id']}", "style", f"{style['category']} · {style['name']}", entity_id=style["id"],
             link="/settings?tab=styles")
        for n, wid in style.get("workflows", {}).items():
            w = wf_node(wid)
            if w:
                edge(f"style:{style['id']}", w, f"{n} refs")
        # The style reaches every panel through its workflows; one line per panel would bury the map.
    if with_prompts:
        from calliope.prompt_library import BY_KEY

        for kind, keys in PROMPTS_FOR.items():
            targets = [n for n in nodes.values() if n["kind"] == kind]
            for key in keys:
                if not targets or key not in BY_KEY:
                    continue
                node(f"prompt:{key}", "prompt", BY_KEY[key].name, key=key, link="/settings?tab=prompts")
                for t in targets:
                    edge(f"prompt:{key}", t["id"], "writes")
    return {"project": {"id": project_id, "title": project["title"], "format": project.get("format")},
            "nodes": list(nodes.values()), "edges": edges,
            "findings": findings(list(nodes.values()), edges, chars, locs, clips, panel_by_clip, project)}


def _same_name(a: dict[str, Any], b: dict[str, Any]) -> bool:
    from calliope.agent.character_identity import _names

    def tags(x: dict[str, Any]) -> list[str]:
        t = x.get("tags")
        t = json.loads(t) if isinstance(t, str) else (t or [])
        return t + [f"alias:{x['alias']}"] if x.get("alias") else t

    return bool(_names(a["name"], tags(a)) & _names(b["name"], tags(b)))


def findings(nodes: list[dict[str, Any]], edges: list[dict[str, Any]], chars, locs, clips, panel_by_clip,
             project) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    used = {e["source"] for e in edges if e["rel"] in ("cast", "location", "mentioned", "reference")}
    for n in nodes:
        if n["kind"] in ("character", "location", "item") and n["id"] not in used:
            out.append({"type": "unused", "severity": "info", "nodes": [n["id"]],
                        "text": f"{n['kind'].title()} '{n['label']}' is not used in any scene.",
                        "action": "delete"})
        if n["kind"] == "character" and n["id"] in used and not n.get("has_sheet"):
            out.append({"type": "no_sheet", "severity": "warn", "nodes": [n["id"]],
                        "text": f"'{n['label']}' appears in scenes but has no sheet -- its panels render without a reference."})
        if n["kind"] == "panel" and not n.get("rendered"):
            out.append({"type": "not_rendered", "severity": "info", "nodes": [n["id"]], "text": f"{n['label']} is not rendered yet."})
        if n["kind"] == "panel" and n.get("missing_refs"):
            out.append({"type": "missing_refs", "severity": "warn", "nodes": [n["id"]],
                        "text": f"{n['label']} points to {len(n['missing_refs'])} reference picture(s) that no longer exist -- re-plan the panel."})
        if n["kind"] == "workflow" and not n.get("enabled"):
            out.append({"type": "disabled_workflow", "severity": "warn", "nodes": [n["id"]],
                        "text": f"Workflow '{n['label']}' is disabled; what it rendered stays, new renders take another one."})
    for e in edges:
        if e["rel"] == "identity" and e.get("outdated"):
            out.append({"type": "library_outdated", "severity": "warn", "nodes": [e["source"], e["target"]],
                        "text": "The library character changed after it was applied -- update from library.",
                        "action": "update_from_library"})
    for group, kind in ((chars, "character"), (locs, "location")):
        for i, a in enumerate(group):
            for b in group[i + 1:]:
                same = _same_name(a, b) if kind == "character" else a["name"].strip().lower() == b["name"].strip().lower()
                if same:
                    out.append({"type": "duplicate", "severity": "warn", "nodes": [f"{kind}:{a['id']}", f"{kind}:{b['id']}"],
                                "text": f"'{a['name']}' and '{b['name']}' look like the same {kind}.", "action": "merge"})
    shots = {e["source"] for e in edges if e["rel"] == "shot"}
    for n in nodes:
        if n["kind"] == "scene" and n["id"] not in shots:
            out.append({"type": "no_shots", "severity": "info", "nodes": [n["id"]],
                        "text": f"Scene {n['label']} is not broken into shots yet."})
    if (project.get("format") or "video") != "video":
        for c in clips:
            if c["id"] not in panel_by_clip:
                out.append({"type": "no_panel", "severity": "info", "nodes": [f"clip:{c['id']}"],
                            "text": f"Clip {c['order_index']} of scene {c['scene_id']} has no panel -- add panels for new clips."})
    order = {"warn": 0, "info": 1}
    return sorted(out, key=lambda f: order[f["severity"]])


def unlink(project_id: int, kind: str, source_id: int, target_id: int) -> None:
    """Remove a configurable connection: a character from a scene, a location from a scene."""
    conn = get_db(settings.db_path)
    try:
        scene = conn.execute("SELECT id FROM scenes WHERE id = ? AND project_id = ?", (target_id, project_id)).fetchone()
        if not scene:
            raise KeyError(target_id)
        if kind == "scene_character":
            conn.execute("DELETE FROM scene_characters WHERE scene_id = ? AND character_id = ?", (target_id, source_id))
        elif kind == "scene_location":
            conn.execute("UPDATE scenes SET location_id = NULL WHERE id = ? AND location_id = ?", (target_id, source_id))
        else:
            raise ValueError(kind)
        conn.commit()
    finally:
        conn.close()


def link(project_id: int, kind: str, source_id: int, target_id: int) -> None:
    conn = get_db(settings.db_path)
    try:
        scene = conn.execute("SELECT id FROM scenes WHERE id = ? AND project_id = ?", (target_id, project_id)).fetchone()
        if not scene:
            raise KeyError(target_id)
        if kind == "scene_character":
            conn.execute("INSERT OR IGNORE INTO scene_characters (scene_id, character_id) VALUES (?, ?)", (target_id, source_id))
        elif kind == "scene_location":
            conn.execute("UPDATE scenes SET location_id = ? WHERE id = ?", (source_id, target_id))
        else:
            raise ValueError(kind)
        conn.commit()
    finally:
        conn.close()


def merge(project_id: int, kind: str, keep_id: int, drop_id: int) -> dict[str, Any]:
    """Fold a duplicate into the one to keep: its scenes move over, its name becomes
    an alias (characters), its tags and pictures join; then it is deleted."""
    table = {"character": "characters", "location": "locations"}[kind]
    conn = get_db(settings.db_path)
    try:
        keep = conn.execute(f"SELECT * FROM {table} WHERE id = ? AND project_id = ?", (keep_id, project_id)).fetchone()
        drop = conn.execute(f"SELECT * FROM {table} WHERE id = ? AND project_id = ?", (drop_id, project_id)).fetchone()
        if not keep or not drop or keep_id == drop_id:
            raise KeyError(drop_id)
        keep, drop = row_to_dict(keep), row_to_dict(drop)
        tags = list(dict.fromkeys([*(keep.get("tags") or []), *(drop.get("tags") or [])]))
        pictures = list(dict.fromkeys([*(keep.get("source_images") or []), *(drop.get("source_images") or [])]))
        sets: dict[str, Any] = {"tags": json.dumps(tags, ensure_ascii=False), "source_images": json.dumps(pictures)}
        if kind == "character":
            aliases = [a.strip() for a in (keep.get("alias") or "").split(",") if a.strip()]
            for extra in [drop["name"], *(a.strip() for a in (drop.get("alias") or "").split(","))]:
                if extra and extra.lower() != keep["name"].lower() and extra not in aliases:
                    aliases.append(extra)
            sets["alias"] = ", ".join(aliases)
            for col in ("sheet_path", "portrait_path", "sd_tags"):
                if not keep.get(col) and drop.get(col):
                    sets[col] = drop[col]
            conn.execute("INSERT OR IGNORE INTO scene_characters (scene_id, character_id) "
                         "SELECT scene_id, ? FROM scene_characters WHERE character_id = ?", (keep_id, drop_id))
            conn.execute("DELETE FROM scene_characters WHERE character_id = ?", (drop_id,))
        else:
            if not keep.get("reference_image_path") and drop.get("reference_image_path"):
                sets["reference_image_path"] = drop["reference_image_path"]
            conn.execute("UPDATE scenes SET location_id = ? WHERE location_id = ?", (keep_id, drop_id))
        conn.execute(f"UPDATE {table} SET {', '.join(f'{k} = ?' for k in sets)} WHERE id = ?", (*sets.values(), keep_id))
        conn.execute(f"DELETE FROM {table} WHERE id = ?", (drop_id,))
        conn.commit()
        return row_to_dict(conn.execute(f"SELECT * FROM {table} WHERE id = ?", (keep_id,)).fetchone())
    finally:
        conn.close()
