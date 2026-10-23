"""Every prompt Calliope sends to a model, in one catalog.

Each entry says what the prompt is, where in Calliope it is used, when it is
sent, which model role gets it and where it lives in the code; ``link`` opens
that place in the web app ({project} is filled with a project id there).

Fixed texts (system prompts, instruction blocks, image templates) are
editable: :func:`text` returns the user's version from ``prompt_overrides``
when there is one, else the default constant from the code. Declared
placeholders like {kind} are filled in by the caller; any other braces stay
as they are, so JSON examples in a prompt are safe. Prompts that code
assembles from story data are listed read-only (``editable`` False) with what
they are built from.
"""
from __future__ import annotations

import importlib
from dataclasses import asdict, dataclass, field
from typing import Any

from calliope.config import settings
from calliope.db import get_db

AREAS = ("Story", "Script", "Panels", "Assets", "Video", "Agent", "Workflows", "Common")


@dataclass(frozen=True)
class PromptSpec:
    key: str
    name: str
    area: str
    description: str
    where: str
    when: str
    role: str  # model role (Settings > Agent model) or "image model"
    source: str  # "module:ATTR" of the default text, or "module:function" for assembled prompts
    link: str = ""
    placeholders: tuple[str, ...] = ()
    editable: bool = True
    built_from: str = ""  # assembled prompts: what the code puts together
    kind: str = "system"  # system | instruction | image | note
    tags: tuple[str, ...] = field(default=())


