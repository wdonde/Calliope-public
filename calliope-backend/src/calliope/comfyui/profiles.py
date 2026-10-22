"""Prompt profiles: how scene prompts are formatted for a workflow's model.

``prose`` (default) sends the classic flat prose paragraph. ``minimax_h3_ref``
rewrites the scene into MiniMax H3's six-section full-reference format at
video-enqueue time (see calliope.agent.prompts). The rewrite reads the clip
form's ``(Input:image)`` and ``(Input:video)`` files; story images only fill
slots the user left empty. ``skill`` writes the prompt by the rules of the
skill named in ``workflows.prompt_skill`` (agent/skill_prompt.py): a clean
base prompt instantly, an LLM rewrite on Regenerate.
"""
from __future__ import annotations

from typing import Any

PROFILES = ("prose", "minimax_h3_ref", "skill")

# Node class types (or model-file fragments) -> the builtin skill for that
# model family. Checked in order; the first hit wins.
_SKILL_BY_NODE = (("Wan22ImageToVideoLatent", "wan22-video-prompt"),)
_SKILL_BY_MODEL_FILE = (("wan2.2", "wan22-video-prompt"), ("wan2_2", "wan22-video-prompt"))

DEFAULT_PROFILE = "prose"


def detect_prompt_profile(workflow_json: dict[str, Any]) -> str:
    """Suggest a prompt profile from the workflow's node classes.

    Deterministic class-type check (not title guessing): any MiniMaxH3* node
    (e.g. ``MiniMaxH3ReferenceToVideo``) means the workflow expects the H3
    full-reference prompt format.
    """
    for node in workflow_json.values():
        if not isinstance(node, dict):
            continue
        if str(node.get("class_type", "")).startswith("MiniMaxH3"):
            return "minimax_h3_ref"
    if detect_prompt_skill(workflow_json):
        return "skill"
    return DEFAULT_PROFILE


def detect_prompt_skill(workflow_json: dict[str, Any]) -> str | None:
    """The builtin prompt skill for a known model family, else ``None``.

    Deterministic, like :func:`detect_prompt_profile`: node class types first,
    then model file names in loader inputs. Unknown families return ``None``
    and are left to the LLM check (``POST /api/workflows/{id}/check-format``).
    """
    nodes = [n for n in workflow_json.values() if isinstance(n, dict)]
    classes = {str(n.get("class_type", "")) for n in nodes}
    for cls, skill in _SKILL_BY_NODE:
        if cls in classes:
            return skill
    files = " ".join(
        str(v).lower() for n in nodes for v in (n.get("inputs") or {}).values() if isinstance(v, str)
    )
    for fragment, skill in _SKILL_BY_MODEL_FILE:
        if fragment in files:
            return skill
    return None
