"""Comic projects: panels per clip, rendering and the webtoon/manga export."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from calliope.agent.comic import export_comic, list_panels, plan_panels, render_panels, update_panel

router = APIRouter()


class PlanRequest(BaseModel):
    replan: bool = False


class RenderRequest(BaseModel):
    panel_ids: list[int] | None = None
    missing_only: bool = True
    reseed: bool = False


class PanelUpdate(BaseModel):
    prompt: str | None = None
    seed: int | None = None
    caption: str | None = None
    # [{"speaker", "text", "x", "y"}], x/y as 0..1 of the panel (null = automatic)
    bubbles: list[dict[str, Any]] | None = None


class ExportRequest(BaseModel):
    rtl: bool = True


@router.get("/projects/{project_id}/panels")
async def get_panels(project_id: int) -> list[dict[str, Any]]:
    return list_panels(project_id)


@router.post("/projects/{project_id}/panels/plan")
async def plan(project_id: int, payload: PlanRequest | None = None) -> list[dict[str, Any]]:
    return await plan_panels(project_id, replan=bool(payload and payload.replan))


@router.post("/projects/{project_id}/panels/render")
async def render(project_id: int, payload: RenderRequest | None = None) -> dict[str, Any]:
    p = payload or RenderRequest()
    try:
        jobs = render_panels(project_id, panel_ids=p.panel_ids, missing_only=p.missing_only, reseed=p.reseed)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"queued": len(jobs), "job_ids": [j["id"] for j in jobs]}


@router.patch("/panels/{panel_id}")
async def patch_panel(panel_id: int, payload: PanelUpdate) -> dict[str, Any]:
    panel = update_panel(panel_id, payload.model_dump(exclude_unset=True))
    if panel is None:
        raise HTTPException(status_code=404, detail="Panel not found")
    return panel


@router.post("/projects/{project_id}/comic/export")
async def export(project_id: int, payload: ExportRequest | None = None) -> dict[str, Any]:
    try:
        files = export_comic(project_id, rtl=True if payload is None else payload.rtl)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"files": files}