S = PromptSpec
CATALOG: list[PromptSpec] = [
    # ── Story ──────────────────────────────────────────────────────────────
    S("story.system", "Storyline system prompt", "Story",
      "Sets the model up as story developer: one JSON object, exactly the required number of beats. "
      "For comics, 'an AI video studio' is swapped for the comic studio of the format.",
      "Project > Story > Draft storyline", "Each time the storyline is drafted, once per chunk of beats.",
      "writer", "calliope.agent.prompts:STORY_GENERATION_SYSTEM", "/project/{project}?stage=story"),
    S("story.user", "Storyline request", "Story",
      "The request with the idea, genre, tone, length, required beat count, language rule and the cast to create.",
      "Project > Story > Draft storyline", "With every storyline draft.", "writer",
      "calliope.agent.prompts:story_generation_user_prompt", "/project/{project}?stage=story", editable=False,
      built_from="Project idea (or novel outline), genre, tone, target length -> beat count, story language, "
                 "comic or video wording.", kind="instruction"),
    S("common.json_only", "JSON-only answer", "Common",
      "Short system prompt for tasks that must answer with one JSON object.",
      "Project > Story > Condense novel; Settings > Workflows > Check format",
      "When a novel is condensed and when a workflow's format is checked.", "writer / main",
      "calliope.agent.condense:JSON_ONLY", "/project/{project}?stage=story"),
    S("story.condense", "Novel condense steps", "Story",
      "The prompts that summarize a long novel part by part; they come from the novel-condense skill.",
      "Project > Story > Condense novel", "When a long text is pasted as the idea.", "writer",
      "calliope.agent.condense:load_templates", "/settings?tab=skills", editable=False,
      built_from="Templates of the skill 'novel-condense' (Settings > Skills) filled with each part of the text.",
      kind="note"),
    # ── Script ─────────────────────────────────────────────────────────────
    S("script.system", "Script system prompt", "Script",
      "Screenwriter role for video projects: scenes as screenplay units, scaled to the runtime, exact scene count.",
      "Project > Script > Generate script", "Each time the script is generated, once per chunk of scenes.",
      "writer", "calliope.agent.prompts:SCRIPT_GENERATION_SYSTEM", "/project/{project}?stage=script"),
    S("script.comic_system", "Comic script system prompt", "Script",
      "Comic writer role for webtoon and manga projects: scenes describe what can be drawn, no camera motion.",
      "Project > Script > Generate script (comic projects)", "Each time a comic script is generated.", "writer",
      "calliope.agent.comic_prompts:COMIC_SCRIPT_SYSTEM", "/project/{project}?stage=script", placeholders=("kind",)),
    S("script.user", "Script request", "Script",
      "The request with beats, characters, locations, runtime budget, scene count and the dialogue language.",
      "Project > Script > Generate script", "With every script chunk.", "writer",
      "calliope.agent.prompts:build_script_chunk_messages", "/project/{project}?stage=script", editable=False,
      built_from="Story beats, cast with appearance lines, locations, runtime budget, scene count, language rule.",
      kind="instruction"),
    S("script.coverage_system", "Shot coverage system prompt", "Script",
      "Director role that breaks each scene into short clips (video) or panels (comics).",
      "Project > Script > Break into shots", "For every scene when it is broken into shots or panels.", "writer",
      "calliope.agent.coverage_agent:COVERAGE_SYSTEM", "/project/{project}?stage=script"),
    S("script.coverage_user", "Shot coverage request", "Script",
      "The scene's action and numbered dialogue lines with the clip length rules and the JSON shape.",
      "Project > Script > Break into shots", "For every scene.", "writer",
      "calliope.agent.coverage_agent:_coverage_messages", "/project/{project}?stage=script", editable=False,
      built_from="Scene heading, action, numbered dialogue lines, scene seconds, clip cap, example JSON.",
      kind="instruction"),
    # ── Panels ─────────────────────────────────────────────────────────────
    S("panels.prompt", "Panel image prompt", "Panels",
      "What each comic panel sends to the image model.",
      "Project > Panels", "When panels are planned and rendered.", "image model",
      "calliope.agent.comic:plan_panels", "/project/{project}?stage=video", editable=False,
      built_from="Render style text (or the format look) + frame line + the clip description in English + "
                 "which reference picture shows whom + the no-text line. SDXL styles add the characters' tags.",
      kind="image"),
    S("panels.look_webtoon", "Webtoon look (no style chosen)", "Panels",
      "Leads every panel prompt of a webtoon project that has no render style.",
      "Project > Panels", "When a panel is planned or rendered.", "image model",
      "calliope.agent.comic:STYLES[webtoon]", "/project/{project}?stage=video", kind="image"),
    S("panels.look_manga", "Manga look (no style chosen)", "Panels",
      "Leads every panel prompt of a manga project that has no render style.",
      "Project > Panels", "When a panel is planned or rendered.", "image model",
      "calliope.agent.comic:STYLES[manga]", "/project/{project}?stage=video", kind="image"),
    S("panels.frame", "Panel frame line", "Panels",
      "Follows a render style's text: one picture, one moment.",
      "Project > Panels", "When a panel is planned or rendered with a render style.", "image model",
      "calliope.agent.comic:FRAME", "/project/{project}?stage=video", kind="image"),
    S("panels.no_text", "No-text line", "Panels",
      "Ends every panel prompt so the model paints no lettering; bubbles are drawn by Calliope.",
      "Project > Panels", "When panels are planned.", "image model",
      "calliope.agent.comic:NO_TEXT", "/project/{project}?stage=video", kind="image"),
    S("panels.translate", "Translate for the image prompt", "Panels",
      "Turns a non-English clip description into English for the image model; the bubbles keep the story language.",
      "Project > Panels > Plan panels", "For every panel whose description is not English.", "writer",
      "calliope.agent.comic:TRANSLATE", "/project/{project}?stage=video", kind="instruction"),
    S("panels.styles", "Render styles", "Panels",
      "The style texts and negative prompts of the render styles.",
      "Settings > Styles", "Whenever a project with that style renders.", "image model",
      "calliope.render_styles:PRESETS", "/settings?tab=styles", editable=False,
      built_from="Edited in Settings > Styles, one per style.", kind="note"),
    # ── Assets ─────────────────────────────────────────────────────────────
    S("assets.designer_system", "Asset designer system prompt", "Assets",
      "Character and production designer role that fills a character sheet or a location/item description.",
      "Project > Assets > New asset / Generate; Asset Library > Edit > Generate",
      "When an asset's look is written from notes and pictures.", "assets (with pictures) / writer",
      "calliope.agent.asset_create:DESIGNER_SYSTEM", "/library"),
    S("assets.describe", "Asset description request", "Assets",
      "The notes, the story context, the pictures and the character sheet fields to fill.",
      "Project > Assets > New asset; Asset Library > Edit > Generate", "When an asset's look is written.",
      "assets / writer", "calliope.agent.asset_create:describe", "/library", editable=False,
      built_from="Name, story context, user notes, attached pictures (color or B/W), sheet fields, tag keys.",
      kind="instruction"),
    S("assets.extract_system", "Fact extraction system prompt", "Assets",
      "Reads facts out of a text into JSON without inventing anything.",
      "Project > Assets > Use identity", "When a library identity is applied to a character with a prose look.",
      "writer", "calliope.agent.asset_create:EXTRACT_SYSTEM", "/project/{project}?stage=assets"),
    S("assets.outfit_rules", "Outfit extraction rules", "Assets",
      "How the outfit is copied out of a prose description: whole phrases, every adjective, nothing invented.",
      "Project > Assets > Use identity", "When a library identity is applied to a character with a prose look.",
      "writer", "calliope.agent.asset_create:OUTFIT_RULES", "/project/{project}?stage=assets", kind="instruction"),
    S("assets.pictures_system", "Picture reader system prompt", "Assets",
      "Vision role that describes reference pictures briefly and exactly.",
      "Asset Library > Edit > Write from pictures", "When the image prompt is written from the pictures.",
      "assets", "calliope.agent.asset_create:PICTURES_SYSTEM", "/library"),
    S("assets.pictures_rules", "Picture reader rules", "Assets",
      "What the vision model tells about each picture: the view, then the art style.",
      "Asset Library > Edit > Write from pictures", "When the image prompt is written from the pictures.",
      "assets", "calliope.agent.asset_create:PICTURES_RULES", "/library",
      placeholders=("count", "what", "name"), kind="instruction"),
    S("assets.pictures_clothes", "Picture reader: outfit", "Assets",
      "Asks for the main outfit of a character and what to leave out.",
      "Asset Library > Edit > Write from pictures", "For characters, with the picture reader rules.", "assets",
      "calliope.agent.asset_create:PICTURES_CLOTHES", "/library", kind="instruction"),
    S("assets.sheet_layout", "Character sheet layout", "Assets",
      "The layout line of character sheet prompts written from pictures.",
      "Asset Library > Edit > Write from pictures", "In every character prompt written from pictures.",
      "image model", "calliope.agent.asset_create:SHEET_LAYOUT", "/library", kind="image"),
    S("assets.sheet_prompt", "Character sheet image prompt", "Assets",
      "The image prompt of a character sheet made from the character information.",
      "Project > Assets; Asset Library > Edit > Image prompt", "When a sheet is rendered without an own prompt.",
      "image model", "calliope.agent.prompts:character_sheet_prompt", "/library", editable=False,
      built_from="Name + the sheet's fields (without alias/role) + the four-view layout; with pictures the "
                 "short reference-mode prompt (image 1 / image 2) is used instead.", kind="image"),
    S("assets.tags_system", "Asset tagging system prompt", "Assets",
      "Labels the project's assets with key:value tags so they can be found.",
      "Project > Assets > Suggest tags", "When tags are suggested for a project's assets.", "writer",
      "calliope.agent.asset_tags:TAGS_SYSTEM", "/project/{project}?stage=assets"),
    S("assets.colorize", "Colorize prompt", "Assets",
      "Turns a black-and-white picture into color without changing a line; character colors are added below it.",
      "Colorize dialog (Panels, Playground)", "When a picture is colorized.", "image model",
      "calliope.agent.colorize:COLORIZE_BASE", "/playground", kind="image"),
    # ── Video ──────────────────────────────────────────────────────────────
    S("video.skill_rewrite", "Prompt rewrite by skill", "Video",
      "System prompt for rewriting a shot into a video prompt; the chosen skill's text follows it.",
      "Video workflows with prompt profile 'Skill'", "Before a clip is queued with such a workflow.", "video",
      "calliope.agent.skill_prompt:SKILL_REWRITE_SYSTEM", "/project/{project}?stage=video"),
    S("video.continuity_plan", "Continuity plan", "Video",
      "Writes one continuity ledger for the whole film: style, subjects, camera and light per shot.",
      "Project > Video (MiniMax H3 workflows)", "Before H3 clips are written.", "video",
      "calliope.agent.continuity:_PLAN_SYSTEM", "/project/{project}?stage=video"),
    S("video.continuity_critic", "Continuity check", "Video",
      "Checks one clip prompt against the continuity plan and lists only failures.",
      "Project > Video (MiniMax H3 workflows)", "After each H3 clip prompt is written.", "video",
      "calliope.agent.continuity:_CRITIC_SYSTEM", "/project/{project}?stage=video"),
    S("video.scene_prompt", "Scene video prompt", "Video",
      "The default prose prompt for a clip.", "Project > Video", "When a clip is queued with a prose workflow.",
      "video", "calliope.agent.prompts:scene_video_prompt", "/project/{project}?stage=video", editable=False,
      built_from="Clip description, characters with their consistency prompts, location, style suffix from "
                 "tone and genre.", kind="instruction"),
    # ── Agent ──────────────────────────────────────────────────────────────
    S("agent.persona", "Agent persona", "Agent",
      "First line of every agent system prompt.", "AI Canvas, Build Scene", "Every agent step.", "main",
      "calliope.agent.harness.prompts:AGENT_PERSONA", "/canvas"),
    S("agent.mode_linked", "Session mode: linked project", "Agent",
      "Rules for an agent session that works inside one project.", "AI Canvas (linked to a project)",
      "Every agent step of a linked session.", "main", "calliope.agent.harness.prompts:MODE_LINKED", "/canvas",
      placeholders=("project_id",)),
    S("agent.mode_sandbox", "Session mode: sandbox", "Agent",
      "Rules for an agent session without a project: generate into the sandbox canvas, create or link projects.",
      "AI Canvas (no project)", "Every agent step of a sandbox session.", "main",
      "calliope.agent.harness.prompts:MODE_SANDBOX", "/canvas"),
    S("agent.mode_scene", "Session mode: Build Scene", "Agent",
      "Rules for the Build Scene chat: only the 3D composition tools.", "Build Scene",
      "Every agent step in Build Scene.", "main", "calliope.agent.harness.prompts:MODE_SCENE", "/build-scene"),
    S("agent.planner", "Swarm planner", "Agent",
      "Decides whether a request is a single reply or a task list for story/script/assets/video sub-agents.",
      "AI Canvas", "At the start of every agent request.", "planner",
      "calliope.agent.harness.orchestrator:PLANNER_SYSTEM", "/canvas"),
    S("agent.synthesis", "Swarm summary", "Agent",
      "Summarizes the sub-agents' work for the user, checked against the real content counts.",
      "AI Canvas", "After the sub-agents of a swarm task finished.", "planner",
      "calliope.agent.harness.orchestrator:SYNTHESIS_SYSTEM", "/canvas"),
    S("agent.final_step", "Final step nudge", "Agent",
      "Tells the agent its step budget is used up: summarize or say what blocks it.",
      "AI Canvas, Build Scene", "On the last allowed step of a turn (Settings > Agent model > Max steps).",
      "main", "calliope.agent.harness.loop:FINAL_STEP_NUDGE", "/settings?tab=agent", kind="instruction"),
    S("agent.no_function_calling", "No tool calling note", "Agent",
      "Shown to the user when the model answered with raw JSON instead of a tool call.",
      "AI Canvas", "When the chosen model cannot call tools.", "main",
      "calliope.agent.harness.loop:NO_FUNCTION_CALLING_NOTE", "/settings?tab=agent", kind="note"),
    # ── Workflows ──────────────────────────────────────────────────────────
    S("workflows.skill_draft_system", "Workflow skill draft", "Workflows",
      "System prompt for drafting prompt-writing rules (a skill) for a new video workflow.",
      "Settings > Workflows > Draft skill", "When a skill is drafted for a video workflow.", "writer",
      "calliope.agent.workflow_format:SKILL_DRAFT_SYSTEM", "/settings?tab=workflows"),
]
BY_KEY = {s.key: s for s in CATALOG}


