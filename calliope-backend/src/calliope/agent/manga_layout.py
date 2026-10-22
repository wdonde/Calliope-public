"""Manga page layouts: panels as polygons cut out of the page.

A layout is a tree of cuts in the spirit of comfyui_panels (MIT,
github.com/bmad4ever/comfyui_panels): the page is cut into rows or columns,
each part can be cut again, and cuts may lean slightly for a livelier page.
Reading order follows the cuts -- rows top to bottom, columns left to right,
mirrored as a whole for right-to-left manga.

``paginate`` spreads the panels over pages by shot size and ``choose``
picks the template whose slot sizes fit the panels best; both only run for
pages the user has not set (agent/comic.py stores the result per project).
"""
from __future__ import annotations

import math
from typing import Any

Point = tuple[float, float]

# Tree: None is a panel slot; ("rows"|"cols", [ratios], [children]).
TEMPLATES: dict[str, dict[str, Any]] = {}


def _t(name: str, tree: Any, tilt: float = 0.0) -> None:
    TEMPLATES[name] = {"tree": tree, "tilt": tilt, "slots": _count(tree)}


def _count(tree: Any) -> int:
    return 1 if tree is None else sum(_count(c) for c in tree[2])


def R(ratios: list[float], children: list[Any] | None = None) -> tuple:
    return ("rows", ratios, children or [None] * len(ratios))


def C(ratios: list[float], children: list[Any] | None = None) -> tuple:
    return ("cols", ratios, children or [None] * len(ratios))


_t("1-splash", None)
_t("2-stack", R([0.5, 0.5]))
_t("2-big-top", R([0.64, 0.36]), 0.03)
_t("2-side", C([0.5, 0.5]))
_t("3-stack", R([0.34, 0.33, 0.33]), 0.025)
_t("3-wide-pair", R([0.55, 0.45], [None, C([0.5, 0.5])]))
_t("3-pair-wide", R([0.42, 0.58], [C([0.55, 0.45]), None]), 0.03)
_t("3-tall-left", C([0.6, 0.4], [None, R([0.5, 0.5])]))
_t("4-band", R([0.3, 0.4, 0.3], [None, C([0.5, 0.5]), None]), 0.025)
_t("4-grid", R([0.5, 0.5], [C([0.5, 0.5]), C([0.4, 0.6])]))
_t("4-hero-split", R([0.45, 0.55], [None, C([0.62, 0.38], [None, R([0.5, 0.5])])]), 0.03)
_t("4-tall-strip", C([0.56, 0.44], [None, R([0.33, 0.34, 0.33])]))
_t("5-classic", R([0.3, 0.35, 0.35], [None, C([0.5, 0.5]), C([0.6, 0.4])]))
_t("5-centre", R([0.25, 0.45, 0.3], [C([0.5, 0.5]), None, C([0.4, 0.6])]), 0.03)
_t("5-corner", R([0.5, 0.5], [C([0.6, 0.4], [None, R([0.5, 0.5])]), C([0.5, 0.5])]))
_t("5-tall-right", R([0.35, 0.65], [C([0.5, 0.5]), C([0.6, 0.4], [None, R([0.5, 0.5])])]), 0.025)
_t("6-grid", R([0.33, 0.34, 0.33], [C([0.5, 0.5]), C([0.5, 0.5]), C([0.5, 0.5])]))
_t("6-band", R([0.3, 0.4, 0.3], [C([0.5, 0.5]), None, C([0.33, 0.34, 0.33])]), 0.025)
_t("6-climax", R([0.25, 0.25, 0.5], [None, C([0.4, 0.6]), C([0.55, 0.45], [None, R([0.5, 0.5])])]), 0.03)

# A4 page at 150 dpi; the export renders and choose() scores on this geometry.
PAGE = {"width": 1240, "height": 1754, "margin": 70, "gutter": 26}
MAX_PER_PAGE = max(t["slots"] for t in TEMPLATES.values())
PAGE_CAPACITY = 5.2
SHOT_WEIGHT = {"wide": 1.6, "medium": 1.0, "overShoulder": 1.0, "closeUp": 0.8, "insert": 0.55}
# Slot shape (width / height) each shot reads best in.
SHOT_ASPECT = {"wide": 1.7, "medium": 1.0, "overShoulder": 1.1, "closeUp": 0.85, "insert": 1.0}


