"""Comic projects: panels planned per clip, bubbles from dialogue, export layouts."""
from __future__ import annotations

import json
from pathlib import Path

from PIL import Image

from calliope.config import settings
from calliope.db import get_db


def _comic_project(client, fmt: str, tmp: Path) -> tuple[int, int]:
    pid = client.post("/api/projects", json={"title": "Comic", "format": fmt}).json()["id"]
    sheet = tmp / "sheet.png"
    Image.new("RGB", (900, 600), "white").save(sheet)
    conn = get_db(settings.db_path)
    try:
        cur = conn.execute(
            "INSERT INTO characters (project_id, name, appearance, sheet_path) VALUES (?, ?, ?, ?)",
            (pid, "Iselle", "blonde ponytail", str(sheet)),
        )
        char_id = cur.lastrowid
        conn.commit()
    finally:
        conn.close()
    scene = client.post(
        f"/api/projects/{pid}/scenes",
        json={"order_index": 1, "heading": "INT. RUINENWOHNUNG - NACHT",
              "dialog": "Iselle Lacourt: (lächelnd) Ich freue mich auch, dich zu sehen.",
              "character_ids": [char_id]},
    ).json()
    conn = get_db(settings.db_path)
    try:
        conn.execute("INSERT OR IGNORE INTO scene_characters (scene_id, character_id) VALUES (?, ?)",
                     (scene["id"], char_id))
        conn.execute(
            "UPDATE clips SET description = ?, shot_size = 'closeUp', dialog_lines_covered = '[1]' WHERE scene_id = ?",
            ("Close-up: Iselle smiles in the doorway, 'Ich freue mich auch, dich zu sehen'.", scene["id"]),
        )
        conn.commit()
    finally:
        conn.close()
    return pid, scene["id"]


def test_format_is_stored_and_returned(client):
    pid = client.post("/api/projects", json={"title": "M", "format": "manga"}).json()["id"]
    assert client.get(f"/api/projects/{pid}").json()["format"] == "manga"
    assert client.post("/api/projects", json={"title": "X", "format": "film"}).status_code == 422


def test_plan_builds_english_prompt_bubbles_and_caption(client, tmp_path):
    pid, _ = _comic_project(client, "webtoon", tmp_path)
    panels = client.post(f"/api/projects/{pid}/panels/plan").json()
    assert len(panels) == 1
    p = panels[0]
    assert p["prompt"].startswith("Single full-frame illustration") and "webtoon" in p["prompt"]
    assert "Ich freue mich" not in p["prompt"]  # quoted speech never reaches the image model
    assert p["bubbles"] == [{"speaker": "Iselle Lacourt", "text": "Ich freue mich auch, dich zu sehen.",
                             "x": None, "y": None}]
    assert p["caption"] == "RUINENWOHNUNG – NACHT"
    assert (p["width"], p["height"]) == (896, 1152)
    assert len(p["refs"]) == 1 and Path(p["refs"][0]).exists()  # cropped head view


def test_bubble_edit_and_webtoon_export(client, tmp_path):
    pid, _ = _comic_project(client, "webtoon", tmp_path)
    panel = client.post(f"/api/projects/{pid}/panels/plan").json()[0]
    moved = client.patch(f"/api/panels/{panel['id']}",
                         json={"bubbles": [{**panel["bubbles"][0], "x": 0.2, "y": 0.4}]}).json()
    assert moved["bubbles"][0]["x"] == 0.2
    assert client.post(f"/api/projects/{pid}/comic/export").status_code == 422  # nothing rendered yet
    img = tmp_path / "panel.png"
    Image.new("RGB", (896, 1152), "gray").save(img)
    conn = get_db(settings.db_path)
    try:
        conn.execute("UPDATE panels SET image_path = ? WHERE id = ?", (str(img), panel["id"]))
        conn.commit()
    finally:
        conn.close()
    files = client.post(f"/api/projects/{pid}/comic/export").json()["files"]
    assert any(f.endswith("webtoon.png") for f in files) and all(Path(f).exists() for f in files)


def test_manga_export_writes_pages_and_pdf(client, tmp_path):
    pid, _ = _comic_project(client, "manga", tmp_path)
    panel = client.post(f"/api/projects/{pid}/panels/plan").json()[0]
    assert "manga" in panel["prompt"]
    img = tmp_path / "panel.png"
    Image.new("RGB", (896, 1152), "gray").save(img)
    conn = get_db(settings.db_path)
    try:
        conn.execute("UPDATE panels SET image_path = ? WHERE id = ?", (str(img), panel["id"]))
        conn.commit()
    finally:
        conn.close()
    files = client.post(f"/api/projects/{pid}/comic/export", json={"rtl": True}).json()["files"]
    assert files[0].endswith("manga.pdf") and any("seite_01" in f for f in files)