def default(key: str) -> str:
    """The text the code ships with (for assembled prompts: the builder's docstring)."""
    spec = BY_KEY[key]
    module, _, attr = spec.source.partition(":")
    item = None
    if attr.endswith("]"):
        attr, _, item = attr[:-1].partition("[")
    value: Any = getattr(importlib.import_module(module), attr)
    if item is not None:
        value = value[item]
    if callable(value):
        return (value.__doc__ or "").strip()
    if not isinstance(value, str):
        return ""
    return value


def _override(key: str) -> dict[str, Any] | None:
    """The user's version; None also when there is no database (yet) -- a prompt
    must never fail because of it."""
    try:
        conn = get_db(settings.db_path)
    except Exception:  # noqa: BLE001
        return None
    try:
        row = conn.execute("SELECT text, image_path, updated_at FROM prompt_overrides WHERE key = ?", (key,)).fetchone()
    except Exception:  # noqa: BLE001 -- table not there yet (before migration)
        return None
    finally:
        conn.close()
    return dict(row) if row else None


def text(key: str, **values: Any) -> str:
    """The prompt as it is sent: the user's version or the default, placeholders filled."""
    spec = BY_KEY[key]
    own = _override(key) if spec.editable else None
    out = (own or {}).get("text") or default(key)
    for name, value in values.items():
        out = out.replace("{" + name + "}", str(value))
    return out


