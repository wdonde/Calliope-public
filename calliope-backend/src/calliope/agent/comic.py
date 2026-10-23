"""Comic projects (webtoon strip / manga pages): one panel per clip.

Story, script and assets are shared with video projects; this module takes
over where a video project renders clips:

* :func:`plan_panels` -- a panels row per clip: an English image prompt
  (the story text keeps its language in the speech bubbles), the size from
  the shot, cropped character sheets as references, bubbles from the clip's
  dialogue lines, a place/time caption on each scene's first panel.
* :func:`render_panels` -- queues image jobs on the workflow that fits the
  number of references; the worker writes the result back via ``panel_id``.
* :func:`export_comic` -- lays the panels out (vertical strip, or A4 pages
  cut by agent/manga_layout.py, right-to-left for manga) and draws the
  bubbles: the text is set by code, never painted by the model.
* :func:`get_layout` / :func:`save_layout` -- manga pages are chosen
  automatically; the user can move panels and pick other layouts.
"""
from __future__ import annotations

import json
import random
import re
import textwrap
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from calliope.prompt_library import text as prompt_text
from calliope.activity import tracked
from calliope.agent import asset_tags, manga_layout
from calliope.agent.llm import LLMClient
from calliope.agent.prompts import guess_language
from calliope.comfyui.parser import parse_dynamic_inputs
from calliope.config import settings
from calliope.db import get_db, row_to_dict
from calliope.queue.manager import queue_manager

FORMATS = ("webtoon", "manga")
STYLES = {
    "webtoon": ("Single full-frame illustration of one continuous scene, Korean webtoon art style, "
                "full-color digital painting, clean line art, cel shading, dramatic cinematic lighting"),
    "manga": ("Single full-frame illustration of one continuous scene, black-and-white Japanese manga art, "
              "detailed ink line art, screentone shading, high contrast, dramatic composition, no color"),
}
FRAME = "Single full-frame illustration of one continuous scene"
TRANSLATE = "Translate into English for an image-generation prompt. Output only the translation."
NO_TEXT = "Plain clothing without lettering or name tags. Clean image without any text, speech bubbles or frame."
SIZES = {"wide": (1216, 704), "medium": (1024, 1024), "closeUp": (896, 1152),
         "insert": (1024, 768), "overShoulder": (1024, 1024)}
