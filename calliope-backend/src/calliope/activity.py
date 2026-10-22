"""What is working right now, per project -- for the "busy" indicators.

Render jobs live in the jobs table and agent turns in agent_sessions; the
long LLM steps (novel summary, storyline, script, clip coverage, panel plan)
run inside a request and leave no trace there. They register here for their
duration; their progress messages (the ``agent.thinking`` events they already
publish) become the operation's current step.
"""
from __future__ import annotations

import functools
import itertools
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

_ids = itertools.count(1)
_ops: dict[int, dict[str, Any]] = {}


@asynccontextmanager
async def track(project_id: int | None, kind: str, label: str) -> AsyncIterator[dict[str, Any]]:
    """Register a running operation for the length of the ``async with``."""
    op = {"id": next(_ids), "project_id": project_id, "kind": kind, "label": label,
          "step": None, "started": time.time()}
    _ops[op["id"]] = op
    try:
        yield op
    finally:
        _ops.pop(op["id"], None)


def tracked(kind: str, label: str):
    """Decorator form of :func:`track`; the project is the first argument
    (or ``project_id=``). Keeps the signature, so FastAPI routes work."""
    def deco(fn):
        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            pid = kwargs.get("project_id", args[0] if args else None)
            async with track(pid, kind, label):
                return await fn(*args, **kwargs)
        return wrapper
    return deco


def note(project_id: Any, message: str) -> None:
    """Progress message for the newest operation of ``project_id``."""
    try:
        pid = int(project_id)
    except (TypeError, ValueError):
        return
    mine = [op for op in _ops.values() if op["project_id"] == pid]
    if mine:
        max(mine, key=lambda op: op["started"])["step"] = message


def operations(project_id: int | None = None) -> list[dict[str, Any]]:
    now = time.time()
    return [
        {**op, "elapsed_sec": int(now - op["started"])}
        for op in sorted(_ops.values(), key=lambda op: op["started"])
        if project_id is None or op["project_id"] == project_id
    ]
