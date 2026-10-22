"""Workflow roles: assignments per job, Auto fallback, input-count check."""
from __future__ import annotations


def _wf(images: int) -> dict:
    wf = {"10": {"class_type": "PrimitiveStringMultiline", "inputs": {"value": "x"}, "_meta": {"title": "Prompt (Input:prompt)"}},
          "40": {"class_type": "SaveImage", "inputs": {"images": ["10", 0]}, "_meta": {"title": "Out (Output:image)"}}}
    for i in range(images):
        wf[str(50 + 10 * i)] = {"class_type": "LoadImage", "inputs": {"image": ""}, "_meta": {"title": f"R{i} (Input:image)"}}
    return wf


def test_roles_resolve_assigned_or_auto(client):
    ids = {}
    for name, images in [("Z text", 0), ("Klein text", 0), ("Klein edit", 1), ("Other edit", 1), ("Klein 2", 2)]:
        ids[name] = client.post("/api/workflows", json={"name": name, "kind": "image", "workflow_json": _wf(images)}).json()["id"]
    roles = {r["key"]: r for r in client.get("/api/settings/workflow-roles").json()}
    assert roles["asset_sheet"]["resolved"] == ids["Klein text"]  # Auto: Klein first
    assert {c["id"] for c in roles["colorize"]["candidates"]} == {ids["Klein edit"], ids["Other edit"]}

    client.post("/api/settings", json={"workflow_assignments": {"asset_sheet": ids["Z text"], "colorize": ids["Other edit"],
                                                               "panel_refs_2": ids["Klein edit"], "bogus": 1}})
    roles = {r["key"]: r for r in client.get("/api/settings/workflow-roles").json()}
    assert roles["asset_sheet"]["resolved"] == ids["Z text"] and roles["colorize"]["resolved"] == ids["Other edit"]
    assert roles["panel_refs_2"]["resolved"] == ids["Klein 2"]  # wrong input count -> Auto
    assert "bogus" not in client.get("/api/settings").json()["workflow_assignments"]

    from calliope.agent.colorize import _one_ref_workflow
    from calliope.agent.comic import _pick_workflow

    assert _one_ref_workflow() == ids["Other edit"] and _pick_workflow(2) == ids["Klein 2"]
    client.post("/api/settings", json={"workflow_assignments": {}})
