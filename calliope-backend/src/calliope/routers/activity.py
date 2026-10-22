"""GET /api/activity -- is anything working, per project?

Combines the three places work happens: long LLM steps (calliope.activity),
agent turns (agent_sessions.status) and render jobs (jobs.status).
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from calliope import activity
from calliope.config import settings
from calliope.db import get_db

router = APIRouter()


def _project_activity(conn, project_id: int) -> dict[str, Any]:
    ops = activity.operations(project_id)
    agents = [
        {"session_id": r["id"], "title": r["title"]}
        for r in conn.execute(
            "SELECT id, title FROM agent_sessions WHERE project_id = ? AND status = 'running'", (project_id,)
        )
    ]
    counts = {
        r["status"]: r["n"]
        for r in conn.execute(
            "SELECT status, COUNT(*) AS n FROM jobs WHERE project_id = ? AND status IN ('pending', 'running') "
            "GROUP BY status",
            (project_id,),
        )
    }
    current = conn.execute(
        "SELECT kind, started_at FROM jobs WHERE project_id = ? AND status = 'running' ORDER BY started_at LIMIT 1",
        (project_id,),
    ).fetchone()
    jobs = {"running": counts.get("running", 0), "queued": counts.get("pending", 0),
            "current_kind": current["kind"] if current else None}
    busy = bool(ops or agents or jobs["running"] or jobs["queued"])
    return {"busy": busy, "operations": ops, "agents": agents, "jobs": jobs}


@router.get("")
async def get_activity(project_id: int | None = None) -> dict[str, Any]:
    conn = get_db(settings.db_path)
    try:
        if project_id is not None:
            return _project_activity(conn, project_id)
        ids = {r[0] for r in conn.execute(
            "SELECT project_id FROM jobs WHERE status IN ('pending', 'running') AND project_id IS NOT NULL "
            "UNION SELECT project_id FROM agent_sessions WHERE status = 'running' AND project_id IS NOT NULL"
        )}
        ids |= {op["project_id"] for op in activity.operations() if op["project_id"] is not None}
        return {"projects": {str(pid): _project_activity(conn, pid) for pid in sorted(ids)}}
    finally:
        conn.close()
