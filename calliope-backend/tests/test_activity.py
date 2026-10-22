"""The busy indicator: LLM steps, agent turns and render jobs per project."""
import asyncio

from calliope import activity


def test_idle_project_is_not_busy(client):
    pid = client.post("/api/projects", json={"title": "Idle"}).json()["id"]
    r = client.get(f"/api/activity?project_id={pid}").json()
    assert r["busy"] is False and r["operations"] == [] and r["jobs"]["queued"] == 0


def test_tracked_operation_and_progress_show_up(client):
    pid = client.post("/api/projects", json={"title": "Busy"}).json()["id"]

    async def scenario():
        async with activity.track(pid, "condense", "Summarizing the novel"):
            activity.note(pid, "Reading part 2/5")
            return activity.operations(pid)

    ops = asyncio.run(scenario())
    assert ops[0]["kind"] == "condense" and ops[0]["step"] == "Reading part 2/5"
    assert activity.operations(pid) == []  # gone once finished


def test_queued_job_marks_project_busy(client):
    from calliope.queue.manager import queue_manager

    pid = client.post("/api/projects", json={"title": "Jobs"}).json()["id"]
    queue_manager.paused = True  # keep the job queued
    try:
        queue_manager.enqueue(project_id=pid, kind="image", payload={})
        r = client.get(f"/api/activity?project_id={pid}").json()
        assert r["busy"] and r["jobs"]["queued"] + r["jobs"]["running"] == 1
        assert str(pid) in client.get("/api/activity").json()["projects"]
    finally:
        queue_manager.paused = False
