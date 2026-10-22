"""Asset tags, item categories and the cross-project asset library."""
from __future__ import annotations

import asyncio
import json
import re
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

    # Three pictures: the two best go in at full size (no collage).
    r3 = client.post(f"/api/projects/{pid}/assets/new", json={
        "kind": "item", "name": "Rifle", "image_paths": pics, "generate": False}).json()
    assert r3["job_id"] is None and len(r3["asset"]["source_images"]) == 3
    again = client.post(f"/api/projects/{pid}/item/{r3['asset']['id']}/from-images").json()
    values = client.get(f"/api/jobs/{again['job_id']}").json()["payload"]["input_values"]
    assert values["50"] in r3["asset"]["source_images"] and values["60"] in r3["asset"]["source_images"]
    assert not Path(r3["asset"]["source_images"][0]).with_name("collage.png").exists()
    assert client.post(f"/api/projects/{pid}/assets/new", json={
        "kind": "item", "name": "X", "image_paths": ["/etc/passwd"]}).status_code == 422


def test_library_asset_is_created_rendered_and_carried_into_projects(client, monkeypatch):
    import calliope.agent.asset_create as ac
    from calliope.config import settings

    async def fake_llm(messages, temperature=0.4, role=None):
        return {"description": "matte black rifle with a red sight", "tags": ["owner:Reed"], "category": "weapon"}

    monkeypatch.setattr(ac, "generate_structured", fake_llm)
    wf = {"10": {"class_type": "PrimitiveStringMultiline", "inputs": {"value": "x"}, "_meta": {"title": "Prompt (Input:prompt)"}},
          "50": {"class_type": "LoadImage", "inputs": {"image": ""}, "_meta": {"title": "Ref (Input:image)"}},
          "40": {"class_type": "SaveImage", "inputs": {"images": ["10", 0]}, "_meta": {"title": "Out (Output:image)"}}}
    client.post("/api/workflows", json={"name": "Klein 1 ref", "kind": "image", "workflow_json": wf})
    up = Path(settings.assets_dir) / "uploads"
    up.mkdir(parents=True, exist_ok=True)
    pic = up / "rifle.png"
    Image.new("RGB", (100, 60), "black").save(pic)

    r = client.post("/api/asset-library/new", json={"kind": "item", "name": "Rifle", "image_paths": [str(pic)]}).json()
    a = client.get("/api/asset-library").json()[0]
    assert a["id"] == r["asset_id"] and a["category"] == "weapon" and a["tags"] == ["owner:Reed"]
    assert a["data"]["pending_job"] == r["job_id"] and a["image_path"] is None
    job = client.get(f"/api/jobs/{r['job_id']}").json()
    assert (job.get("payload") or json.loads(job["payload_json"]))["library_asset_id"] == a["id"]

    out = Path(settings.assets_dir) / "render.png"
    Image.new("RGB", (64, 64), "gray").save(out)
    ac.library_render_done(a["id"], str(out))  # what the worker does when the job finishes
    a = client.get("/api/asset-library").json()[0]
    assert a["image_path"] and Path(a["image_path"]).exists() and "pending_job" not in a["data"]

    pid = client.post("/api/projects", json={"title": "P"}).json()["id"]
    item = client.post(f"/api/asset-library/{a['id']}/to-project", json={"project_id": pid}).json()
    assert item["category"] == "weapon" and Path(item["reference_image_path"]).exists()
    assert len(item["source_images"]) == 1 and Path(item["source_images"][0]).exists()


