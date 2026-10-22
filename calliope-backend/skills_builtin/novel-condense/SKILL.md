---
name: novel-condense
description: "Prompt templates behind 'Store as novel & summarize' — how a long novel is condensed into Story idea, genre, tone and length. Edit to change the summaries; not a task recipe for the agent."
version: 1.0.0
license: MIT
metadata:
  author: Calliope
  tags: [story, novel, summary, idea]
---

# Novel Condense

Calliope reads these templates when a novel is condensed into **Story idea**
(New Project checkbox, or the summarize button under Story idea). The model
assigned to the **Story** role answers them.

How it runs:

1. The novel is cut into parts of about 6,000 characters.
2. `part-summary` is sent once per part.
3. `project-fields` gets all part summaries and returns title, genre, tone,
   length and the idea.
4. `shorten` runs only when the idea comes back too long.
5. `merge-parts` runs only for very long novels (more than 20 parts): it merges
   neighbouring part summaries so Draft Storyline gets at most 20 of them.

Rules for editing:

- Keep the `##` headings and one ```` ```text ```` block under each —
  that block is the prompt. Everything else in this file is ignored.
- Keep the JSON answer line of each prompt; Calliope parses those keys.
- Placeholders (written `$name`) are filled in by Calliope:

| Placeholder | Value |
| --- | --- |
| `$lang` | output language, by default "the same language as the source text" |
| `$part`, `$parts` | number of this part, number of parts |
| `$text` | the part, the joined summaries, or the idea to shorten |
| `$durations` | lengths the model may pick, e.g. "30 seconds, 1 minute, 2 minutes" |
| `$min_chars`, `$max_chars` | target length of the idea |

A missing or broken section falls back to Calliope's built-in prompt, so a
mistake here never stops the button from working. Delete this skill folder to
get the original templates back (it is re-seeded on the next use).

## part-summary

```text
Summarize part $part of $parts of a story in 4-6 sentences, written in $lang. Name the main characters with their appearance, the locations and the key events.
Answer: {"summary": "..."}

PART:
$text
```

## project-fields

```text
Below are summaries of every part of a story, in order. Fill in the fields for a short-film project:
- title: the story's title, in $lang
- genre: 1-3 genres in English, e.g. "Sci-Fi, Action"
- tone: mood and visual style in a few English words, e.g. "Cinematic, dark, atmospheric"
- target_duration: exactly one of $durations -- this becomes a SHORT video, even for a long novel.
- idea: a summary of the WHOLE plot in $lang, $min_chars to $max_chars characters, strictly chronological: every part gets at least one sentence. Exciting events (conflict, danger, turning points, surprises) take priority over explanations. Name the main and important side characters with a short appearance, and the locations. Render the ending exactly, including its last twist, hint or open question. No generic closing sentences.
Answer: {"title": "...", "genre": "...", "tone": "...", "target_duration": "...", "idea": "..."}

SUMMARIES:
$text
```

## merge-parts

```text
Merge these consecutive part summaries of a story into ONE summary of 5-7 sentences, written in $lang. Keep every named character, location and turning point, in order.
Answer: {"summary": "..."}

SUMMARIES:
$text
```

## shorten

```text
Rewrite the following text shorter: at most $max_chars characters, same order and same language, keep every character and turning point, drop only details.
Answer: {"idea": "<the complete shortened text>"}

TEXT:
$text
```
