"""Dependency map of a project (agent/dependency_map.py): the graph, its findings
and the fixes it offers (unlink, link, merge)."""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from calliope.agent import dependency_map

router = APIRouter()


class Connection(BaseModel):
    kind: Literal["scene_character", "scene_location"]
    source_id: int  # character / location
    target_id: int  # scene


class Merge(BaseModel):
    kind: Literal["character", "location"]
    keep_id: int
    drop_id: int


@router.get("/projects/{project_id}/dependencies")
async def get_dependencies(project_id: int, prompts: bool = False) -> dict[str, Any]:
    try:
        return dependency_map.build(project_id, with_prompts=prompts)
    except KeyError as exc:
        raise HTTPException(404, "Project not found") from exc


@router.post("/projects/{project_id}/dependencies/unlink")
async def unlink(project_id: int, payload: Connection) -> dict[str, bool]:
    try:
        dependency_map.unlink(project_id, payload.kind, payload.source_id, payload.target_id)
    except KeyError as exc:
        raise HTTPException(404, "Scene not found") from exc
    return {"ok": True}


@router.post("/projects/{project_id}/dependencies/link")
async def link(project_id: int, payload: Connection) -> dict[str, bool]:
    try:
        dependency_map.link(project_id, payload.kind, payload.source_id, payload.target_id)
    except KeyError as exc:
        raise HTTPException(404, "Scene not found") from exc
    return {"ok": True}


@router.post("/projects/{project_id}/dependencies/merge")
async def merge(project_id: int, payload: Merge) -> dict[str, Any]:
    try:
        return dependency_map.merge(project_id, payload.kind, payload.keep_id, payload.drop_id)
    except KeyError as exc:
        raise HTTPException(404, "Asset not found") from exc