def test_library_asset_edit_shows_projects_and_renders_from_prompt(client):
    from calliope.config import settings

    wf = {"10": {"class_type": "PrimitiveStringMultiline", "inputs": {"value": "x"}, "_meta": {"title": "Prompt (Input:prompt)"}},
          "40": {"class_type": "SaveImage", "inputs": {"images": ["10", 0]}, "_meta": {"title": "Out (Output:image)"}}}
    client.post("/api/workflows", json={"name": "Klein text", "kind": "image", "workflow_json": wf})
    a = client.post("/api/projects", json={"title": "Alien Nation"}).json()["id"]
    b = client.post("/api/projects", json={"title": "Sequel"}).json()["id"]
    c = client.post(f"/api/projects/{a}/characters", json={"name": "Reed", "appearance": "grey hair"}).json()
    lib = client.post(f"/api/projects/{a}/character/{c['id']}/to-library").json()
    client.post(f"/api/asset-library/{lib['id']}/to-project", json={"project_id": b})
    listed = client.get("/api/asset-library").json()[0]
    assert listed["source_project"] == {"id": a, "title": "Alien Nation"}
    assert listed["used_in"] == [{"id": b, "title": "Sequel", "outdated": False}]

    pic = Path(settings.assets_dir) / "uploads" / "reed.png"
    pic.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (40, 40), "white").save(pic)
    edited = client.patch(f"/api/asset-library/{lib['id']}", json={
        "name": "Edward Reed", "data": {"appearance": "grey buzz cut", "consistency_prompt": "SHEET of Reed",
                                         "bogus": "x"},
        "source_images": [str(pic)]}).json()
    assert edited["name"] == "Edward Reed" and edited["data"]["appearance"] == "grey buzz cut"
    assert "bogus" not in edited["data"] and edited["used_in"][0]["id"] == b
    assert len(edited["data"]["source_images"]) == 1 and "/library/" in edited["data"]["source_images"][0]
    assert client.patch(f"/api/asset-library/{lib['id']}", json={"source_images": ["/etc/passwd"]}).status_code == 422

    cleared = client.patch(f"/api/asset-library/{lib['id']}", json={"source_images": []}).json()
    assert cleared["data"]["source_images"] == []
    job_id = client.post(f"/api/asset-library/{lib['id']}/from-images").json()["job_id"]
    job = client.get(f"/api/jobs/{job_id}").json()
    assert (job.get("payload") or json.loads(job["payload_json"]))["input_values"]["10"] == "SHEET of Reed"


def test_missing_look_is_generated_as_a_character_sheet(client, monkeypatch):
    import calliope.agent.asset_create as ac

    async def fake_llm(messages, temperature=0.4, role=None):
        return {"look": {"Age": "52", "Gender": "male", "Species": "human", "Hair": "grey buzz cut",
                         "Distinctive features": "scar over left eye", "Headwear": "none",
                         "Outerwear": "olive field jacket", "Color palette": "olive, grey, black"},
                "tags": ["faction:Union"]}

    monkeypatch.setattr(ac, "generate_structured", fake_llm)
    pid = client.post("/api/projects", json={"title": "T"}).json()["id"]
    c = client.post(f"/api/projects/{pid}/characters", json={"name": "Reed"}).json()
    out = client.post(f"/api/projects/{pid}/character/{c['id']}/describe").json()
    text = out["appearance"]
    assert text.startswith("CHARACTER INFORMATION\nName: Reed\nAge: 52") and "Species" not in text
    assert "\n\nOUTFIT\nHeadwear: none\nOuterwear: olive field jacket" in text and out["tags"] == ["faction:Union"]

    from calliope.agent.prompts import appearance_line

    line = appearance_line(text)
    assert line.startswith("Age: 52; Gender: male") and "OUTFIT" not in line and "Name:" not in line


def test_suggested_tags_do_not_add_a_second_value_for_a_key():
    from calliope.agent.asset_tags import merge_tags

    assert merge_tags(["faction:Military", "alias:Eddy"], ["faction:Human Military", "alias:Reed", "role:hero"]) == [
        "faction:Military", "alias:Eddy", "alias:Reed", "role:hero"]


SHEET = """CHARACTER INFORMATION
Name: Captain Edward 'Ripper Eddy' Reed
Age: 45
Gender: male
Build: muscular
Hair: short, graying, military cut
Distinctive features: prominent scar across left cheek

OUTFIT
Headwear: tactical helmet
Outerwear: tactical vest"""


