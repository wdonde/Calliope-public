"""What the render queue is doing, in words: for the queue panels in Assets and Library.

Jobs only carry ids in their payload; this resolves them to what a person
recognises -- "Character sheet · Reed", "Panel #12", "Colorize · page_3.png".
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from calliope.config import settings
from calliope.db import get_db

ACTIVE = ("running", "pending")
RECENT_MINUTES = 15
HISTORY = 8  # finished jobs per workflow the estimate averages over
FALLBACK_SEC = {"image": 120, "video": 1500, "export": 120}


def _durations(conn) -> tuple[dict[int, float], dict[str, float]]:
    """Typical seconds per workflow and per job kind, from recently finished jobs."""
    rows = conn.execute(
        "SELECT workflow_id, kind, (julianday(completed_at) - julianday(started_at)) * 86400 AS sec "
        "FROM jobs WHERE status = 'done' AND started_at IS NOT NULL AND completed_at IS NOT NULL "
        "ORDER BY completed_at DESC LIMIT 500").fetchall()
    by_wf: dict[int, list[float]] = {}
    by_kind: dict[str, list[float]] = {}
    for r in rows:
        if r["sec"] is None or r["sec"] <= 0:
            continue
        if r["workflow_id"] is not None and len(by_wf.setdefault(r["workflow_id"], [])) < HISTORY:
            by_wf[r["workflow_id"]].append(r["sec"])
        if len(by_kind.setdefault(r["kind"], [])) < HISTORY * 3:
            by_kind[r["kind"]].append(r["sec"])
    avg = lambda xs: sum(xs) / len(xs)  # noqa: E731
    return ({k: avg(v) for k, v in by_wf.items() if v}, {k: avg(v) for k, v in by_kind.items() if v})


def _seconds_since(conn, stamp: str | None) -> float:
    if not stamp:
        return 0.0
    row = conn.execute("SELECT (julianday('now') - julianday(?)) * 86400 AS s", (stamp,)).fetchone()
    return max(0.0, row["s"] or 0.0)


def _label(conn, job: dict[str, Any], payload: dict[str, Any]) -> str:
    def name(table: str, key: str) -> str:
        row = conn.execute(f"SELECT name FROM {table} WHERE id = ?", (payload[key],)).fetchone()
        return row["name"] if row else f"#{payload[key]}"

    if payload.get("library_asset_id"):
        row = conn.execute("SELECT name FROM library_assets WHERE id = ?", (payload["library_asset_id"],)).fetchone()
        return f"Library · {row['name'] if row else '#' + str(payload['library_asset_id'])}"
    if payload.get("character_id"):
        return f"Character sheet · {name('characters', 'character_id')}"
    if payload.get("location_id"):
        return f"Location · {name('locations', 'location_id')}"
    if payload.get("item_id"):
        return f"Item · {name('items', 'item_id')}"
    if payload.get("panel_id"):
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM panels p JOIN clips c ON c.id = p.clip_id JOIN scenes s ON s.id = c.scene_id "
            "WHERE p.project_id = ? AND (s.order_index, c.order_index) <= ("
            "SELECT s2.order_index, c2.order_index FROM panels p2 JOIN clips c2 ON c2.id = p2.clip_id "
            "JOIN scenes s2 ON s2.id = c2.scene_id WHERE p2.id = ?)",
            (job["project_id"], payload["panel_id"]),
        ).fetchone()
        return f"Panel #{row['n']}" if row and row["n"] else "Panel"
    if payload.get("colorize_source"):
        return f"Colorize · {Path(payload['colorize_source']).name}"
    if job.get("kind") == "video":
        return f"Video clip #{job.get('clip_id') or ''}".strip()
    if job.get("kind") == "export":
        return "Export"
    return f"{job.get('kind', 'job').capitalize()} job"


def overview(project_id: int | None = None) -> dict[str, Any]:
    """Running and queued jobs (queue order), plus the ones that ended recently."""
    conn = get_db(settings.db_path)
    try:
        titles = {r["id"]: r["title"] for r in conn.execute("SELECT id, title FROM projects")}
        active = [dict(r) for r in conn.execute(
            "SELECT * FROM jobs WHERE status IN ('running', 'pending') ORDER BY created_at ASC, id ASC")]
        recent = [dict(r) for r in conn.execute(
            "SELECT * FROM jobs WHERE status IN ('done', 'failed', 'cancelled') AND completed_at IS NOT NULL "
            f"AND completed_at >= datetime('now', '-{RECENT_MINUTES} minutes') ORDER BY completed_at DESC LIMIT 8")]

        def public(job: dict[str, Any], position: int | None = None) -> dict[str, Any]:
            payload = json.loads(job.get("payload_json") or "{}")
            outputs = json.loads(job.get("output_paths_json") or "[]")
            return {
                "id": job["id"], "status": job["status"], "kind": job["kind"],
                "project_id": job["project_id"], "project": titles.get(job["project_id"]),
                "label": _label(conn, job, payload), "position": position,
                "created_at": job["created_at"], "started_at": job["started_at"],
                "completed_at": job["completed_at"], "error": job.get("error"),
                "output": outputs[0] if outputs else None,
            }

        running = [public(j) for j in active if j["status"] == "running"]
        queued = [public(j, i + 1) for i, j in enumerate(j for j in active if j["status"] == "pending")]
        done = [public(j) for j in recent]

        # Forecast: jobs render one after another, each taking its workflow's usual time.
        by_wf, by_kind = _durations(conn)
        now = conn.execute("SELECT datetime('now') AS n").fetchone()["n"]
        wf_of = {j["id"]: j for j in active}
        clock = 0.0
        for j in running + queued:
            src = wf_of[j["id"]]
            typical = by_wf.get(src["workflow_id"]) or by_kind.get(src["kind"]) or FALLBACK_SEC.get(src["kind"], 120)
            if j["status"] == "running":
                left = max(0.0, typical - _seconds_since(conn, src["started_at"]))
            else:
                left = typical
            clock += left
            j["typical_sec"] = round(typical)
            j["remaining_sec"] = round(clock)
            j["eta"] = conn.execute("SELECT datetime(?, ?) AS t", (now, f"+{round(clock)} seconds")).fetchone()["t"]
            j["estimate_from"] = "workflow" if src["workflow_id"] in by_wf else "kind" if src["kind"] in by_kind else "default"
    finally:
        conn.close()
    mine = (lambda j: j["project_id"] == project_id) if project_id is not None else (lambda j: True)
    return {
        "running": [j for j in running if mine(j)],
        "queued": [j for j in queued if mine(j)],
        "recent": [j for j in done if mine(j)],
        # Jobs of other projects run in the same queue and delay these.
        "elsewhere": {"running": sum(not mine(j) for j in running), "queued": sum(not mine(j) for j in queued)},
        # When the last of these jobs should be done (UTC, like the job times).
        "finish_at": next((j["eta"] for j in reversed(running + queued) if mine(j)), None),
        "now": now,
    }
