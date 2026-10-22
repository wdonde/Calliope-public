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
