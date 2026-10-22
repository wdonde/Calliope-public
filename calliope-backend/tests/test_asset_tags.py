"""Asset tags, item categories and the cross-project asset library."""
from __future__ import annotations

from pathlib import Path

from PIL import Image

from calliope.agent.asset_tags import aliases, name_tags, normalize_tags


def test_tags_are_cleaned_and_placeholders_dropped():
    assert normalize_tags(["Alias : Ripper Eddy", "alias:ripper eddy", " villain ", "type:interior|exterior",
                           "alias:other names, if any", "", 5]) == ["alias:Ripper Eddy", "villain"]
    assert name_tags("Captain Edward 'Ripper Eddy' Reed") == ["alias:Ripper Eddy"]
    assert name_tags("Shane O'Brien") == []
    assert aliases({"tags": ["alias:Eddy", "faction:Union", "nickname:Rip"]}) == ["Eddy", "Rip"]


def test_tags_and_category_round_trip_through_the_api(client):
    pid = client.post("/api/projects", json={"title": "T"}).json()["id"]
    c = client.post(f"/api/projects/{pid}/characters",
                    json={"name": "Captain Edward 'Ripper Eddy' Reed", "tags": ["faction:Union Guard"]}).json()
    assert c["tags"] == ["alias:Ripper Eddy", "faction:Union Guard"]
    item = client.post(f"/api/projects/{pid}/items", json={"name": "Rifle", "category": "Weapon"}).json()
    assert item["category"] == "weapon" and item["tags"] == []
    item = client.patch(f"/api/projects/{pid}/items/{item['id']}", json={"tags": ["owner:Reed"]}).json()
    assert item["tags"] == ["owner:Reed"]
    assets = client.get(f"/api/projects/{pid}/assets").json()
    assert assets["characters"][0]["tags"][0] == "alias:Ripper Eddy"


def test_panel_finds_character_by_alias():
    from calliope.agent.comic import _panel_characters

    reed = {"name": "Captain Edward Reed", "tags": ["alias:Ripper Eddy"], "sheet_path": __file__}
    focus, _ = _panel_characters("Close-up: Eddy grins.", [reed, {"name": "Malone", "tags": []}])
    assert focus == [reed]


def test_library_copies_assets_between_projects(client, tmp_path):
    from calliope.config import settings

    a = client.post("/api/projects", json={"title": "A"}).json()["id"]
    b = client.post("/api/projects", json={"title": "B"}).json()["id"]
    c = client.post(f"/api/projects/{a}/characters", json={"name": "Reed", "appearance": "grey buzz cut",
                                                          "tags": ["alias:Eddy"]}).json()
    sheet = Path(settings.assets_dir) / str(a) / "sheet.png"
    sheet.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (60, 40), "white").save(sheet)
    client.patch(f"/api/projects/{a}/characters/{c['id']}", json={"sheet_path": str(sheet)})

    lib = client.post(f"/api/projects/{a}/character/{c['id']}/to-library").json()
    assert lib["kind"] == "character" and lib["tags"] == ["alias:Eddy"]
    assert lib["image_path"] and Path(lib["image_path"]).exists() and lib["image_path"] != str(sheet)
    assert [x["id"] for x in client.get("/api/asset-library", params={"q": "buzz"}).json()] == [lib["id"]]
    assert client.get("/api/asset-library", params={"tag": "alias:eddy"}).json()[0]["id"] == lib["id"]
    assert client.get("/api/asset-library", params={"kind": "item"}).json() == []

    added = client.post(f"/api/asset-library/{lib['id']}/to-project", json={"project_id": b}).json()
    assert added["project_id"] == b and added["appearance"] == "grey buzz cut"
    assert f"library:{lib['id']}" in added["tags"] and Path(added["sheet_path"]).exists()

    assert client.patch(f"/api/asset-library/{lib['id']}", json={"tags": ["hero"]}).json()["tags"] == ["hero"]
    assert client.delete(f"/api/asset-library/{lib['id']}").json() == {"ok": True}
    assert not Path(lib["image_path"]).exists() and Path(added["sheet_path"]).exists()


def test_new_character_from_pictures_is_described_and_rendered(client, monkeypatch):
    import calliope.agent.asset_create as ac
    from calliope.config import settings

    seen = {}

    async def fake_llm(messages, temperature=0.4, role=None):
        seen["role"] = role
        seen["images"] = sum(1 for p in messages[1]["content"] if isinstance(p, dict) and p["type"] == "image_url")
        return {"appearance": "50s, grey buzz cut, scar over left eye, olive field jacket", "tags": ["faction:Union"]}

    monkeypatch.setattr(ac, "generate_structured", fake_llm)
    wf = {"10": {"class_type": "PrimitiveStringMultiline", "inputs": {"value": "x"}, "_meta": {"title": "Prompt (Input:prompt)"}},
          "50": {"class_type": "LoadImage", "inputs": {"image": ""}, "_meta": {"title": "Ref (Input:image)"}},
          "60": {"class_type": "LoadImage", "inputs": {"image": ""}, "_meta": {"title": "Ref 2 (Input:image)"}},
          "40": {"class_type": "SaveImage", "inputs": {"images": ["10", 0]}, "_meta": {"title": "Out (Output:image)"}}}
    client.post("/api/workflows", json={"name": "Klein 2 refs", "kind": "image", "workflow_json": wf})
    pid = client.post("/api/projects", json={"title": "N"}).json()["id"]
    up = Path(settings.assets_dir) / "uploads"
    up.mkdir(parents=True, exist_ok=True)
    pics = []
    for i in range(3):
        p = up / f"ref{i}.png"
        Image.new("RGB", (80, 120), (40 * i, 90, 90)).save(p)
        pics.append(str(p))

    r = client.post(f"/api/projects/{pid}/assets/new", json={
        "kind": "character", "name": "Captain Edward 'Ripper Eddy' Reed",
        "prompt": "Alter Soldat, Narbe", "image_paths": pics[:2]}).json()
    a = r["asset"]
    assert seen == {"role": "assets", "images": 2}
    assert a["appearance"].startswith("50s, grey buzz cut") and a["tags"] == ["alias:Ripper Eddy", "faction:Union"]
    assert len(a["source_images"]) == 2 and all(str(pid) in p for p in a["source_images"])
    job = client.get(f"/api/jobs/{r['job_id']}").json()
    values = (job.get("payload") or json.loads(job["payload_json"]))["input_values"]
    assert values["50"] == a["source_images"][0] and values["60"] == a["source_images"][1]

    # Three pictures become one collage, which needs a one-reference workflow -- there is none here.
    r3 = client.post(f"/api/projects/{pid}/assets/new", json={
        "kind": "item", "name": "Rifle", "image_paths": pics, "generate": False}).json()
    assert r3["job_id"] is None and len(r3["asset"]["source_images"]) == 3
    assert client.post(f"/api/projects/{pid}/item/{r3['asset']['id']}/from-images").status_code == 422
    assert Path(r3["asset"]["source_images"][0]).with_name("collage.png").exists()
    assert client.post(f"/api/projects/{pid}/assets/new", json={
        "kind": "item", "name": "X", "image_paths": ["/etc/passwd"]}).status_code == 422
