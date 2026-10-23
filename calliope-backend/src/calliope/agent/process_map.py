"""A project's process map: the steps Calliope takes it through and what each needs.

Phases follow the work: Story -> Assets -> Script -> Panels (comics) or Video.
Each step (a function you can start in Calliope) says what it needs, what it
produces, which model role and prompts write it, which workflow renders it,
and how to start it (an API call, or the page where it is done by hand). The
status comes from the project's content: done (it produced something),
ready (what it needs is there) or blocked (with what is missing).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from calliope.config import settings
from calliope.db import get_db

PHASES = [
    ("story", "Story", "idea -> storyline beats and cast"),
    ("assets", "Assets", "how characters, places and items look"),
    ("script", "Script", "beats -> scenes -> shots"),
    ("panels", "Panels", "shots drawn as comic panels"),
    ("video", "Video", "shots rendered as video clips"),
]

# key, phase, name, description, needs (counter >= 1), produces, llm role, workflow roles, prompts,
# start action (method, path, body, confirm text) or None, page to open, formats (None = all)
STEPS: list[dict[str, Any]] = [
    {"key": "story.condense", "phase": "story", "name": "Condense novel",
     "description": "Stores a long text (a novel) and replaces the idea with a summary the generators can use.",
     "needs": [], "produces": ["idea"], "done_when": "novel", "role": "writer", "prompts": ["story.condense", "common.json_only"],
     "start": None, "page": "story"},
    {"key": "story.draft", "phase": "story", "name": "Draft storyline",
     "description": "Writes the story beats and the cast (characters, locations, items) from the idea.",
     "needs": ["idea"], "produces": ["beats", "characters", "locations", "items"], "role": "writer",
     "prompts": ["story.system", "story.user"],
     "start": {"method": "POST", "path": "/api/projects/{project}/generate-story", "body": None,
               "confirm": "Replaces the current beats and cast with a new draft."}, "page": "story"},
    {"key": "assets.describe", "phase": "assets", "name": "Write character sheets",
     "description": "Fills each character's sheet (identity and outfit) from notes, pictures or the story.",
     "needs": ["characters"], "produces": ["sheets_text"], "progress": ("sheets_text", "characters"), "role": "assets",
     "prompts": ["assets.designer_system", "assets.describe"], "start": None, "page": "assets"},
    {"key": "assets.library", "phase": "assets", "name": "Use library identities",
     "description": "Gives project characters the look of a library character they match (Library matches).",
     "needs": ["characters"], "produces": ["sheets_text"], "role": "writer",
     "prompts": ["assets.extract_system", "assets.outfit_rules"], "start": None, "page": "assets"},
    {"key": "assets.tags", "phase": "assets", "name": "Suggest tags",
     "description": "Labels characters, locations and items with key:value tags so they can be found.",
     "needs": ["characters"], "produces": ["tags"], "role": "writer", "prompts": ["assets.tags_system"],
     "start": {"method": "POST", "path": "/api/projects/{project}/assets/suggest-tags", "body": None, "confirm": ""},
     "page": "assets"},
    {"key": "assets.render", "phase": "assets", "name": "Render sheets and references",
     "description": "Renders the missing character sheets and location/item reference pictures.",
     "needs": ["characters"], "produces": ["sheets"], "progress": ("sheets", "characters"), "workflows": ["asset_sheet", "asset_location", "asset_item"],
     "prompts": ["assets.sheet_prompt", "assets.sheet_layout"], "style": True,
     "start": {"method": "POST", "path": "/api/projects/{project}/generate-assets", "body": {"missing_only": True},
               "confirm": ""}, "page": "assets"},
    {"key": "script.generate", "phase": "script", "name": "Generate script",
     "description": "Writes the scenes with action and dialogue (in the story's language) from the beats.",
     "needs": ["beats"], "produces": ["scenes"], "role": "writer",
     "prompts": ["script.system", "script.comic_system", "script.user"],
     "start": {"method": "POST", "path": "/api/projects/{project}/generate-script", "body": {"replace": True},
               "confirm": "Replaces the current scenes (and their shots) with a new script."}, "page": "script"},
    {"key": "script.shots", "phase": "script", "name": "Break into shots",
     "description": "Splits every scene into short shots (video clips or comic panels) with their dialogue lines.",
     "needs": ["scenes"], "produces": ["clips"], "role": "writer",
     "prompts": ["script.coverage_system", "script.coverage_user"],
     "start": {"method": "POST", "path": "/api/projects/{project}/expand-clips", "body": {"all": True},
               "confirm": "Breaks every scene into shots again; existing shots of those scenes are replaced."},
     "page": "script"},
    {"key": "panels.plan", "phase": "panels", "name": "Plan panels",
     "description": "One panel per shot: English image prompt, size from the shot, sheet crops as references, "
                    "speech bubbles from the dialogue.", "needs": ["clips"], "produces": ["panels"], "progress": ("panels", "clips"), "role": "writer",
     "prompts": ["panels.prompt", "panels.translate", "panels.no_text"], "style": True,
     "start": {"method": "POST", "path": "/api/projects/{project}/panels/plan", "body": None, "confirm": ""},
     "page": "video", "formats": ("webtoon", "manga")},
    {"key": "panels.render", "phase": "panels", "name": "Render panels",
     "description": "Queues the panels without a picture on the panel workflows (or the render style's).",
     "needs": ["panels"], "produces": ["panel_images"], "progress": ("panel_images", "panels"), "workflows": ["panel_text", "panel_refs_1", "panel_refs_2"],
     "prompts": ["panels.look_webtoon", "panels.look_manga", "panels.frame"], "style": True,
     "start": {"method": "POST", "path": "/api/projects/{project}/panels/render", "body": {"missing_only": True},
               "confirm": ""}, "page": "video", "formats": ("webtoon", "manga")},
    {"key": "panels.export", "phase": "panels", "name": "Export comic",
     "description": "Lays the panels out (webtoon strip or manga pages) and draws the speech bubbles.",
     "needs": ["panel_images"], "produces": ["comic"], "start": {
         "method": "POST", "path": "/api/projects/{project}/comic/export", "body": {"rtl": True}, "confirm": ""},
     "page": "video", "formats": ("webtoon", "manga")},
    {"key": "video.render", "phase": "video", "name": "Render clips",
     "description": "Queues a video job per shot on the clip's workflow; prompts are written per clip.",
     "needs": ["clips"], "produces": ["videos"], "progress": ("videos", "clips"), "role": "video",
     "prompts": ["video.scene_prompt", "video.skill_rewrite", "video.continuity_plan", "video.continuity_critic"],
     "start": None, "page": "video", "formats": ("video",)},
    {"key": "video.export", "phase": "video", "name": "Export film",
     "description": "Joins the rendered clips into one film.", "needs": ["videos"], "produces": ["film"],
     "start": {"method": "POST", "path": "/api/jobs/projects/{project}/export", "body": None, "confirm": ""},
     "page": "video", "formats": ("video",)},
]
LABELS = {
    "idea": "a story idea", "beats": "story beats", "characters": "characters", "locations": "locations",
    "items": "items", "sheets_text": "character sheets (text)", "tags": "tags", "sheets": "rendered sheets",
    "scenes": "scenes", "clips": "shots", "panels": "panels", "panel_images": "rendered panels",
    "comic": "an exported comic", "videos": "rendered clips", "film": "an exported film",
}


def counts(project_id: int) -> dict[str, int]:
    conn = get_db(settings.db_path)
    try:
        one = lambda sql: conn.execute(sql, (project_id,)).fetchone()[0]  # noqa: E731
        project = conn.execute("SELECT idea, source_text, format FROM projects WHERE id = ?", (project_id,)).fetchone()
        sheets = [r[0] for r in conn.execute("SELECT sheet_path FROM characters WHERE project_id = ?", (project_id,))]
        comic_dir = Path(settings.data_dir) / "assets" / str(project_id) / "comic"
        out = {
            "idea": 1 if project and (project["idea"] or "").strip() else 0,
            "novel": 1 if project and (project["source_text"] or "").strip() else 0,
            "beats": one("SELECT COUNT(*) FROM story_beats WHERE project_id = ?"),
            "characters": one("SELECT COUNT(*) FROM characters WHERE project_id = ?"),
            "locations": one("SELECT COUNT(*) FROM locations WHERE project_id = ?"),
            "items": one("SELECT COUNT(*) FROM items WHERE project_id = ?"),
            "sheets_text": one("SELECT COUNT(*) FROM characters WHERE project_id = ? AND appearance LIKE 'CHARACTER INFORMATION%'"),
            "tags": one("SELECT COUNT(*) FROM characters WHERE project_id = ? AND COALESCE(tags, '[]') != '[]'"),
            "sheets": sum(1 for s in sheets if s and Path(s).exists()),
            "scenes": one("SELECT COUNT(*) FROM scenes WHERE project_id = ?"),
            "clips": one("SELECT COUNT(*) FROM clips WHERE project_id = ?"),
            "panels": one("SELECT COUNT(*) FROM panels WHERE project_id = ?"),
            "panel_images": one("SELECT COUNT(*) FROM panels WHERE project_id = ? AND image_path IS NOT NULL"),
            "comic": sum(1 for _ in comic_dir.glob("export*/*")) if comic_dir.exists() else 0,
            "videos": one("SELECT COUNT(*) FROM clips WHERE project_id = ? AND clip_path IS NOT NULL"),
            "film": 0,
            "running": one("SELECT COUNT(*) FROM jobs WHERE project_id = ? AND status IN ('queued', 'running')"),
        }
        out["_format"] = project["format"] if project else "video"  # type: ignore[assignment]
        return out
    finally:
        conn.close()


def _model(role: str | None) -> dict[str, Any] | None:
    if not role:
        return None
    try:
        p = settings.resolve_llm_for_role(role)
    except Exception:  # noqa: BLE001
        return {"role": role, "name": None, "model": None}
    return {"role": role, "name": p.get("name"), "model": p.get("model")}


def _workflows(roles: list[str], style: dict[str, Any] | None) -> list[dict[str, Any]]:
    from calliope.workflow_roles import ROLES, resolve

    conn = get_db(settings.db_path)
    try:
        names = {r[0]: r[1] for r in conn.execute("SELECT id, name FROM workflows")}
    finally:
        conn.close()
    out = []
    for role in roles:
        n = ROLES[role][0]
        styled = (style or {}).get("workflows", {}).get(str(min(n, 2))) if role.startswith(("panel", "asset_sheet")) else None
        wid = styled or resolve(role)
        out.append({"role": role, "label": ROLES[role][1], "workflow_id": wid, "workflow": names.get(wid),
                    "from_style": bool(styled)})
    return out


def build(project_id: int) -> dict[str, Any]:
    from calliope.prompt_library import BY_KEY
    from calliope.render_styles import for_project

    c = counts(project_id)
    fmt = c.pop("_format") or "video"  # type: ignore[assignment]
    style = for_project(project_id)
    # Prompts of the other format are not used by this project.
    skip = {"script.system", "panels.look_webtoon" if fmt == "manga" else "panels.look_manga"} if fmt != "video" \
        else {"script.comic_system"}
    steps = []
    for s in STEPS:
        if s.get("formats") and fmt not in s["formats"]:
            continue
        needs = [{"what": n, "label": LABELS[n], "have": c.get(n, 0)} for n in s["needs"]]
        produced = c.get(s["done_when"], 0) if s.get("done_when") else sum(c.get(p, 0) for p in s["produces"])
        progress = None
        if s.get("progress"):
            have, of = (c.get(k, 0) for k in s["progress"])
            progress = {"have": min(have, of), "of": of}
        status = ("blocked" if any(n["have"] == 0 for n in needs) else
                  "partial" if progress and 0 < progress["have"] < progress["of"] else
                  "done" if produced else "optional" if s.get("done_when") else "ready")
        steps.append({
            "key": s["key"], "phase": s["phase"], "name": s["name"], "description": s["description"],
            "needs": needs, "produces": [{"what": p, "label": LABELS[p], "have": c.get(p, 0)} for p in s["produces"]],
            "status": status, "progress": progress, "model": _model(s.get("role")),
            "workflows": _workflows(s.get("workflows", []), style),
            "prompts": [{"key": k, "name": BY_KEY[k].name, "editable": BY_KEY[k].editable}
                        for k in s.get("prompts", []) if k in BY_KEY and k not in skip],
            "style": ({"id": style["id"], "name": f"{style['category']} · {style['name']}"} if style else {"id": None})
                     if s.get("style") else None,
            "start": ({**s["start"], "path": s["start"]["path"].replace("{project}", str(project_id))}
                      if s.get("start") else None),
            "page": f"/project/{project_id}?stage={s['page']}",
        })
    # Step -> step: what one produces, the next needs.
    edges = []
    for a in steps:
        for b in steps:
            shared = {p["what"] for p in a["produces"]} & {n["what"] for n in b["needs"]}
            if a is not b and shared:
                edges.append({"id": f"{a['key']}>{b['key']}", "source": a["key"], "target": b["key"],
                              "label": ", ".join(LABELS[w] for w in sorted(shared))})
    phases = [{"key": k, "name": n, "hint": h} for k, n, h in PHASES if any(s["phase"] == k for s in steps)]
    return {"format": fmt, "phases": phases, "steps": steps, "edges": edges, "counts": c}
