"""Render styles (render_styles.py): list, create, edit and delete; a project
picks one with PATCH /api/projects/{id} ``render_style_id``."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from calliope import render_styles

router = APIRouter()


class StyleIn(BaseModel):
    category: str | None = Field(None, min_length=1, max_length=60)
    name: str | None = Field(None, min_length=1, max_length=80)
    style_prompt: str | None = None
    negative_prompt: str | None = None
    # number of references ("0", "1", "2") -> workflow id; missing = the role's workflow
    workflows: dict[str, int | None] | None = None


@router.get("/render-styles")
async def list_render_styles() -> list[dict[str, Any]]:
    return render_styles.list_styles()


@router.post("/render-styles")
async def create_render_style(payload: StyleIn) -> dict[str, Any]:
    if not payload.category or not payload.name:
        raise HTTPException(422, "category and name are required")
    return render_styles.save_style(payload.model_dump(exclude_none=True))


@router.patch("/render-styles/{style_id}")
async def update_render_style(style_id: int, payload: StyleIn) -> dict[str, Any]:
    if render_styles.get_style(style_id) is None:
        raise HTTPException(404, "Style not found")
    return render_styles.save_style(payload.model_dump(exclude_unset=True), style_id)


@router.delete("/render-styles/{style_id}")
async def delete_render_style(style_id: int) -> dict[str, bool]:
    render_styles.delete_style(style_id)
    return {"ok": True}
