"""Which ComfyUI workflow does which job -- like the agent model roles for LLMs.

Each role needs a workflow with a fixed number of image inputs (references).
Settings > Workflows assigns one per role; "Auto" (no assignment) keeps the
built-in choice: an enabled image workflow with that many image inputs,
FLUX.2 Klein first. An assignment that no longer fits (deleted, disabled,
other input count) falls back to Auto.
"""
from __future__ import annotations

import json
from typing import Any

from calliope.comfyui.parser import parse_dynamic_inputs
from calliope.config import settings
from calliope.db import get_db, row_to_dict

# key -> (image inputs the workflow must have, label, hint)
ROLES: dict[str, tuple[int, str, str]] = {
    "asset_sheet": (0, "Character sheet (text)", "Assets: a character sheet from its description."),
    "asset_location": (0, "Location reference (text)", "Assets: a location reference image."),
    "asset_item": (0, "Item reference (text)", "Assets: an item reference image."),
    "asset_refs_1": (1, "Asset from 1 picture", "New asset / re-render with one own picture."),
    "asset_refs_2": (2, "Asset from 2 pictures", "New asset / re-render with two own pictures (color + full body)."),
    **{f"asset_refs_{n}": (n, f"Asset from {n} pictures", f"New asset / re-render with {n} own pictures "
                           "(image 1 color face, image 2 full body, the rest more views).") for n in range(3, 7)},
    "panel_text": (0, "Comic panel (no reference)", "Panels without a character sheet."),
    "panel_refs_1": (1, "Comic panel (1 reference)", "Panels with one character reference."),
    "panel_refs_2": (2, "Comic panel (2 references)", "Panels with two references (two characters, or body + face)."),
    "colorize": (1, "Colorize", "Colorize a black-and-white picture."),
}


def _image_workflows() -> list[dict[str, Any]]:
    conn = get_db(settings.db_path)
    try:
        rows = [row_to_dict(r) for r in conn.execute(
            "SELECT id, name, is_enabled, workflow_json FROM workflows WHERE kind = 'image' ORDER BY id")]
    finally:
        conn.close()
    for wf in rows:
        inputs = parse_dynamic_inputs(json.loads(wf.pop("workflow_json") or "{}"))
        wf["images"] = sum(1 for i in inputs if i.get("kind") == "image")
    return rows


def auto(n_images: int, workflows: list[dict[str, Any]] | None = None) -> int | None:
    fits = [w for w in workflows or _image_workflows() if w["is_enabled"] and w["images"] == n_images]
    fits.sort(key=lambda w: ("klein" not in w["name"].lower(), w["id"]))
    return fits[0]["id"] if fits else None


def resolve(role: str) -> int | None:
    """The workflow for a role: the assigned one when it still fits, else Auto."""
    n_images = ROLES[role][0]
    workflows = _image_workflows()
    assigned = (settings.workflow_assignments or {}).get(role)
    if assigned and any(w["id"] == assigned and w["is_enabled"] and w["images"] == n_images for w in workflows):
        return assigned
    return auto(n_images, workflows)


def overview() -> list[dict[str, Any]]:
    """Every role with its assignment, what it resolves to, and the workflows that fit."""
    workflows = _image_workflows()
    out = []
    for key, (n_images, label, hint) in ROLES.items():
        fits = [{"id": w["id"], "name": w["name"]} for w in workflows if w["is_enabled"] and w["images"] == n_images]
        assigned = (settings.workflow_assignments or {}).get(key)
        resolved = assigned if any(f["id"] == assigned for f in fits) else auto(n_images, workflows)
        out.append({"key": key, "label": label, "hint": hint, "images": n_images, "assigned": assigned,
                    "resolved": resolved, "candidates": fits})
    return out