# Where Calliope's character-sheet template puts each view (fractions of the image).
SHEET_CROPS = {"front": (0.0, 0.0, 0.34, 0.97), "head": (0.66, 0.6, 1.0, 1.0)}
FONTS = {
    "bubble": ["/mnt/c/Windows/Fonts/comicbd.ttf", "C:/Windows/Fonts/comicbd.ttf",
               "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"],
    "caption": ["/mnt/c/Windows/Fonts/arialbd.ttf", "C:/Windows/Fonts/arialbd.ttf",
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"],
}


def comic_dir(project_id: int) -> Path:
    return Path(settings.data_dir) / "assets" / str(project_id) / "comic"


# ── plan ──────────────────────────────────────────────────────────────────

def _style_head(project: dict[str, Any], style: dict[str, Any] | None) -> str:
    """What leads a panel prompt: the project's render style, else the format's built-in look."""
    if style and style.get("style_prompt", "").strip():
        return f"{style['style_prompt'].strip().rstrip('.')}. {prompt_text('panels.frame')}"
    fmt = project.get("format") if project.get("format") in STYLES else "webtoon"
    return prompt_text(f"panels.look_{fmt}")


def _restyle(prompt: str, head: str) -> str:
    """A planned prompt with its style head swapped for the current one (the style may have changed)."""
    from calliope.render_styles import list_styles

    frame = prompt_text("panels.frame")
    known = [*STYLES.values(), *(prompt_text(f"panels.look_{f}") for f in STYLES),
             *(f"{s['style_prompt'].strip().rstrip('.')}. {fr}" for s in list_styles() for fr in {FRAME, frame})]
    for old in sorted(known, key=len, reverse=True):
        if old and prompt.startswith(old):
            return head + prompt[len(old):]
    return prompt if prompt.startswith(head) else f"{head}. {prompt}"


def _clean_description(desc: str) -> str:
    desc = re.sub(r"^\w+ shot[:.]\s*", "", desc.strip(), flags=re.I)
    # quoted speech would be painted as text
    desc = re.sub(r"[:,]?\s*(?<!\w)['\"\u201c\u201e\u2018][^'\"\u201c\u201d\u2018\u2019]{8,}['\"\u201d\u2019](?!\w)", "", desc)
    desc = re.sub(r",?\s*(anime style|cinematic 3d cgi)[^.]*", "", desc, flags=re.I)
    return desc.strip().rstrip(".")


async def _to_english(text: str) -> str:
    """Image prompts are English; the story's own language stays in the bubbles."""
    if not text or guess_language(text) in (None, "English"):
        return text
    client = LLMClient.for_role("writer", timeout=120.0)
    try:
        out = await client.chat([
            {"role": "user", "content": prompt_text("panels.translate") + "\n\n" + text},
        ], temperature=0.2)
        return out.strip() or text
    except Exception:  # noqa: BLE001 -- an untranslated prompt still renders
        return text
    finally:
        await client.close()


def _sheet_crop(project_id: int, sheet: str, view: str) -> str:
    target = comic_dir(project_id) / "refs" / f"{Path(sheet).stem}_{view}.png"
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        im = Image.open(sheet).convert("RGB")
        x0, y0, x1, y1 = SHEET_CROPS[view]
        im.crop((int(x0 * im.width), int(y0 * im.height), int(x1 * im.width), int(y1 * im.height))).save(target)
    return str(target)


# Words in a name that say nothing about who it is (titles, ranks, articles).
NAME_FILLERS = {
    "captain", "capt", "lieutenant", "lt", "private", "pvt", "sergeant", "sgt", "major", "colonel",
    "general", "commander", "admiral", "corporal", "officer", "agent", "doctor", "dr", "mr", "mrs",
    "ms", "miss", "sir", "lady", "lord", "king", "queen", "prince", "princess", "professor", "prof",
    "the", "der", "die", "das", "le", "la", "el", "von", "van", "de",
}
# Clothing that overrides the reference outfit when a shot asks for it.
OUTFIT_WORDS = ("helmet", "hat", "cap", "hood", "mask", "visor", "armor", "armour", "spacesuit",
                "gown", "dress", "robe", "cloak")


def _named_in(desc: str, name: str) -> bool:
    """Whether the description mentions the character by a real part of the name."""
    keys = [w for w in re.findall(r"[^\W\d_]+", name.lower()) if len(w) > 2 and w not in NAME_FILLERS]
    text = desc.lower()
    return any(re.search(rf"\b{re.escape(k)}\b", text) for k in keys)


def _panel_characters(desc: str, scene_chars: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    """(characters in focus with a sheet -- at most 2 --, names of everyone else in the shot).
    With nobody named, the scene's only character is in focus."""
    named = [c for c in scene_chars
             if any(_named_in(desc, n) for n in [c["name"], *asset_tags.aliases(c)])]
    if not named and len(scene_chars) == 1:
        named = scene_chars
    focus = [c for c in named if c.get("sheet_path") and Path(c["sheet_path"]).exists()][:2]
    others = [c["name"] for c in named if c not in focus]
    return focus, others


def _reference_hint(desc: str, who: list[dict[str, Any]], others: list[str]) -> tuple[list[tuple[str, str]], str]:
    """Which sheet views go in as references, and the sentence that binds them.
    One person gets body and face; two get one view each."""
    def keeps(c: dict[str, Any]) -> str:
        own = (c.get("appearance") or "").lower()
        changed = [w for w in OUTFIT_WORDS if re.search(rf"\b{w}s?\b", desc.lower()) and w not in own]
        return "same face and hair; clothing as described here" if changed else "same face, hair and outfit"

    if len(who) == 1:
        c = who[0]
        views = [(c["sheet_path"], "front"), (c["sheet_path"], "head")]
        hint = (f"{c['name']} looks exactly like reference images 1 (full body) and 2 (face) -- {keeps(c)} -- "
                "shown once in a new pose.")
    else:
        views, parts = [], []
        for k, c in enumerate(who):
            views.append((c["sheet_path"], "front"))
            parts.append(f"{c['name']} looks exactly like reference image {k + 1} ({keeps(c)}).")
        hint = " ".join(parts)
    if who:
        hint += f" Exactly {len(who)} named {'person' if len(who) == 1 else 'people'} in focus, each shown once."
    if others:
        hint += f" Anyone else ({', '.join(others)}) stays small in the background, faces not in focus."
    return views, hint + " " if hint else ""


def _caption(heading: str | None) -> str:
    return re.sub(r"^(INT|EXT)\.?\s*", "", heading or "").strip().replace(" - ", " – ")


@tracked("panels", "Planning panels")
async def plan_panels(project_id: int, *, replan: bool = False) -> list[dict[str, Any]]:
    """Create (or with ``replan`` rebuild) a panel for every clip, in story order."""
    conn = get_db(settings.db_path)
    try:
        project = row_to_dict(conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone())
        from calliope.render_styles import for_project

        style = _style_head(project, for_project(project_id))
        scenes = [row_to_dict(r) for r in conn.execute(
            "SELECT * FROM scenes WHERE project_id = ? ORDER BY order_index", (project_id,))]
        existing = {r["clip_id"]: row_to_dict(r) for r in conn.execute(
            "SELECT * FROM panels WHERE project_id = ?", (project_id,))}
        for scene in scenes:
            chars = [row_to_dict(r) for r in conn.execute(
                "SELECT c.* FROM characters c JOIN scene_characters sc ON sc.character_id = c.id "
                "WHERE sc.scene_id = ?", (scene["id"],))]
            lines = [l for l in (scene.get("dialog") or "").splitlines() if l.strip()]
            clips = [row_to_dict(r) for r in conn.execute(
                "SELECT * FROM clips WHERE scene_id = ? ORDER BY order_index", (scene["id"],))]
            for i, clip in enumerate(clips):
                if clip["id"] in existing and not replan:
                    continue
                raw = clip.get("description") or scene.get("action") or ""
                desc = await _to_english(_clean_description(raw))
                who, others = _panel_characters(desc, chars)
                views, ref_hint = _reference_hint(desc, who, others)
                refs = [_sheet_crop(project_id, sheet, view) for sheet, view in views]
                covered = clip.get("dialog_lines_covered") or []
                if isinstance(covered, str):
                    covered = json.loads(covered or "[]")
                bubbles = []
                for n in covered:
                    if isinstance(n, int) and 1 <= n <= len(lines):
                        speaker, _, text = lines[n - 1].partition(":")
                        text = re.sub(r"^\s*\([^)]*\)\s*", "", text).strip().strip('"„“\'')
                        bubbles.append({"speaker": speaker.strip(), "text": text, "x": None, "y": None})
                w, h = SIZES.get(clip.get("shot_size") or "", SIZES["medium"])
                values = (project_id, clip["id"], f"{style}. {desc}. {ref_hint}{prompt_text("panels.no_text")}", w, h,
                          json.dumps(refs), json.dumps(bubbles, ensure_ascii=False),
                          _caption(scene.get("heading")) if i == 0 else None)
                conn.execute(
                    """
                    INSERT INTO panels (project_id, clip_id, prompt, width, height, refs_json, bubbles_json, caption)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(clip_id) DO UPDATE SET prompt = excluded.prompt, width = excluded.width,
                        height = excluded.height, refs_json = excluded.refs_json,
                        bubbles_json = excluded.bubbles_json, caption = excluded.caption,
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    values,
                )
                conn.commit()
        return list_panels(project_id, conn=conn)
    finally:
        conn.close()


def list_panels(project_id: int, *, conn=None) -> list[dict[str, Any]]:
    """Panels in story order, with their render job's status."""
    own = conn is None
    conn = conn or get_db(settings.db_path)
    try:
        rows = conn.execute(
            """
            SELECT p.*, j.status AS job_status, j.error AS job_error, c.shot_size,
                   s.order_index AS scene_order, c.order_index AS clip_order
            FROM panels p
            JOIN clips c ON c.id = p.clip_id
            JOIN scenes s ON s.id = c.scene_id
            LEFT JOIN jobs j ON j.id = p.job_id
            WHERE p.project_id = ?
            ORDER BY s.order_index, c.order_index
            """,
            (project_id,),
        ).fetchall()
        out = []
        for r in rows:
            d = row_to_dict(r)
            d["refs"] = json.loads(d.pop("refs_json") or "[]")
            d["bubbles"] = json.loads(d.pop("bubbles_json") or "[]")
            out.append(d)
        return out
    finally:
        if own:
            conn.close()


# ── render ────────────────────────────────────────────────────────────────

def _pick_workflow(n_refs: int, style: dict[str, Any] | None = None) -> int | None:
    """The panel workflow for this many references: the project's render style,
    else Settings > Workflows roles, else Auto."""
    from calliope.render_styles import workflow_for

    return workflow_for(style, n_refs, {0: "panel_text", 1: "panel_refs_1", 2: "panel_refs_2"}[min(n_refs, 2)])


def _image_node_ids(workflow_id: int) -> list[str]:
    conn = get_db(settings.db_path)
    try:
        wf = row_to_dict(conn.execute("SELECT workflow_json FROM workflows WHERE id = ?", (workflow_id,)).fetchone())
    finally:
        conn.close()
    return [str(i["nodeId"]) for i in parse_dynamic_inputs(json.loads(wf["workflow_json"])) if i.get("kind") == "image"]


def render_panels(project_id: int, *, panel_ids: list[int] | None = None, missing_only: bool = True,
                  reseed: bool = False) -> list[dict[str, Any]]:
    """Queue an image job per panel; returns the queued jobs."""
    from calliope.render_styles import character_tag_text, for_project, negative_values, uses_tags

    conn = get_db(settings.db_path)
    try:
        project = row_to_dict(conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone())
    finally:
        conn.close()
    style = for_project(project_id)
    head = _style_head(project, style)
    tagged = []  # SDXL-like styles: characters named in a panel get their tags
    if uses_tags(style):
        conn = get_db(settings.db_path)
        try:
            tagged = [row_to_dict(r) for r in conn.execute(
                "SELECT * FROM characters WHERE project_id = ? AND COALESCE(sd_tags, '') != ''", (project_id,))]
        finally:
            conn.close()
    jobs = []
    for p in list_panels(project_id):
        if panel_ids and p["id"] not in panel_ids:
            continue
        if missing_only and not panel_ids and p.get("image_path"):
            continue
        refs = [r for r in p["refs"] if Path(r).exists()][:2]
        wf = _pick_workflow(len(refs), style)
        if wf is None and refs:  # fall back to text only
            refs, wf = [], _pick_workflow(0, style)
        if wf is None:
            raise ValueError("No enabled image workflow for comic panels -- add one in Settings > Workflows.")
        seed = random.randint(1, 2**31 - 1) if reseed else p["seed"]
        inputs = parse_dynamic_inputs(json.loads(_workflow_json(wf)))
        values: dict[str, Any] = negative_values(inputs, style)
        for inp in inputs:
            role = inp.get("role")
            if role == "prompt":
                prompt = _restyle(p["prompt"], head)
                who = [c for c in tagged if _named_in(prompt[len(head):], c["name"])]
                if who:
                    prompt = head + ", " + ", ".join(character_tag_text(c) for c in who) + prompt[len(head):]
                values[str(inp["nodeId"])] = prompt
            elif role == "width":
                values[str(inp["nodeId"])] = p["width"]
            elif role == "height":
                values[str(inp["nodeId"])] = p["height"]
            elif role == "seed":
                values[str(inp["nodeId"])] = seed
        for node, ref in zip(_image_node_ids(wf), refs):
            values[node] = ref
        job = queue_manager.enqueue(project_id=project_id, kind="image", workflow_id=wf, clip_id=None,
                                    payload={"panel_id": p["id"], "input_values": values})
        conn = get_db(settings.db_path)
        try:
            conn.execute("UPDATE panels SET job_id = ?, seed = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                         (job["id"], seed, p["id"]))
            conn.commit()
        finally:
            conn.close()
        jobs.append(job)
    return jobs


def _workflow_json(workflow_id: int) -> str:
    conn = get_db(settings.db_path)
    try:
        return conn.execute("SELECT workflow_json FROM workflows WHERE id = ?", (workflow_id,)).fetchone()[0]
    finally:
        conn.close()


def update_panel(panel_id: int, fields: dict[str, Any]) -> dict[str, Any] | None:
    allowed = {"prompt", "seed", "caption"}
    sets = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if "bubbles" in fields and fields["bubbles"] is not None:
        sets["bubbles_json"] = json.dumps(fields["bubbles"], ensure_ascii=False)
    conn = get_db(settings.db_path)
    try:
        if sets:
            conn.execute(
                f"UPDATE panels SET {', '.join(f'{k} = ?' for k in sets)}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (*sets.values(), panel_id),
            )
            conn.commit()
        row = conn.execute("SELECT project_id FROM panels WHERE id = ?", (panel_id,)).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    return next((p for p in list_panels(row["project_id"]) if p["id"] == panel_id), None)


# ── layout ────────────────────────────────────────────────────────────────

def _font(kind: str, size: int):
    for path in FONTS[kind]:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def _bubble_box(d: ImageDraw.ImageDraw, text: str, oval: bool, size: int):
    f = _font("bubble", size)
    lines = textwrap.wrap(text, 18 if oval else 24) or [""]
    lw = max(d.textlength(l, font=f) for l in lines)
    lh = int(size * 1.25)
    bw = int(lw * (1.45 if oval else 1.0) + (40 if oval else 56))
    bh = int(lh * len(lines) * (1.45 if oval else 1.0) + (36 if oval else 40))
    return f, lines, lh, bw, bh


def _draw_bubble(img: Image.Image, text: str, cx: int, cy: int, box: tuple[int, int, int, int],
                 oval: bool, tail_dir: int, size: int) -> None:
    d = ImageDraw.Draw(img)
    f, lines, lh, bw, bh = _bubble_box(d, text, oval, size)
    x0 = max(box[0] + 8, min(int(cx - bw / 2), box[2] - bw - 8))
    y0 = max(box[1] + 8, min(int(cy - bh / 2), box[3] - bh - 8))
    tx = x0 + bw // 2 + tail_dir * bw // 5
    d.polygon([(tx - 13, y0 + bh - 12), (tx + 13, y0 + bh - 12), (tx + tail_dir * 22, y0 + bh + 30)],
              fill="white", outline="black", width=3)
    if oval:
        d.ellipse([x0, y0, x0 + bw, y0 + bh], fill="white", outline="black", width=3)
    else:
        d.rounded_rectangle([x0, y0, x0 + bw, y0 + bh], radius=min(bh // 2, 40), fill="white", outline="black", width=3)
    d.polygon([(tx - 10, y0 + bh - 8), (tx + 10, y0 + bh - 8), (tx, y0 + bh)], fill="white")
    ty = y0 + (bh - lh * len(lines)) / 2
    for i, line in enumerate(lines):
        d.text((x0 + (bw - d.textlength(line, font=f)) / 2, ty + i * lh), line, font=f, fill="black")


def _bubble_xy(b: dict[str, Any], n: int, rtl: bool, has_caption: bool) -> tuple[float, float]:
    first = 0.72 if rtl else 0.28
    x = b["x"] if b.get("x") is not None else (first if n % 2 == 0 else 1 - first)
    y = b["y"] if b.get("y") is not None else (0.3 if has_caption else 0.15) + 0.18 * (n // 2)
    return float(x), float(y)


def _caption_box(img: Image.Image, text: str, x: int, y: int, right: bool, inner_w: int, dark: bool) -> None:
    d = ImageDraw.Draw(img)
    f = _font("caption", 21)
    tw = d.textlength(text, font=f)
    x0 = x + inner_w - tw - 34 if right else x + 10
    d.rectangle([x0, y, x0 + tw + 24, y + 38], fill="#111" if dark else "white", outline="black", width=3)
    d.text((x0 + 12, y + 8), text, font=f, fill="white" if dark else "black")


def _webtoon(panels: list[tuple[dict[str, Any], Image.Image]], out: Path) -> list[Path]:
    width, gutter = 800, 110
    tiles = [(p, im.resize((width, round(im.height * width / im.width)), Image.LANCZOS)) for p, im in panels]
    page = Image.new("RGB", (width, gutter + sum(im.height + gutter for _, im in tiles)), "white")
    y = gutter
    for p, im in tiles:
        page.paste(im, (0, y))
        if p.get("caption"):
            _caption_box(page, p["caption"], 24, y - 60, False, width, True)
        for n, b in enumerate(p["bubbles"]):
            bx, by = _bubble_xy(b, n, False, False)
            _draw_bubble(page, b["text"], int(bx * width), y + int(by * im.height),
                         (0, y, width, y + im.height), False, -1 if bx < 0.5 else 1, 26)
        y += im.height + gutter
    png, jpg = out / "webtoon.png", out / "webtoon.jpg"
    page.save(png)
    page.save(jpg, quality=88)
    return [png, jpg]


PAGE = manga_layout.PAGE


def _stored_layout(project_id: int) -> list[dict[str, Any]] | None:
    conn = get_db(settings.db_path)
    try:
        raw = conn.execute("SELECT comic_layout FROM projects WHERE id = ?", (project_id,)).fetchone()
    finally:
        conn.close()
    return json.loads(raw[0]).get("pages") if raw and raw[0] else None


def get_layout(project_id: int, panels: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Manga pages for the current panels (stored choices + automatic rest)."""
    stored = _stored_layout(project_id)
    panels = list_panels(project_id) if panels is None else panels
    pages = manga_layout.resolve(panels, stored)
    ids = {p["id"] for p in panels}
    counts = sorted({len(pg["panels"]) for pg in pages})
    return {
        "pages": pages,
        # Custom only while a stored page still holds a current panel (a
        # re-plan makes new panel ids and leaves the old layout behind).
        "custom": any(i in ids for pg in stored or [] for i in pg.get("panels") or []),
        "templates": {n: manga_layout.templates_for(n) for n in counts},
        "max_per_page": manga_layout.MAX_PER_PAGE,
    }


def save_layout(project_id: int, pages: list[dict[str, Any]] | None) -> dict[str, Any]:
    """Store the user's pages (None = back to automatic). Pages marked auto keep
    no template, so they are re-chosen when their panels change."""
    panels = list_panels(project_id)
    data = None
    if pages is not None:
        known = {p["id"] for p in panels}
        cleaned = []
        for pg in pages:
            ids = [i for i in pg["panels"] if i in known]
            if ids:
                cleaned.append({"panels": ids, "template": None if pg.get("auto") else pg.get("template")})
        data = json.dumps({"pages": cleaned}) if cleaned else None
    conn = get_db(settings.db_path)
    try:
        conn.execute("UPDATE projects SET comic_layout = ? WHERE id = ?", (data, project_id))
        conn.commit()
    finally:
        conn.close()
    return get_layout(project_id, panels)


def _fill_slot(page: Image.Image, poly: list[tuple[float, float]], im: Image.Image | None,
               label: str | None) -> tuple[int, int, int, int]:
    """Cover the polygon with the image -- or leave it blank, with ``label`` as
    a placeholder for previews -- draw its frame and return the box captions
    and bubbles may use."""
    x0, y0, x1, y1 = (int(round(v)) for v in manga_layout._bbox(poly))
    w, h = max(1, x1 - x0), max(1, y1 - y0)
    if im is not None:
        scale = max(w / im.width, h / im.height)
        r = im.resize((max(w, round(im.width * scale)), max(h, round(im.height * scale))), Image.LANCZOS)
        left, top = (r.width - w) // 2, (r.height - h) // 3
        tile = r.crop((left, top, left + w, top + h))
    elif label:
        tile = Image.new("RGB", (w, h), "#e6e6e6")
        d = ImageDraw.Draw(tile)
        f = _font("caption", max(18, min(w, h) // 6))
        d.text(((w - d.textlength(label, font=f)) / 2, h / 2 - f.size / 2), label, font=f, fill="#888")
    else:
        tile = None
    if tile is not None:
        mask = Image.new("L", (w, h), 0)
        ImageDraw.Draw(mask).polygon([(x - x0, y - y0) for x, y in poly], fill=255)
        page.paste(tile, (x0, y0), mask)
    ImageDraw.Draw(page).line(poly + poly[:1], fill="black", width=5, joint="curve")
    # Inner box (second-outermost corners) keeps captions and bubbles off slanted edges.
    xs, ys = sorted(p[0] for p in poly), sorted(p[1] for p in poly)
    if len(poly) >= 4:
        return int(xs[1]), int(ys[1]), int(xs[-2]), int(ys[-2])
    return x0, y0, x1, y1


def _manga_page(page_def: dict[str, Any], by_id: dict[int, dict[str, Any]], rtl: bool,
                *, placeholders: bool) -> Image.Image:
    page = Image.new("RGB", (PAGE["width"], PAGE["height"]), "white")
    polys = manga_layout.slots(page_def["template"], PAGE["width"], PAGE["height"],
                               margin=PAGE["margin"], gutter=PAGE["gutter"], rtl=rtl)
    for poly, pid in zip(polys, page_def["panels"]):
        p = by_id[pid]
        path = p.get("image_path")
        im = Image.open(path).convert("L").convert("RGB") if path and Path(path).exists() else None
        box = _fill_slot(page, poly, im, f"#{p['number']}" if placeholders else None)
        # Bubbles stay below the place/time caption.
        room = (box[0], box[1] + (52 if p.get("caption") else 0), box[2], box[3])
        for n, b in enumerate(p.get("bubbles") or []):
            bx, by = _bubble_xy(b, n, rtl, bool(p.get("caption")))
            _draw_bubble(page, b["text"], int(box[0] + bx * (box[2] - box[0])), int(box[1] + by * (box[3] - box[1])),
                         room, True, -1 if bx < 0.5 else 1, 24)
        if p.get("caption"):
            _caption_box(page, p["caption"], box[0], box[1] + 10, rtl, box[2] - box[0], False)
    return page


def _pages_and_panels(project_id: int) -> tuple[list[dict[str, Any]], dict[int, dict[str, Any]]]:
    panels = list_panels(project_id)
    by_id = {p["id"]: {**p, "number": n} for n, p in enumerate(panels, 1)}
    return get_layout(project_id, panels)["pages"], by_id


def render_manga_page(project_id: int, index: int, *, rtl: bool = True, width: int = 620) -> bytes:
    """PNG preview of one page (unrendered panels show as numbered placeholders)."""
    import io

    pages, by_id = _pages_and_panels(project_id)
    if not 0 <= index < len(pages):
        raise ValueError("No such page.")
    img = _manga_page(pages[index], by_id, rtl, placeholders=True)
    img = img.resize((width, round(img.height * width / img.width)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def _manga(project_id: int, out: Path, rtl: bool) -> list[Path]:
    """The pages as shown in the Panels stage; unrendered panels stay empty frames."""
    pages_def, by_id = _pages_and_panels(project_id)
    pages = [_manga_page(pg, by_id, rtl, placeholders=False) for pg in pages_def]
    paths = []
    for n, pg in enumerate(pages, 1):
        path = out / f"seite_{n:02d}.png"
        pg.save(path)
        paths.append(path)
    if pages:
        pdf = out / "manga.pdf"
        pages[0].save(pdf, save_all=True, append_images=pages[1:], resolution=150)
        paths.insert(0, pdf)
    return paths


def export_comic(project_id: int, *, rtl: bool = True) -> list[str]:
    """Lay out the panels (webtoon: the rendered ones); returns the written files."""
    conn = get_db(settings.db_path)
    try:
        fmt = conn.execute("SELECT format FROM projects WHERE id = ?", (project_id,)).fetchone()[0]
    finally:
        conn.close()
    rendered = [p for p in list_panels(project_id) if p.get("image_path") and Path(p["image_path"]).exists()]
    if not rendered:
        raise ValueError("No rendered panels yet -- render the panels first.")
    out = Path(settings.data_dir) / "assets" / str(project_id) / "export"
    out.mkdir(parents=True, exist_ok=True)
    if fmt == "manga":
        files = _manga(project_id, out, rtl)
    else:
        files = _webtoon([(p, Image.open(p["image_path"]).convert("RGB")) for p in rendered], out)
    return [str(f) for f in files]
