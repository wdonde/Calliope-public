"""Colorize a black-and-white picture (agent/colorize.py)."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from calliope.agent.colorize import colorize
from calliope.config import settings

router = APIRouter()


class ColorizeRequest(BaseModel):
    image_path: str
    notes: str = ""  # colors to use, any language
    project_id: int | None = None  # take the characters' colors from this project
    character_ids: list[int] | None = None  # only these characters (None = all)


@router.post("/colorize")
async def colorize_picture(payload: ColorizeRequest) -> dict[str, Any]:
    target = Path(payload.image_path).resolve()
    try:
        target.relative_to(Path(settings.assets_dir).resolve())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Image outside the assets folder") from exc
    if not target.is_file():
        raise HTTPException(status_code=404, detail="Image not found")
    try:
        job = colorize(str(target), notes=payload.notes, project_id=payload.project_id,
                       character_ids=payload.character_ids)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"job_id": job["id"]}
