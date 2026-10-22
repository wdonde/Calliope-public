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
