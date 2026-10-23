"""Colorize a black-and-white picture (manga panel, ink drawing) with Klein edit.

The picture goes in as the single reference of a one-reference image workflow;
the prompt keeps line art and composition and only adds color. Colors come
from the user's notes and, optionally, from the character sheets of a project
(skin, eyes, hair, outfit, palette), so the same character gets the same
colors in every picture. The output keeps the picture's aspect ratio.
"""
from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any

from PIL import Image

from calliope.prompt_library import text as prompt_text
from calliope.agent.character_identity import sections
from calliope.comfyui.parser import parse_dynamic_inputs
from calliope.config import settings
from calliope.db import get_db, row_to_dict
from calliope.queue.manager import queue_manager

COLOR_FIELDS = ("Skin", "Eyes", "Hair", "Headwear", "Outerwear", "Top", "Bottom", "Footwear", "Color palette")
MEGAPIXELS = 1.0
COLORIZE_BASE = (
    "Colorize this black-and-white manga/comic picture. Keep every line, the composition, faces, poses, "
    "hands, background and all details exactly as they are -- only add color. Replace screentone dots "
    "and hatching with smooth, clean color shading. Full color, natural skin tones, believable light, "
    "vivid but natural palette, cel-shaded anime/comic coloring. Do not add, remove or move anything; "
    "no text, no speech bubbles, no frame."
)


def output_size(path: str) -> tuple[int, int]:
    """About one megapixel in the picture's aspect ratio, multiples of 16."""
    with Image.open(path) as im:
        w, h = im.size
    scale = (MEGAPIXELS * 1_000_000 / max(1, w * h)) ** 0.5
    return max(256, round(w * scale / 16) * 16), max(256, round(h * scale / 16) * 16)


def character_colors(project_id: int, character_ids: list[int] | None) -> list[str]:
    """One line per character with the color-bearing fields of its sheet."""
    conn = get_db(settings.db_path)
    try:
        rows = [row_to_dict(r) for r in conn.execute("SELECT * FROM characters WHERE project_id = ?", (project_id,))]
    finally:
        conn.close()
    out = []
    for c in rows:
        if character_ids is not None and c["id"] not in character_ids:
            continue
        lines = [line for part in sections(c.get("appearance")).values() for line in part]
        colors = [line for line in lines if line.split(":", 1)[0].strip() in COLOR_FIELDS]
        if colors:
            out.append(f"{c['name']}: " + "; ".join(colors))
        elif (c.get("appearance") or "").strip():
            out.append(f"{c['name']}: {c['appearance'].strip()[:300]}")
    return out


def colorize_prompt(notes: str, colors: list[str]) -> str:
    parts = [prompt_text("assets.colorize")]
    if notes.strip():
        parts.append(f"Colors to use: {notes.strip()}")
    if colors:
        parts.append("If these characters appear, give them exactly these colors:\n" + "\n".join(f"- {c}" for c in colors))
    return "\n\n".join(parts)


def _one_ref_workflow() -> int | None:
    from calliope.workflow_roles import resolve

    return resolve("colorize")


def colorize(image_path: str, *, notes: str = "", project_id: int | None = None,
             character_ids: list[int] | None = None) -> dict[str, Any]:
    """Queue the colorize render; the result lands in the project's (or the
    Playground's) outputs like any other image job."""
    wf = _one_ref_workflow()
    if wf is None:
        raise ValueError("No enabled image workflow with one image input (e.g. FLUX.2 Klein edit).")
    colors = character_colors(project_id, character_ids) if project_id else []
    prompt = colorize_prompt(notes, colors)
    width, height = output_size(image_path)
    conn = get_db(settings.db_path)
    try:
        wf_json = json.loads(conn.execute("SELECT workflow_json FROM workflows WHERE id = ?", (wf,)).fetchone()[0])
        if project_id is None:
            from calliope.routers.playground import ensure_playground_project

            target = ensure_playground_project(conn)
        else:
            target = project_id
    finally:
        conn.close()
    values: dict[str, Any] = {}
    for inp in parse_dynamic_inputs(wf_json):
        node, role = str(inp["nodeId"]), inp.get("role")
        if inp.get("kind") == "image":
            values[node] = image_path
        elif role == "prompt":
            values[node] = prompt
        elif role == "width":
            values[node] = width
        elif role == "height":
            values[node] = height
        elif role == "seed":
            values[node] = random.randint(1, 2**31 - 1)
    return queue_manager.enqueue(project_id=target, kind="image", workflow_id=wf,
                                 payload={"input_values": values, "colorize_source": str(Path(image_path)),
                                          "prompt": prompt})
