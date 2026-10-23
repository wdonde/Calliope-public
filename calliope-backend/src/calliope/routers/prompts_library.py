"""The Prompts page: every prompt Calliope sends, with where and when it is
used; fixed texts can be edited and reset, each entry can carry a picture."""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, UploadFile
from pydantic import BaseModel

from calliope import prompt_library
from calliope.config import settings

router = APIRouter()


class PromptUpdate(BaseModel):
    text: str | None = None
    reset: bool = False
    image_path: str | None = None  # "" removes the picture


def _entry(key: str) -> dict[str, Any]:
    return next(e for e in prompt_library.entries() if e["key"] == key)


@router.get("/prompts")
async def list_prompts() -> list[dict[str, Any]]:
    return prompt_library.entries()


@router.patch("/prompts/{key}")
async def update_prompt(key: str, payload: PromptUpdate) -> dict[str, Any]:
    spec = prompt_library.BY_KEY.get(key)
    if spec is None:
        raise HTTPException(404, "Unknown prompt")
    if payload.text is not None or payload.reset:
        if not spec.editable:
            raise HTTPException(422, "This prompt is assembled in code and cannot be edited here.")
        if payload.text is not None and not payload.reset:
            if not payload.text.strip():
                raise HTTPException(422, "The prompt must not be empty -- use Reset for the default.")
            missing = prompt_library.missing_placeholders(key, payload.text)
            if missing:
                raise HTTPException(422, "Keep the placeholders: " + ", ".join("{" + m + "}" for m in missing))
    prompt_library.save(key, text_value=payload.text, reset_text=payload.reset, image_path=payload.image_path)
    return _entry(key)


@router.post("/prompts/{key}/image")
async def upload_prompt_image(key: str, file: UploadFile) -> dict[str, Any]:
    if key not in prompt_library.BY_KEY:
        raise HTTPException(404, "Unknown prompt")
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in (".png", ".jpg", ".jpeg", ".webp"):
        raise HTTPException(422, "Use a PNG, JPG or WebP picture.")
    folder = Path(settings.assets_dir) / "prompt_images"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{key}{suffix}"
    with target.open("wb") as f:
        shutil.copyfileobj(file.file, f)
    prompt_library.save(key, image_path=str(target))
    return _entry(key)
