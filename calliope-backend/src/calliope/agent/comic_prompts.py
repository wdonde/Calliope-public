"""Prompt wording for comic projects (webtoon episodes, manga chapters).

Video projects measure length in seconds and think in moving shots; comics
measure it in panels (webtoon) or pages (manga) and think in still frames
carried by dialogue. The story/script/coverage builders take ``fmt`` and use
these pieces instead of their video wording when it is a comic format.
"""
from __future__ import annotations

import re
from typing import Any


def story_language_rule(text: str | None) -> str:
    # Late import: prompts imports this module.
    from calliope.agent.prompts import story_language_rule as rule

    return rule(text)

COMIC_FORMATS = ("webtoon", "manga")
PANELS_PER_PAGE = 5
PANELS_PER_BEAT = 3.5
PANELS_PER_SCENE = 2.5

# Length choices offered in the Story settings (stored in target_duration).
LENGTHS = {
    "webtoon": ("10 panels", "20 panels", "40 panels", "60 panels"),
    "manga": ("8 pages", "16 pages", "24 pages", "40 pages"),
}
DEFAULT_LENGTH = {"webtoon": "20 panels", "manga": "16 pages"}


def is_comic(fmt: str | None) -> bool:
    return fmt in COMIC_FORMATS


def panel_count(target: str | None, fmt: str | None) -> int:
    """Panels for a comic length like "20 panels" or "16 pages"."""
    text = (target or "").lower()
    m = re.search(r"(\d+)\s*(panel|page)", text)
    if not m:
        text = DEFAULT_LENGTH.get(fmt or "", "20 panels")
        m = re.search(r"(\d+)\s*(panel|page)", text)
    n = int(m.group(1))
    return n * PANELS_PER_PAGE if m.group(2) == "page" else n


def beat_count(target: str | None, fmt: str | None) -> int:
    return max(4, min(60, round(panel_count(target, fmt) / PANELS_PER_BEAT)))


def scene_count(target: str | None, fmt: str | None) -> int:
    return max(3, min(90, round(panel_count(target, fmt) / PANELS_PER_SCENE)))


def medium(fmt: str | None, target: str | None) -> dict[str, str]:
    """Wording that replaces the video phrasing in the story prompts."""
    panels = panel_count(target, fmt)
    if fmt == "manga":
        pages = max(1, round(panels / PANELS_PER_PAGE))
        return {
            "studio": "a manga studio",
            "brief": "a manga chapter (black-and-white comic pages)",
            "length": f"chapter_length: {pages} pages (~{panels} panels)",
            "scale": (f"For a {pages}-page chapter, each beat is a concrete incident worth about "
                      f"{PANELS_PER_BEAT:g} panels, so Script can later stage it as panels"),
            "weight": f"each worth about {PANELS_PER_BEAT:g} panels",
            "craft": ("7. Manga pacing: build tension toward page turns, give the strongest reveal to the "
                      "last page, and keep each beat drawable as still images with dialogue."),
        }
    return {
        "studio": "a webtoon studio",
        "brief": "a webtoon episode (vertical-scroll color comic)",
        "length": f"episode_length: {panels} panels",
        "scale": (f"For a {panels}-panel episode, each beat is a concrete incident worth about "
                  f"{PANELS_PER_BEAT:g} panels, so Script can later stage it as panels"),
        "weight": f"each worth about {PANELS_PER_BEAT:g} panels",
        "craft": ("7. Webtoon pacing: open with a striking panel that makes readers scroll, keep momentum "
                  "panel to panel, and end the episode on a cliffhanger."),
    }


COMIC_SCRIPT_SYSTEM = (
    "You are a comic writer adapting a story into a script for a {kind}. "
    "A scene is a script unit — heading, the visible action of its moment, and every line of "
    "dialogue spoken in it. A later pass breaks each scene into panels (still images), so the "
    "action describes what can be DRAWN: poses, expressions, composition — no camera motion. "
    "Always respond with a single valid JSON object. "
    "HARD RULE: the scenes array length must equal required_scene_count from the user message."
)