def test_library_keeps_only_the_identity_and_projects_find_and_use_it(client):
    a = client.post("/api/projects", json={"title": "Alien Nation"}).json()["id"]
    c = client.post(f"/api/projects/{a}/characters", json={
        "name": "Captain Edward 'Ripper Eddy' Reed", "role": "Protagonist", "appearance": SHEET,
        "personality": "grim, loyal"}).json()
    lib = client.post(f"/api/projects/{a}/character/{c['id']}/to-library").json()
    assert lib["data"]["appearance"].endswith("scar across left cheek") and "OUTFIT" not in lib["data"]["appearance"]
    assert "personality" not in lib["data"] and "role" not in lib["data"]
    assert lib["tags"] == ["alias:Ripper Eddy", "role:protagonist", "gender:male", "age:45", "build:muscular",
                           "hair:short, graying, military cut", "mark:prominent scar across left cheek"]

    # Another project has its own Reed in a pilot outfit.
    b = client.post("/api/projects", json={"title": "Sequel"}).json()["id"]
    own = client.post(f"/api/projects/{b}/characters", json={
        "name": "Eddy Reed", "role": "protagonist",
        "appearance": "CHARACTER INFORMATION\nName: Eddy Reed\nAge: 47\nGender: male\n\nOUTFIT\nTop: orange flight suit"}).json()
    client.post(f"/api/projects/{b}/characters", json={"name": "Malone", "appearance": "Gender: female"})
    matches = client.get(f"/api/projects/{b}/library-matches").json()
    assert [m["character_id"] for m in matches] == [own["id"]]
    best = matches[0]["matches"][0]
    assert best["asset_id"] == lib["id"] and "name: eddy, reed" in best["why"] and "gender: male" in best["why"]

    used = client.post(f"/api/asset-library/{lib['id']}/apply/{b}/{own['id']}").json()["character"]
    assert used["appearance"].startswith("CHARACTER INFORMATION\nName: Captain Edward")
    assert used["appearance"].endswith("OUTFIT\nTop: orange flight suit")  # the project's outfit stays
    assert f"library:{lib['id']}" in used["tags"] and "alias:Ripper Eddy" in used["tags"]
    assert client.get("/api/asset-library").json()[0]["used_in"][0]["id"] == b

    edited = client.patch(f"/api/asset-library/{lib['id']}", json={
        "data": {"appearance": SHEET.replace("military cut", "shaved")}}).json()
    assert "hair:short, graying, shaved" in edited["tags"] and "OUTFIT" not in edited["data"]["appearance"]



def test_linking_keeps_prose_outfit_locks_identity_and_updates_from_library(client, monkeypatch):
    import calliope.agent.asset_create as ac

    calls = []

    async def fake_llm(messages, temperature=0.4, role=None):
        text = messages[1]["content"] if isinstance(messages[1]["content"], str) else messages[1]["content"][0]["text"]
        calls.append(text)
        if "Do not invent anything" in text:  # prose -> outfit
            return {"look": {"Headwear": "none", "Outerwear": "orange flight jacket", "Footwear": "black boots"}}
        return {"look": {"Outerwear": "white parade coat", "Top": "grey shirt"}, "tags": []}

    monkeypatch.setattr(ac, "generate_structured", fake_llm)
    a = client.post("/api/projects", json={"title": "Alien Nation"}).json()["id"]
    src = client.post(f"/api/projects/{a}/characters", json={"name": "Edward Reed", "appearance": SHEET}).json()
    lib = client.post(f"/api/projects/{a}/character/{src['id']}/to-library").json()

    b = client.post("/api/projects", json={"title": "Sequel"}).json()["id"]
    prose = client.post(f"/api/projects/{b}/characters", json={
        "name": "Reed", "appearance": "A pilot in an orange flight jacket and black boots."}).json()
    client.patch(f"/api/projects/{b}/characters/{prose['id']}", json={"consistency_prompt": "MY OWN PROMPT"})

    r = client.post(f"/api/asset-library/{lib['id']}/apply/{b}/{prose['id']}").json()
    c = r["character"]
    assert c["appearance"].startswith("CHARACTER INFORMATION\nName: Captain Edward")
    assert "OUTFIT\nHeadwear: none\nOuterwear: orange flight jacket" in c["appearance"]  # prose outfit kept
    assert r["prompt_kept"] is True and c["consistency_prompt"] == "MY OWN PROMPT"
    again = client.post(f"/api/asset-library/{lib['id']}/apply/{b}/{prose['id']}", json={"replace_prompt": True}).json()
    assert again["prompt_kept"] is False and again["character"]["consistency_prompt"].startswith("CHARACTER SHEET")

    # Generate on a linked character writes the outfit only; the identity stays.
    d = client.post(f"/api/projects/{b}/character/{prose['id']}/describe").json()
    assert d["appearance"].startswith("CHARACTER INFORMATION\nName: Captain Edward") and "Age: 45" in d["appearance"]
    assert "Outerwear: white parade coat" in d["appearance"] and "orange" not in d["appearance"]

    # The library changes -> the project sees it as outdated and can update.
    import time
    time.sleep(1.1)
    client.patch(f"/api/asset-library/{lib['id']}", json={"data": {"appearance": SHEET.replace("Age: 45", "Age: 50")}})
    m = client.get(f"/api/projects/{b}/library-matches").json()[0]["matches"][0]
    assert m["linked"] and m["outdated"]
    assert [u["outdated"] for u in client.get("/api/asset-library").json()[0]["used_in"]] == [True]
    up = client.post(f"/api/projects/{b}/character/{prose['id']}/update-from-library").json()["character"]
    assert "Age: 50" in up["appearance"] and "Outerwear: white parade coat" in up["appearance"]
    assert not client.get(f"/api/projects/{b}/library-matches").json()[0]["matches"][0]["outdated"]


