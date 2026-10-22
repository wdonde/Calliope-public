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
