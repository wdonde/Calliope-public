---
name: wan22-video-prompt
description: "Use when writing or improving video prompts for Wan 2.2 workflows (5B TI2V, text-to-video or image-to-video with a start image) — clip prompts, start-image prompts, negative prompts, length and size limits on a 12 GB GPU."
version: 1.0.0
license: MIT
metadata:
  author: Calliope (wdonde fork)
  tags: [video, prompt-engineering, wan, wan2.2, comfyui, image-to-video, text-to-video]
---

# Wan 2.2 Video Prompts

## Overview

How to write prompts for the Wan 2.2 5B workflows in this install. The
MiniMax H3 skills (`h3-video-prompt-enhancer`, `h3-continuation-prompt`) do
**not** apply to Wan: Wan takes one plain-prose prompt per clip, no
multi-shot timing, no `<Subject N>` tags, no audio.

## When to Use

- Writing or rewriting a clip's video prompt for a Wan workflow
- Writing the start-image (keyframe) prompt that feeds Wan image-to-video
- A Wan clip came out washed out, smeared, with fused characters or text

Don't use for MiniMax H3 workflows (use the H3 skills) or for character
sheets (use `character-consistency`).

## The Pipeline That Works Here

Plain text-to-video gave washed-out colours, vertical streaks and fused
characters on this setup. The reliable path is two steps per clip:

1. **Start image** — FLUX.2 Klein text workflow ("FLUX.2 Klein 4B – Charakter (Text)"), 960 × 544, fixed seed. Check it before rendering video; a bad start image means a bad clip.
2. **Video** — "Wan 2.2 5B I2V – Startbild (12GB AMD)" with that image as the start frame, 960 × 544.

Limits on the RX 7700 XT (12 GB):

| Setting | Value |
| --- | --- |
| Size | 960 × 544 (832 × 480 if a clip is too slow) |
| Length | 3–4 seconds per clip; the workflow caps at 97 frames |
| Time | about 22–45 minutes per clip |

Longer moments are several clips, never one long clip.

## Prompt Structure

Write in English, one paragraph, 40–90 words. One clip = one action.

**Image-to-video (start image given)** — the image already fixes who and
where. Spend the words on what *changes*:

1. Subject anchor in a few words ("the robot", "the girl with the fox")
2. The action and its direction, with a verb of motion ("turns her head toward the window, then steps forward")
3. Secondary motion (hair, cloth, smoke, water, light flicker)
4. Camera ("slow push-in", "static camera", "slow pan left")
5. Style suffix (below)

**Text-to-video / start-image prompt** — describe everything visible:

1. Shot size ("Wide shot:", "Medium shot:", "Close-up:")
2. Subject with appearance (age, hair, clothing, colours) — every character, each clearly separate
3. Setting and time of day
4. Action
5. Lighting and colour ("warm sunset light", "cold blue night, neon reflections")
6. Style suffix

## Style Suffixes

| Project style | Append |
| --- | --- |
| Anime | "anime style, cel-shaded 2D animation, clean line art, rich but balanced colors, detailed background art" |
| 3D | "cinematic 3D CGI, soft global illumination, balanced exposure, detailed materials" |
| Realistic | "cinematic film still, natural lighting, shallow depth of field" |

Use the same suffix in every clip of a project.

## Rules

- **No dialogue in the prompt.** Quoted speech makes the image model draw speech bubbles and garbled text. Describe the speaking instead ("she speaks urgently").
- End start-image prompts with: "No speech bubbles, no text, no captions."
- Describe characters by appearance, not only by name — the model does not know the names.
- Two or more characters: give each its own position ("on the left", "in the foreground") so they do not fuse.
- One camera move at most per clip; "static camera" is a valid, stable choice.
- Avoid fast, large motion (spinning, running at the camera) at 4 seconds — it smears.
- Keep the scene's lighting words identical across its clips for continuity.

## Negative Prompt

The I2V workflow already carries a negative prompt. When asked for one:

```text
text, subtitles, captions, speech bubble, watermark, logo, blurry, low quality, washed out colors, overexposed, vertical streaks, flicker, deformed hands, extra limbs, fused characters, duplicate person, static frame, jpeg artifacts
```

## Examples

Image-to-video, anime:

```text
The girl with the fox spirit looks up at the glowing dragon, her hair and skirt lifting in the evening wind; the fox on her shoulder flicks its tail. The dragon's crystal wings slowly unfold. Slow push-in toward the girl. Anime style, cel-shaded 2D animation, clean line art, rich but balanced colors, detailed background art.
```

Start image, 3D:

```text
Medium shot: a small silver robot with large round glowing eyes kneels on a wooden workshop floor on the left, holding a flower made of copper pipes; a fluffy orange shiba dog sits on the right watching it. Warm afternoon sunlight through tall windows. Cinematic 3D CGI, soft global illumination, balanced exposure, detailed materials. No speech bubbles, no text, no captions.
```