def comic_script_chunk_messages(
    *,
    fmt: str,
    title: str,
    idea: str | None,
    beats: list[dict[str, Any]],
    characters: list[dict[str, Any]],
    locations: list[dict[str, Any]],
    target_duration: str | None,
    scene_count: int,
    chunk_start: int,
    chunk_scenes: int,
    previous_tail: list[dict[str, Any]] | None = None,
) -> list[dict[str, str]]:
    """Script chunk for a comic: scenes measured in panels, dialogue-driven."""
    panels = panel_count(target_duration, fmt)
    per_scene = max(1, round(panels / max(1, scene_count)))
    kind = "manga chapter" if fmt == "manga" else "webtoon episode"
    last = chunk_start + chunk_scenes - 1
    char_lines = "\n".join(
        f"- id={c['id']} {c['name']} ({c.get('role') or ''}): {c.get('appearance') or ''}" for c in characters
    )
    loc_lines = "\n".join(f"- id={l['id']} {l['name']}: {l.get('description') or ''}" for l in locations)
    beat_lines = "\n".join(f"- {b.get('order_index')}. {b.get('title')}: {b.get('description')}" for b in beats)
    tail = ""
    if previous_tail:
        tail = "\n\nScenes already written (CONTINUE from here):\n" + "\n".join(
            f"  {s.get('order_index')}. {s.get('heading')} — {(s.get('action') or '')[:180]}" for s in previous_tail
        )
    user = f"""Write scenes {chunk_start} to {last} of a {scene_count}-scene script for this {kind}.

Title: {title}
Idea: {idea or ''}
Length: {target_duration or DEFAULT_LENGTH[fmt]} (~{panels} panels in total)
required_scene_count: {scene_count}
Per-scene budget: ~{per_scene} panels each — write action and dialogue for that many still images.
THIS CHUNK: exactly {chunk_scenes} scenes, order_index {chunk_start} through {last}.

Story beats:
{beat_lines or '(none)'}

Characters:
{char_lines or '(none)'}

Locations:
{loc_lines or '(none)'}{tail}

=== HARD CONSTRAINTS (non-negotiable) ===
1. The JSON field "scenes" MUST contain EXACTLY {chunk_scenes} objects.
2. order_index must run {chunk_start}, {chunk_start + 1}, ... {last} with no gaps.
3. Advance the beat arc; this chunk covers the part that falls at scenes {chunk_start}-{last}.
4. Continue earlier scenes naturally — same characters, consistent locations.
{story_language_rule(idea)}
=== ACTION (what the panels will show) ===
One flowing paragraph, present tense: who is where, poses, gestures, facial expressions,
the key prop, the setting and its light. Describe moments that read as STILL images — no
camera moves, no sounds. The first time a character appears in a scene, attach a short
visual anchor from their description (hair, outfit, one distinguishing feature).

=== DIALOGUE (carries the story in a comic) ===
Every line the scene needs, 'SPEAKER: line', one per line, in reading order. Comics lean on
dialogue: keep the important lines, never summarize them. Short lines fit speech bubbles.

Respond ONLY with JSON:
{{
  "scenes": [
    {{
      "order_index": {chunk_start},
      "heading": "INT. LOCATION - TIME",
      "action": "MIA, a teenage girl with a chestnut ponytail and yellow rain jacket, stands in the dusty hall, lantern raised, eyes wide.",
      "dialog": "MIA: line",
      "duration_sec": {per_scene},
      "character_ids": [1],
      "location_id": 1
    }}
  ]
}}"""
    return [
        {"role": "system", "content": COMIC_SCRIPT_SYSTEM.format(kind=kind)},
        {"role": "user", "content": user},
    ]


def comic_coverage_messages(
    *,
    fmt: str,
    scene: dict[str, Any],
    characters: list[dict[str, Any]],
    numbered_dialog: str,
    panels_hint: int,
    previous_heading: str | None,
    next_heading: str | None,
) -> list[dict[str, str]]:
    """Break one scene into comic panels (the clips of a comic project)."""
    kind = "manga" if fmt == "manga" else "webtoon"
    char_lines = "\n".join(
        f"- id={c['id']} {c['name']}: {(c.get('appearance') or '').strip()}" for c in characters
    )
    context = ""
    if previous_heading or next_heading:
        context = f"\nNeighbor scenes: before {previous_heading or '(none)'} / after {next_heading or '(none)'}\n"
    user = f"""Break this scene into {kind} PANELS (still images).

Scene {scene.get('order_index')}: {scene.get('heading') or '(no heading)'}
Suggested panel count: ~{panels_hint} (adjust to the material)
{context}
Characters in this scene:
{char_lines or '(none)'}

Action:
{scene.get('action') or '(none)'}

Dialogue lines (verbatim, numbered):
{numbered_dialog}

=== HARD CONSTRAINTS (non-negotiable) ===
1. Every numbered dialogue line goes to EXACTLY ONE panel (dialog_lines_covered); a panel
   holds at most 2-3 short lines so its speech bubbles fit. Together the panels show the
   whole action, in reading order.
2. Each description is ONE still image in English: framing, who is in it (with a short
   appearance anchor the first time), pose, facial expression, setting, light. No motion,
   no camera moves, no text or speech bubbles in the description.
3. shot_size is one of: wide, medium, closeUp, insert, overShoulder — vary it like a comic
   (establishing wide, reaction close-ups, detail inserts).
4. duration_sec is always 1 (comics have no duration).

Respond ONLY with JSON:
{{
  "clips": [
    {{"order_index": 1, "description": "Wide shot: ...", "dialog_lines_covered": [1],
      "shot_size": "wide", "duration_sec": 1}}
  ]
}}"""
    return [
        {"role": "system", "content": f"You are a {kind} artist planning panels. You output ONLY a single valid JSON object."},
        {"role": "user", "content": user},
    ]