def entries() -> list[dict[str, Any]]:
    """The catalog for the Prompts page: metadata, default and current text, picture."""
    conn = get_db(settings.db_path)
    try:
        own = {r["key"]: dict(r) for r in conn.execute("SELECT * FROM prompt_overrides")}
    finally:
        conn.close()
    out = []
    for spec in CATALOG:
        o = own.get(spec.key) or {}
        d = default(spec.key)
        out.append({**asdict(spec), "default": d, "text": o.get("text") or d,
                    "overridden": bool(o.get("text")), "image_path": o.get("image_path"),
                    "updated_at": o.get("updated_at")})
    return out


def save(key: str, *, text_value: str | None = None, image_path: str | None = None,
         reset_text: bool = False) -> None:
    conn = get_db(settings.db_path)
    try:
        conn.execute("INSERT OR IGNORE INTO prompt_overrides (key) VALUES (?)", (key,))
        if reset_text:
            conn.execute("UPDATE prompt_overrides SET text = NULL, updated_at = CURRENT_TIMESTAMP WHERE key = ?", (key,))
        elif text_value is not None:
            same = text_value.strip() == default(key).strip()
            conn.execute("UPDATE prompt_overrides SET text = ?, updated_at = CURRENT_TIMESTAMP WHERE key = ?",
                         (None if same else text_value, key))
        if image_path is not None:
            conn.execute("UPDATE prompt_overrides SET image_path = ? WHERE key = ?", (image_path or None, key))
        conn.commit()
    finally:
        conn.close()


def missing_placeholders(key: str, value: str) -> list[str]:
    """Declared placeholders an edited text dropped (the code would have nothing to fill)."""
    return [p for p in BY_KEY[key].placeholders if "{" + p + "}" not in value]