def test_render_queues_a_job_per_missing_panel(client, tmp_path):
    pid, _ = _comic_project(client, "webtoon", tmp_path)
    client.post(f"/api/projects/{pid}/panels/plan")
    wf = {"10": {"class_type": "PrimitiveStringMultiline", "inputs": {"value": "x"}, "_meta": {"title": "Prompt (Input:prompt)"}},
          "40": {"class_type": "SaveImage", "inputs": {"images": ["10", 0]}, "_meta": {"title": "Out (Output:image)"}}}
    client.post("/api/workflows", json={"name": "Klein text", "kind": "image", "workflow_json": wf})
    r = client.post(f"/api/projects/{pid}/panels/render").json()
    assert r["queued"] == 1  # no 1-reference workflow -> falls back to text only
    panel = client.get(f"/api/projects/{pid}/panels").json()[0]
    assert panel["job_id"] == r["job_ids"][0]
    payload = json.loads(get_db(settings.db_path).execute(
        "SELECT payload_json FROM jobs WHERE id = ?", (panel["job_id"],)).fetchone()[0])
    assert payload["panel_id"] == panel["id"] and payload["input_values"]["10"] == panel["prompt"]


def test_comic_lengths_count_panels_not_seconds():
    from calliope.agent.prompts import recommend_beat_count, recommend_scene_count

    assert recommend_beat_count("20 panels", "webtoon") == 6
    assert recommend_scene_count("20 panels", "webtoon") == 8
    assert recommend_beat_count("16 pages", "manga") == 23  # 80 panels
    assert recommend_beat_count("2 minutes") == 10  # video unchanged


def test_comic_story_prompt_has_no_video_wording():
    from calliope.agent.prompts import story_generation_user_prompt, story_system

    webtoon = story_generation_user_prompt("T", "idea", "Sci-Fi", "dark", "20 panels", "webtoon")
    assert "webtoon episode" in webtoon and "cliffhanger" in webtoon
    assert "video" not in webtoon.lower() and "seconds" not in webtoon
    assert "manga studio" in story_system("manga")
    assert "AI-generated video" in story_generation_user_prompt("T", "i", "g", "t", "2 minutes")


def test_comic_script_and_panels_use_comic_prompts(client, monkeypatch):
    import calliope.agent.coverage_agent as coverage
    import calliope.agent.script_agent as script

    seen: list[str] = []

    async def fake_script(messages, temperature=0.7, **kw):
        seen.append(messages[0]["content"] + messages[1]["content"])
        n = int(__import__("re").search(r"exactly (\d+) scenes", messages[1]["content"]).group(1))
        return {"scenes": [{"heading": "INT. HALL - NIGHT", "action": "Mia waits.", "dialog": "MIA: Hallo?\nMIA: Ist da wer?",
                            "duration_sec": 2} for _ in range(n)]}

    async def fake_cover(messages, temperature=0.5, **kw):
        seen.append(messages[0]["content"] + messages[1]["content"])
        return {"clips": [{"order_index": 1, "description": "Wide shot: Mia in the hall.",
                           "dialog_lines_covered": [1, 2], "shot_size": "wide", "duration_sec": 1}]}

    monkeypatch.setattr(script, "generate_structured", fake_script)
    monkeypatch.setattr(coverage, "generate_structured", fake_cover)
    pid = client.post("/api/projects", json={"title": "W", "format": "webtoon", "idea": "x",
                                              "target_duration": "10 panels"}).json()["id"]
    r = client.post(f"/api/projects/{pid}/generate-script")
    assert r.status_code == 200, r.text
    assert any("webtoon episode" in s and "STILL images" in s for s in seen)
    assert any("PANELS (still images)" in s for s in seen)
    assert not any("AI-video shot clips" in s for s in seen)


def test_story_text_follows_the_idea_language():
    from calliope.agent.comic_prompts import comic_script_chunk_messages
    from calliope.agent.prompts import story_generation_user_prompt

    de = ("Eine junge Bäckerin namens Lina entdeckt, dass ihr Sauerteig sprechen kann. Er warnt sie vor einem "
          "Drachen, der die Stadt bedroht, und sie backen zusammen ein Brot für den Drachen.")
    assert "LANGUAGE: write titles" in story_generation_user_prompt("T", de, "g", "t", "10 panels", "webtoon")
    script = comic_script_chunk_messages(
        fmt="webtoon", title="T", idea=de, beats=[], characters=[], locations=[], target_duration="10 panels",
        scene_count=4, chunk_start=1, chunk_scenes=4)[1]["content"]
    assert "every dialogue line in German" in script and "action stay in English" in script
    en = "A young baker named Lina finds out that her sourdough starter can talk and warns her about the dragon in the town."
    assert "LANGUAGE" not in story_generation_user_prompt("T", en, "g", "t", "2 minutes")
