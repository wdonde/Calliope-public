"""Colorize: a black-and-white picture becomes a color one via a one-reference workflow."""
from __future__ import annotations

import json
from pathlib import Path

from PIL import Image

from calliope.config import settings

WF = {"10": {"class_type": "PrimitiveStringMultiline", "inputs": {"value": "x"}, "_meta": {"title": "Prompt (Input:prompt)"}},
      "11": {"class_type": "PrimitiveInt", "inputs": {"value": 1024}, "_meta": {"title": "Width (Input:width)"}},
      "12": {"class_type": "PrimitiveInt", "inputs": {"value": 1024}, "_meta": {"title": "Height (Input:height)"}},
      "50": {"class_type": "LoadImage", "inputs": {"image": ""}, "_meta": {"title": "Ref (Input:image)"}},
      "40": {"class_type": "SaveImage", "inputs": {"images": ["10", 0]}, "_meta": {"title": "Out (Output:image)"}}}


def _picture(name: str, size=(800, 1200)) -> Path:
    p = Path(settings.assets_dir) / "uploads" / name
    p.parent.mkdir(parents=True, exist_ok=True)
    Image.new("L", size, 200).convert("RGB").save(p)
    return p


def test_colorize_keeps_aspect_and_uses_character_colors(client):
    client.post("/api/workflows", json={"name": "Klein edit", "kind": "image", "workflow_json": WF})
    pid = client.post("/api/projects", json={"title": "M"}).json()["id"]
    reed = client.post(f"/api/projects/{pid}/characters", json={"name": "Reed", "appearance": (
        "CHARACTER INFORMATION\nName: Reed\nHair: dark brown, short\nEyes: blue\n\nOUTFIT\nOuterwear: olive field jacket")}).json()
    client.post(f"/api/projects/{pid}/characters", json={"name": "Malone", "appearance": "red beret"})
    pic = _picture("panel.png")

    r = client.post("/api/colorize", json={"image_path": str(pic), "notes": "blauer Himmel", "project_id": pid,
                                           "character_ids": [reed["id"]]})
    job = client.get(f"/api/jobs/{r.json()['job_id']}").json()
    values = job["payload"]["input_values"]
    assert values["50"] == str(pic.resolve()) and (values["11"], values["12"]) == (816, 1232)
    prompt = values["10"]
    assert prompt.startswith("Colorize this black-and-white") and "Colors to use: blauer Himmel" in prompt
    assert "- Reed: Hair: dark brown, short; Eyes: blue; Outerwear: olive field jacket" in prompt
    assert "Malone" not in prompt and job["project_id"] == pid

    # Without a project the job runs in the hidden Playground project.
    free = client.post("/api/colorize", json={"image_path": str(pic)}).json()
    assert client.get(f"/api/jobs/{free['job_id']}").json()["project_id"] != pid
    assert client.post("/api/colorize", json={"image_path": "/etc/passwd"}).status_code == 422