def templates_for(n: int) -> list[str]:
    return [k for k, t in TEMPLATES.items() if t["slots"] == n]


# ── geometry ──

def _clip(poly: list[Point], a: Point, b: Point, keep_positive: bool) -> list[Point]:
    """Sutherland-Hodgman against the line a→b; keeps one side."""
    def side(p: Point) -> float:
        v = (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0])
        return v if keep_positive else -v

    out: list[Point] = []
    for i, cur in enumerate(poly):
        prev = poly[i - 1]
        sc, sp = side(cur), side(prev)
        if sc >= 0:
            if sp < 0:
                out.append(_cross(prev, cur, sp, sc))
            out.append(cur)
        elif sp >= 0:
            out.append(_cross(prev, cur, sp, sc))
    return out


def _cross(p: Point, q: Point, sp: float, sq: float) -> Point:
    t = sp / (sp - sq)
    return (p[0] + t * (q[0] - p[0]), p[1] + t * (q[1] - p[1]))


def _bbox(poly: list[Point]) -> tuple[float, float, float, float]:
    xs, ys = [p[0] for p in poly], [p[1] for p in poly]
    return min(xs), min(ys), max(xs), max(ys)


def _split(poly: list[Point], tree: Any, tilt: float, gutter: float, depth: int, out: list[list[Point]]) -> None:
    if tree is None:
        out.append(poly)
        return
    kind, ratios, children = tree
    x0, y0, x1, y1 = _bbox(poly)
    total = sum(ratios)
    edge = 0.0
    g = gutter / 2
    for i, child in enumerate(children):
        piece = poly
        # Lean alternates per cut so neighbouring cuts don't run parallel.
        if i > 0:
            pos, lean = edge, tilt * (1 if (i + depth) % 2 else -1)
            if kind == "rows":
                y = y0 + pos * (y1 - y0)
                d = lean * (x1 - x0)
                piece = _clip(piece, (x0 - 1, y + d + g), (x1 + 1, y - d + g), True)
            else:
                x = x0 + pos * (x1 - x0)
                d = lean * (y1 - y0)
                piece = _clip(piece, (x + d + g, y0 - 1), (x - d + g, y1 + 1), False)
        edge += ratios[i] / total
        if i < len(children) - 1:
            pos, lean = edge, tilt * (1 if (i + 1 + depth) % 2 else -1)
            if kind == "rows":
                y = y0 + pos * (y1 - y0)
                d = lean * (x1 - x0)
                piece = _clip(piece, (x0 - 1, y + d - g), (x1 + 1, y - d - g), False)
            else:
                x = x0 + pos * (x1 - x0)
                d = lean * (y1 - y0)
                piece = _clip(piece, (x + d - g, y0 - 1), (x - d - g, y1 + 1), True)
        _split(piece, child, tilt, gutter, depth + 1, out)


def slots(template: str, width: float, height: float, *, margin: float, gutter: float,
          rtl: bool = False) -> list[list[Point]]:
    """Panel polygons in reading order for a page of ``width`` x ``height``."""
    t = TEMPLATES[template]
    page = [(margin, margin), (width - margin, margin), (width - margin, height - margin), (margin, height - margin)]
    out: list[list[Point]] = []
    _split(page, t["tree"], t["tilt"], gutter, 0, out)
    if rtl:
        out = [[(width - x, y) for x, y in poly] for poly in out]
    return out


def _area(poly: list[Point]) -> float:
    return abs(sum(p[0] * q[1] - q[0] * p[1] for p, q in zip(poly, poly[1:] + poly[:1]))) / 2


# ── automatic choices ──

def _weight(panel: dict[str, Any]) -> float:
    return SHOT_WEIGHT.get(panel.get("shot_size") or "", 1.0)