def test_draft_storyline_writes_characters_as_sheets(client, monkeypatch):
    import calliope.routers.story as story_router

    async def fake(messages, temperature=0.7, **kw):
        user = messages[1]["content"]
        assert '"look": {"Age"' in user and "Headwear" in user  # the schema asks for the sheet
        m = re.search(r"exactly (\d+) beats", user)
        n = int(m.group(1)) if m else 4
        out = {"beats": [{"order_index": i + 1, "title": f"B{i}", "description": "x"} for i in range(n)]}
        if "OUTPUT SCHEMA" in user and "characters" in user:
            out.update({"title": "T", "logline": "L", "locations": [], "items": [], "characters": [
                {"name": "Mia", "role": "protagonist", "look": {"Age": "17", "Gender": "female",
                                                               "Hair": "chestnut ponytail", "Headwear": "none",
                                                               "Outerwear": "yellow rain jacket"}},
                {"name": "Old Tom", "appearance": "a bearded fisherman"}]})
        return out

    monkeypatch.setattr(story_router, "generate_structured", fake)
    pid = client.post("/api/projects", json={"title": "T", "idea": "x", "target_duration": "1 minute"}).json()["id"]
    assert client.post(f"/api/projects/{pid}/generate-story").status_code == 200
    chars = {c["name"]: c for c in client.get(f"/api/projects/{pid}/assets").json()["characters"]}
    assert chars["Mia"]["appearance"] == ("CHARACTER INFORMATION\nName: Mia\nAge: 17\nGender: female\n"
                                          "Hair: chestnut ponytail\n\nOUTFIT\nHeadwear: none\nOuterwear: yellow rain jacket")
    assert chars["Old Tom"]["appearance"] == "a bearded fisherman"  # prose answers still work


