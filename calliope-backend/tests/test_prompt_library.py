"""Prompt catalog: every prompt listed, fixed texts editable and resettable."""
from __future__ import annotations


def test_catalog_lists_prompts_with_where_and_when(client):
    entries = client.get("/api/prompts").json()
    story = next(e for e in entries if e["key"] == "story.system")
    assert story["area"] == "Story" and story["where"] and story["when"] and story["link"]
    assert story["text"] == story["default"] and not story["overridden"]
    assembled = next(e for e in entries if e["key"] == "panels.prompt")
    assert not assembled["editable"] and assembled["built_from"]


def test_edited_prompt_is_sent_and_reset_brings_back_the_default(client):
    from calliope.agent.prompts import STORY_GENERATION_SYSTEM, story_system

    r = client.patch("/api/prompts/story.system", json={"text": "You plan stories for an AI video studio. JSON only."})
    assert r.json()["overridden"]
    assert story_system() == "You plan stories for an AI video studio. JSON only."
    client.patch("/api/prompts/story.system", json={"reset": True})
    assert story_system() == STORY_GENERATION_SYSTEM


def test_placeholders_must_stay_and_assembled_prompts_are_read_only(client):
    from calliope.prompt_library import text

    r = client.patch("/api/prompts/script.comic_system", json={"text": "You write comics."})
    assert r.status_code == 422 and "{kind}" in r.json()["detail"]
    client.patch("/api/prompts/script.comic_system", json={"text": "You write a {kind} script. JSON {\"a\": 1}."})
    assert text("script.comic_system", kind="manga") == 'You write a manga script. JSON {"a": 1}.'
    assert client.patch("/api/prompts/panels.prompt", json={"text": "x"}).status_code == 422
    # saving the default text again is no override
    from calliope.prompt_library import default

    client.patch("/api/prompts/agent.persona", json={"text": default("agent.persona")})
    assert not next(e for e in client.get("/api/prompts").json() if e["key"] == "agent.persona")["overridden"]
