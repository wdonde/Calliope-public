"""Comic projects (webtoon strip / manga pages): one panel per clip.

Story, script and assets are shared with video projects; this module takes
over where a video project renders clips:

* :func:`plan_panels` -- a panels row per clip: an English image prompt
  (the story text keeps its language in the speech bubbles), the size from
  the shot, cropped character sheets as references, bubbles from the clip's
  dialogue lines, a place/time caption on each scene's first panel.
* :func:`render_panels` -- queues image jobs on the workflow that fits the
  number of references; the worker writes the result back via ``panel_id``.
* :func:`export_comic` -- lays the panels out (vertical strip or A4 pages,
  right-to-left for manga) and draws the bubbles: the text is set by code,
  never painted by the model.
"""
from __future__ import annotations

import json
import random
import re
import textwrap
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from calliope.activity import tracked
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
            {"role": "user", "content": "Translate into English for an image-generation prompt. "
                                        "Output only the translation.\n\n" + text},
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


def _panel_characters(desc: str, scene_chars: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Characters named in the shot; the scene's only character when none is named."""
    with_sheet = [c for c in scene_chars if c.get("sheet_path") and Path(c["sheet_path"]).exists()]
    named = [c for c in with_sheet if c["name"].split()[0].lower() in desc.lower()]
    if named:
        return named[:2]
    return with_sheet if len(scene_chars) == 1 else []


def _caption(heading: str | None) -> str:
    return re.sub(r"^(INT|EXT)\.?\s*", "", heading or "").strip().replace(" - ", " – ")


@tracked("panels", "Planning panels")
async def plan_panels(project_id: int, *, replan: bool = False) -> list[dict[str, Any]]:
    """Create (or with ``replan`` rebuild) a panel for every clip, in story order."""
    conn = get_db(settings.db_path)
    try:
        project = row_to_dict(conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone())
        style = STYLES.get(project.get("format") or "", STYLES["webtoon"])
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
                who = _panel_characters(desc, chars)
                view = "head" if clip.get("shot_size") == "closeUp" else "front"
                refs = [_sheet_crop(project_id, c["sheet_path"], view) for c in who]
                ref_hint = " ".join(
                    f"{c['name']} looks exactly like reference image {k + 1} (same face, hair and outfit), "
                    "shown once in a new pose." for k, c in enumerate(who))
                if who:
                    ref_hint += f" Exactly {len(who)} named {'person' if len(who) == 1 else 'people'} in focus. "
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
                values = (project_id, clip["id"], f"{style}. {desc}. {ref_hint}{NO_TEXT}", w, h,
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

def _pick_workflow(n_refs: int) -> int | None:
    """The enabled image workflow with exactly ``n_refs`` image inputs (Klein preferred)."""
    conn = get_db(settings.db_path)
    try:
        rows = [row_to_dict(r) for r in conn.execute(
            "SELECT * FROM workflows WHERE kind = 'image' AND is_enabled = 1 ORDER BY id")]
    finally:
        conn.close()
    fits = []
    for wf in rows:
        inputs = parse_dynamic_inputs(json.loads(wf["workflow_json"]))
        if sum(1 for i in inputs if i.get("kind") == "image") == n_refs:
            fits.append(wf)
    fits.sort(key=lambda wf: ("klein" not in wf["name"].lower(), wf["id"]))
    return fits[0]["id"] if fits else None


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
    jobs = []
    for p in list_panels(project_id):
        if panel_ids and p["id"] not in panel_ids:
            continue
        if missing_only and not panel_ids and p.get("image_path"):
            continue
        refs = [r for r in p["refs"] if Path(r).exists()][:2]
        wf = _pick_workflow(len(refs))
        if wf is None and refs:  # fall back to text only
            refs, wf = [], _pick_workflow(0)
        if wf is None:
            raise ValueError("No enabled image workflow for comic panels -- add one in Settings > Workflows.")
        seed = random.randint(1, 2**31 - 1) if reseed else p["seed"]
        values: dict[str, Any] = {}
        for inp in parse_dynamic_inputs(json.loads(_workflow_json(wf))):
            role = inp.get("role")
            if role == "prompt":
                values[str(inp["nodeId"])] = p["prompt"]
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


def _manga(panels: list[tuple[dict[str, Any], Image.Image]], out: Path, rtl: bool) -> list[Path]:
    W, H, M, G = 1240, 1754, 70, 26
    inner = W - 2 * M
    items = [(p, im.convert("L").convert("RGB")) for p, im in panels]
    rows, i = [], 0
    while i < len(items):
        p = items[i][0]
        nxt = items[i + 1][0] if i + 1 < len(items) else None
        if p.get("shot_size") != "wide" and nxt and nxt.get("shot_size") != "wide":
            tall = "closeUp" in (p.get("shot_size"), nxt.get("shot_size"))
            rows.append(([items[i], items[i + 1]], int(inner / 2 * (1.3 if tall else 1.0))))
            i += 2
        else:
            rows.append(([items[i]], int(inner * (0.52 if p.get("shot_size") == "wide" else 0.62))))
            i += 1
    page_rows, cur, used = [], [], 0
    for cells, h in rows:
        if cur and used + h > H - 2 * M:
            page_rows.append(cur)
            cur, used = [], 0
        cur.append((cells, h))
        used += h + G
    if cur:
        page_rows.append(cur)
    pages = []
    for prow in page_rows:
        page = Image.new("RGB", (W, H), "white")
        pages.append(page)
        total = sum(h for _, h in prow) + G * (len(prow) - 1)
        stretch = min(1.35, (H - 2 * M) / total) if len(prow) > 1 or total > (H - 2 * M) * 0.6 else 1.0
        y = M
        for cells, h in prow:
            h = int(h * stretch)
            cw = (inner - G * (len(cells) - 1)) // len(cells)
            for k, (p, im) in enumerate(list(reversed(cells)) if rtl else cells):
                x = M + k * (cw + G)
                scale = max(cw / im.width, h / im.height)
                r = im.resize((round(im.width * scale), round(im.height * scale)), Image.LANCZOS)
                left, top = (r.width - cw) // 2, (r.height - h) // 3
                page.paste(r.crop((left, top, left + cw, top + h)), (x, y))
                box = (x, y, x + cw, y + h)
                ImageDraw.Draw(page).rectangle(box, outline="black", width=5)
                if p.get("caption"):
                    _caption_box(page, p["caption"], x, y + 10, rtl, cw, False)
                for n, b in enumerate(p["bubbles"]):
                    bx, by = _bubble_xy(b, n, rtl, bool(p.get("caption")))
                    _draw_bubble(page, b["text"], int(x + bx * cw), int(y + by * h), box, True,
                                 -1 if bx < 0.5 else 1, 24)
            y += h + G
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
    """Lay out every rendered panel; returns the written files."""
    conn = get_db(settings.db_path)
    try:
        fmt = conn.execute("SELECT format FROM projects WHERE id = ?", (project_id,)).fetchone()[0]
    finally:
        conn.close()
    panels = [(p, Image.open(p["image_path"]).convert("RGB")) for p in list_panels(project_id)
              if p.get("image_path") and Path(p["image_path"]).exists()]
    if not panels:
        raise ValueError("No rendered panels yet -- render the panels first.")
    out = Path(settings.data_dir) / "assets" / str(project_id) / "export"
    out.mkdir(parents=True, exist_ok=True)
    files = _manga(panels, out, rtl) if fmt == "manga" else _webtoon(panels, out)
    return [str(f) for f in files]