def test_black_and_white_pictures_get_explicit_colors_and_a_color_render(client, monkeypatch):
    import calliope.agent.asset_create as ac
    from calliope.config import settings

    seen = []

    async def fake_llm(messages, temperature=0.4, role=None):
        seen.append(messages[1]["content"][0]["text"])
        return {"look": {"Hair": "black, spiky", "Color palette": "red, black"}, "tags": []}

    monkeypatch.setattr(ac, "generate_structured", fake_llm)
    wf = {"10": {"class_type": "PrimitiveStringMultiline", "inputs": {"value": "x"}, "_meta": {"title": "Prompt (Input:prompt)"}},
          "50": {"class_type": "LoadImage", "inputs": {"image": ""}, "_meta": {"title": "Ref (Input:image)"}},
          "40": {"class_type": "SaveImage", "inputs": {"images": ["10", 0]}, "_meta": {"title": "Out (Output:image)"}}}
    client.post("/api/workflows", json={"name": "Klein 1 ref", "kind": "image", "workflow_json": wf})
    pid = client.post("/api/projects", json={"title": "BW"}).json()["id"]
    up = Path(settings.assets_dir) / "uploads"
    up.mkdir(parents=True, exist_ok=True)
    ink = up / "manga.png"
    Image.new("L", (60, 90), 230).convert("RGB").save(ink)
    r = client.post(f"/api/projects/{pid}/assets/new", json={"kind": "character", "name": "Kai", "image_paths": [str(ink)]}).json()
    assert "black-and-white" in seen[0] and "explicit colors" in seen[0]
    job = client.get(f"/api/jobs/{r['job_id']}").json()
    assert "render in full color" in (job.get("payload") or json.loads(job["payload_json"]))["prompt"]

    color = up / "photo.png"
    Image.new("RGB", (60, 90), (200, 60, 40)).save(color)
    seen.clear()
    r2 = client.post(f"/api/projects/{pid}/assets/new", json={"kind": "character", "name": "Ava", "image_paths": [str(color)]}).json()
    assert "black-and-white" not in seen[0]
    job2 = client.get(f"/api/jobs/{r2['job_id']}").json()
    assert "full color" not in (job2.get("payload") or json.loads(job2["payload_json"]))["prompt"]



def test_color_picture_leads_and_style_is_kept(client, monkeypatch):
    import calliope.agent.asset_create as ac
    from calliope.config import settings

    seen = []

    async def fake_llm(messages, temperature=0.4, role=None):
        seen.append([p.get("text") for p in messages[1]["content"] if p["type"] == "text"])
        return {"look": {"Hair": "blue, shoulder-length"}, "tags": []}

    monkeypatch.setattr(ac, "generate_structured", fake_llm)
    up = Path(settings.assets_dir) / "uploads"
    up.mkdir(parents=True, exist_ok=True)
    pages = []
    for i, size in enumerate([(400, 1200), (600, 900)]):
        p = up / f"page{i}.png"
        Image.new("L", size, 220).convert("RGB").save(p)
        pages.append(str(p))
    face = up / "face.png"
    Image.new("RGB", (430, 340), (40, 60, 200)).save(face)
    images = [*pages, str(face)]
    assert ac.pick_refs(images) == [str(face), pages[0]]  # color first, then the tallest (full body)

    asyncio.run(ac.describe("character", "Kaoru", "", images, identity_only=True))
    texts = seen[0]
    assert "Picture 3 (COLOR):" in texts and "Picture 1 (black-and-white):" in texts
    assert "Take every color" in texts[0]

    entity = {"id": 1, "name": "Kaoru", "appearance": "CHARACTER INFORMATION\nHair: blue", "source_images": images}
    client.post("/api/workflows", json={"name": "Klein 2", "kind": "image", "workflow_json": {
        "10": {"class_type": "PrimitiveStringMultiline", "inputs": {"value": "x"}, "_meta": {"title": "Prompt (Input:prompt)"}},
        "50": {"class_type": "LoadImage", "inputs": {"image": ""}, "_meta": {"title": "A (Input:image)"}},
        "60": {"class_type": "LoadImage", "inputs": {"image": ""}, "_meta": {"title": "B (Input:image)"}},
        "40": {"class_type": "SaveImage", "inputs": {"images": ["10", 0]}, "_meta": {"title": "Out (Output:image)"}}}})
    pid = client.post("/api/projects", json={"title": "K"}).json()["id"]
    job = ac.enqueue_from_images(pid, "character", entity)
    prompt = job["payload"]["prompt"] if "payload" in job else json.loads(job["payload_json"])["prompt"]
    assert "same art style as the reference" in prompt and "colors from the color reference" in prompt


def test_pictures_overrule_an_earlier_draft():
    from calliope.agent.asset_create import context_block

    assert "stay true to it" in context_block("Story. Earlier draft: brown hair", False)
    with_pics = context_block("Story. Earlier draft: brown hair", True)
    assert "may be wrong" in with_pics and "pictures show replaces it" in with_pics and "stay true" not in with_pics