def paginate(panels: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Group panels into pages: about PAGE_CAPACITY shot weight each, new scenes
    preferably on a fresh page, no page left with a single stray panel."""
    pages: list[list[dict[str, Any]]] = []
    cur: list[dict[str, Any]] = []
    load = 0.0
    for p in panels:
        w = _weight(p)
        new_scene = bool(cur) and p.get("scene_order") != cur[-1].get("scene_order")
        if cur and (load + w > PAGE_CAPACITY or len(cur) >= MAX_PER_PAGE
                    or (new_scene and load >= PAGE_CAPACITY * 0.7)):
            pages.append(cur)
            cur, load = [], 0.0
        cur.append(p)
        load += w
    if cur:
        if len(cur) == 1 and pages and len(pages[-1]) < MAX_PER_PAGE:
            pages[-1].append(cur[0])
        else:
            pages.append(cur)
    return pages


def choose(panels: list[dict[str, Any]], previous: str | None = None) -> str:
    """Template whose slot sizes and shapes suit these panels best."""
    weights = [_weight(p) for p in panels]
    total_w = sum(weights)
    best, best_score = None, math.inf
    for name in templates_for(len(panels)):
        polys = slots(name, PAGE["width"], PAGE["height"], margin=PAGE["margin"], gutter=PAGE["gutter"])
        areas = [_area(p) for p in polys]
        total_a = sum(areas)
        score = 0.0
        for poly, area, w, p in zip(polys, areas, weights, panels):
            x0, y0, x1, y1 = _bbox(poly)
            score += abs(area / total_a - w / total_w)
            want = SHOT_ASPECT.get(p.get("shot_size") or "", 1.0)
            score += 0.12 * abs(math.log(((x1 - x0) / (y1 - y0)) / want))
        if name == previous:
            score += 0.08
        if score < best_score:
            best, best_score = name, score
    return best or templates_for(min(len(panels), MAX_PER_PAGE))[0]


def resolve(panels: list[dict[str, Any]], stored: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Pages for the current panels: the stored ones where still valid, the
    rest filled in automatically. Each page: {"panels": [ids], "template",
    "auto"} where auto means the template was chosen, not set by the user."""
    by_id = {p["id"]: p for p in panels}
    pages: list[dict[str, Any]] = []
    placed: set[int] = set()
    for page in stored or []:
        ids = [i for i in page.get("panels") or [] if i in by_id and i not in placed][:MAX_PER_PAGE]
        if ids:
            placed.update(ids)
            pages.append({"panels": ids, "template": page.get("template")})
    if not pages:
        pages = [{"panels": [p["id"] for p in grp], "template": None} for grp in paginate(panels)]
    else:
        # Panels after the last placed one get pages of their own; others
        # join the page of the panel before them.
        order = [p["id"] for p in panels]
        last = max(i for i, pid in enumerate(order) if pid in placed)
        tail = [p for p in panels[last + 1:] if p["id"] not in placed]
        pages += [{"panels": [p["id"] for p in grp], "template": None} for grp in paginate(tail)]
        placed.update(p["id"] for p in tail)
        for idx, pid in enumerate(order):
            if pid in placed:
                continue
            before = next((order[j] for j in range(idx - 1, -1, -1) if order[j] in placed), None)
            target = next((pg for pg in pages if before in pg["panels"]), pages[0])
            pos = target["panels"].index(before) + 1 if before in target["panels"] else 0
            if len(target["panels"]) >= MAX_PER_PAGE:
                # A full page: the panel opens a page of its own, before the
                # target when nothing precedes it, so reading order holds.
                at = pages.index(target) + (0 if before is None else 1)
                pages.insert(at, {"panels": [pid], "template": None})
            else:
                target["panels"].insert(pos, pid)
                if target.get("template") and TEMPLATES[target["template"]]["slots"] != len(target["panels"]):
                    target["template"] = None
            placed.add(pid)
    previous = None
    for page in pages:
        t = page.get("template")
        valid = t in TEMPLATES and TEMPLATES[t]["slots"] == len(page["panels"])
        page["auto"] = not valid
        if not valid:
            page["template"] = choose([by_id[i] for i in page["panels"]], previous)
        previous = page["template"]
    return pages
