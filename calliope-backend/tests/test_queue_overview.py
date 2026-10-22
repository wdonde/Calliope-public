"""The queue overview names jobs and keeps queue order."""
from __future__ import annotations

from calliope.queue.manager import queue_manager


def test_active_jobs_are_labelled_in_queue_order(client):
    a = client.post("/api/projects", json={"title": "A"}).json()["id"]
    b = client.post("/api/projects", json={"title": "B"}).json()["id"]
    reed = client.post(f"/api/projects/{a}/characters", json={"name": "Reed"}).json()
    client.post("/api/jobs/pause")  # keep the worker from picking them up
    j1 = queue_manager.enqueue(project_id=b, kind="image", workflow_id=None, payload={"colorize_source": "/x/page_3.png"})
    j2 = queue_manager.enqueue(project_id=a, kind="image", workflow_id=None, payload={"character_id": reed["id"]})

    everything = client.get("/api/jobs/active").json()
    assert [(j["label"], j["position"]) for j in everything["queued"]] == [
        ("Colorize · page_3.png", 1), ("Character sheet · Reed", 2)]
    mine = client.get("/api/jobs/active", params={"project_id": a}).json()
    assert [j["label"] for j in mine["queued"]] == ["Character sheet · Reed"] and mine["queued"][0]["position"] == 2
    assert mine["elsewhere"] == {"running": 0, "queued": 1} and mine["queued"][0]["project"] == "A"
    assert client.get(f"/api/jobs/{j2['id']}").status_code == 200  # /active does not shadow /{id}
    client.post("/api/jobs/resume")


def test_forecast_uses_the_workflows_usual_duration(client):
    from calliope.config import settings
    from calliope.db import get_db

    pid = client.post("/api/projects", json={"title": "F"}).json()["id"]
    wf = client.post("/api/workflows", json={"name": "W", "kind": "image", "workflow_json": {
        "10": {"class_type": "PrimitiveStringMultiline", "inputs": {"value": "x"}, "_meta": {"title": "Prompt (Input:prompt)"}},
        "40": {"class_type": "SaveImage", "inputs": {"images": ["10", 0]}, "_meta": {"title": "Out (Output:image)"}}}}).json()["id"]
    client.post("/api/jobs/pause")
    conn = get_db(settings.db_path)
    try:
        for minutes in (4, 6):  # two finished jobs of workflow 7: usually 5 minutes
            conn.execute("INSERT INTO jobs (project_id, kind, workflow_id, status, payload_json, created_at, started_at, "
                         "completed_at) VALUES (?, 'image', ?, 'done', '{}', datetime('now','-1 hour'), "
                         "datetime('now','-1 hour'), datetime('now','-1 hour', ?))", (pid, wf, f"+{minutes} minutes"))
        conn.commit()
    finally:
        conn.close()
    queue_manager.enqueue(project_id=pid, kind="image", workflow_id=wf, payload={})
    queue_manager.enqueue(project_id=pid, kind="image", workflow_id=wf, payload={})
    d = client.get("/api/jobs/active", params={"project_id": pid}).json()
    assert [j["typical_sec"] for j in d["queued"]] == [300, 300]
    assert [j["remaining_sec"] for j in d["queued"]] == [300, 600]
    assert d["finish_at"] == d["queued"][-1]["eta"] and d["queued"][0]["estimate_from"] == "workflow"
    client.post("/api/jobs/resume")
