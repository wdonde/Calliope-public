"""Dependency map: graph of a project, findings, and the fixes it offers."""
from __future__ import annotations

from calliope.config import settings
from calliope.db import get_db


def _project(client) -> tuple[int, dict[str, int]]:
    pid = client.post("/api/projects", json={"title": "Map", "format": "webtoon"}).json()["id"]
    ids = {}
    for name in ("Captain Reed", "Reed", "Mia"):
        ids[name] = client.post(f"/api/projects/{pid}/characters", json={"name": name}).json()["id"]
    loc = client.post(f"/api/projects/{pid}/locations", json={"name": "Bridge"}).json()["id"]
    client.post(f"/api/projects/{pid}/locations", json={"name": "bridge "})
    scene = client.post(f"/api/projects/{pid}/scenes", json={
        "order_index": 1, "heading": "INT. BRIDGE", "character_ids": [ids["Captain Reed"]]}).json()["id"]
    conn = get_db(settings.db_path)
    conn.execute("INSERT OR IGNORE INTO scene_characters (scene_id, character_id) VALUES (?, ?)", (scene, ids["Reed"]))
    conn.execute("UPDATE scenes SET location_id = ? WHERE id = ?", (loc, scene))
    conn.commit()
    conn.close()
    ids["scene"], ids["loc"] = scene, loc
    return pid, ids


def test_map_shows_connections_and_findings(client):
    pid, ids = _project(client)
    g = client.get(f"/api/projects/{pid}/dependencies").json()
    kinds = {n["id"]: n["kind"] for n in g["nodes"]}
    assert kinds[f"character:{ids['Mia']}"] == "character" and kinds[f"scene:{ids['scene']}"] == "scene"
    rels = {(e["source"], e["target"]): e["rel"] for e in g["edges"]}
    assert rels[(f"character:{ids['Reed']}", f"scene:{ids['scene']}")] == "cast"
    assert rels[(f"location:{ids['loc']}", f"scene:{ids['scene']}")] == "location"
    types = [(f["type"], f["nodes"]) for f in g["findings"]]
    assert ("unused", [f"character:{ids['Mia']}"]) in types
    assert ("duplicate", [f"character:{ids['Captain Reed']}", f"character:{ids['Reed']}"]) in types
    assert any(t == "duplicate" and n[0].startswith("location:") for t, n in types)
    assert any(t == "no_panel" for t, _ in types)  # a scene gets a first clip; comics need panels
    with_prompts = client.get(f"/api/projects/{pid}/dependencies?prompts=true").json()
    assert any(n["kind"] == "prompt" for n in with_prompts["nodes"])


def test_unlink_link_and_merge(client):
    pid, ids = _project(client)
    body = {"kind": "scene_character", "source_id": ids["Reed"], "target_id": ids["scene"]}
    client.post(f"/api/projects/{pid}/dependencies/unlink", json=body)
    edges = client.get(f"/api/projects/{pid}/dependencies").json()["edges"]
    assert not any(e["source"] == f"character:{ids['Reed']}" for e in edges)
    client.post(f"/api/projects/{pid}/dependencies/link", json=body)
    kept = client.post(f"/api/projects/{pid}/dependencies/merge", json={
        "kind": "character", "keep_id": ids["Captain Reed"], "drop_id": ids["Reed"]}).json()
    assert kept["alias"] == "Reed"
    g = client.get(f"/api/projects/{pid}/dependencies").json()
    assert f"character:{ids['Reed']}" not in {n["id"] for n in g["nodes"]}
    assert not any(f["type"] == "duplicate" and f["nodes"][0].startswith("character:") for f in g["findings"])
    assert client.post(f"/api/projects/{pid}/dependencies/merge", json={
        "kind": "character", "keep_id": ids["Mia"], "drop_id": ids["Mia"]}).status_code == 404
