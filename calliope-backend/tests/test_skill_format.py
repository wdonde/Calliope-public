"""Prompt format 'skill': model-family detection and the instant base prompt."""
from calliope.agent.skill_prompt import base_prompt, style_suffix
from calliope.comfyui.profiles import detect_prompt_profile, detect_prompt_skill


def _wf(*nodes):
    return {str(i): n for i, n in enumerate(nodes, 1)}


def test_wan22_detected_by_node_type():
    wf = _wf({"class_type": "Wan22ImageToVideoLatent", "inputs": {}})
    assert detect_prompt_skill(wf) == "wan22-video-prompt"
    assert detect_prompt_profile(wf) == "skill"


def test_wan22_detected_by_model_file():
    wf = _wf({"class_type": "UNETLoader", "inputs": {"unet_name": "wan2.2_ti2v_5B_fp16.safetensors"}})
    assert detect_prompt_skill(wf) == "wan22-video-prompt"


def test_h3_wins_and_unknown_stays_prose():
    assert detect_prompt_profile(_wf({"class_type": "MiniMaxH3ReferenceToVideo", "inputs": {}})) == "minimax_h3_ref"
    unknown = _wf({"class_type": "UNETLoader", "inputs": {"unet_name": "flux2-klein-4b.safetensors"}})
    assert detect_prompt_skill(unknown) is None
    assert detect_prompt_profile(unknown) == "prose"


def test_style_suffix_follows_tone():
    assert style_suffix("Anime, warm").startswith("anime style")
    assert style_suffix("Cinematic 3D CGI").startswith("cinematic 3D CGI")
    assert style_suffix("Dark, tense") == "cinematic film still, natural lighting, balanced exposure"


def test_base_prompt_drops_dialogue_and_heading():
    scene = {"heading": "INT. HANGAR - NIGHT", "action": "wide shot. A man fires a laser", "dialog": 'Gee: "Stop!"'}
    chars = [{"name": "Gee", "appearance": "grey temples, navy uniform"}]
    prompt = base_prompt(scene, chars, {"tone": "Anime"})
    assert "Stop" not in prompt and "INT." not in prompt
    assert "navy uniform" in prompt and "anime style" in prompt


def test_base_prompt_cleans_shot_label_quotes_and_style_lead():
    scene = {"action": "wide shot. Wide shot: the dragon speaks: 'Do you remember the last time?'. Wings glow"}
    chars = [{"name": "Kaede", "appearance": "Anime style, cel-shaded. Long dark hair, red scarf"}]
    prompt = base_prompt(scene, chars, {"tone": "Anime"})
    assert prompt.startswith("Wide shot: the dragon speaks")
    assert "remember" not in prompt and "dragon’s" not in prompt
    assert "Kaede: Long dark hair" in prompt
    assert prompt.lower().count("anime style") == 1


def test_base_prompt_keeps_apostrophes():
    prompt = base_prompt({"action": "The dragon’s wings and Tokio’s skyline glow"}, [], {"tone": "Anime"})
    assert "dragon’s wings and Tokio’s skyline" in prompt


def test_source_outline_block_and_part_ranges():
    from calliope.agent.prompts import source_outline_block, source_part_range

    block = source_outline_block(["A happens.", "B happens.", "C happens.", "D happens."])
    assert "Part 1. A happens." in block and "Part 4. D happens." in block
    assert source_part_range(block, 1, 5, 10) == "1-2"
    assert source_part_range(block, 6, 10, 10) == "3-4"
    assert source_outline_block([]) == ""


def test_outline_names_the_story_language():
    from calliope.agent.prompts import guess_language, source_outline_block

    de = ["Commander Gee steht auf der Brücke und sieht ein schwarzes Loch mit einem weißen Kern, "
          "das nicht da sein dürfte. Der erste Offizier ist ratlos und die Crew wartet auf den Befehl."] * 2
    assert guess_language(" ".join(de)) == "German"
    assert "in German" in source_outline_block(de)
    en = ["The captain lands on the planet and fights the monsters with his rifle in the dark of the night."] * 3
    assert "LANGUAGE" not in source_outline_block(en)


def test_script_passage_covers_the_matching_novel_parts(monkeypatch):
    import json as _json

    from calliope.agent import source_passages
    from calliope.config import settings

    monkeypatch.setattr(type(settings), "context_window_tokens", lambda self: 16384)
    parts = [f"Teil {i} " + ("x" * 5800) for i in range(1, 9)]
    project = {"source_text": "\n\n".join(parts), "source_summaries": _json.dumps([f"s{i}" for i in range(8)])}
    first = source_passages.passage_for_scenes(project, 1, 3, 12)
    last = source_passages.passage_for_scenes(project, 10, 12, 12)
    assert "Teil 1 " in first and "Teil 8 " not in first
    assert "Teil 8 " in last and "Teil 1 " not in last
    assert source_passages.passage_for_scenes({"source_text": ""}, 1, 3, 12) is None
