"""Danbooru tags from pictures with a WD tagger (SmilingWolf, ONNX, on the CPU).

SDXL anime models (Illustrious, NoobAI, ...) are trained on Danbooru tags,
so tags read from a character's pictures ("dark blue hair, wavy hair,
collared shirt, grey pants") are words those models know. The model is
downloaded on first use into data/models/<repo name>/.

Needs the optional ``tagger`` extra (onnxruntime, numpy).
"""
from __future__ import annotations

import csv
import threading
from collections import Counter
from pathlib import Path
from typing import Any

import httpx
from PIL import Image

from calliope.config import settings

REPO = "SmilingWolf/wd-vit-tagger-v3"
FILES = ("model.onnx", "selected_tags.csv")
GENERAL_THRESHOLD = 0.35
CHARACTER_THRESHOLD = 0.85
# Tags that say nothing about the character's look (framing, background, the picture itself).
NOISE = {
    "solo", "simple background", "white background", "grey background", "gray background", "looking at viewer",
    "standing", "full body", "upper body", "cowboy shot", "portrait", "multiple views", "reference sheet",
    "character sheet", "monochrome", "greyscale", "comic", "speech bubble", "english text", "text focus",
    "censored", "mosaic censoring", "bar censor", "border", "white border", "letterboxed", "artist name",
    "signature", "watermark", "twitter username", "web address", "closed mouth", "open mouth", "blush",
    "smile", "sweat", "tears", "crying", "teeth", "tongue", "tongue out", "parted lips", "arms at sides",
}
# Explicit tags never go into a prompt (they come from adult source pages, not from the look).
EXPLICIT = ("nude", "naked", "nipple", "pussy", "penis", "sex", "cum", "ass", "areola", "cleavage", "underwear",
            "panties", "bra", "lingerie", "towel", "bathing", "topless", "bottomless", "spread", "groin", "pubic",
            "cameltoe", "erect", "orgasm", "saliva", "ahegao", "bondage", "condom")
_KAOMOJI = {"0_0", "(o)_(o)", "+_+", "+_-", "._.", "<o>_<o>", "<|>_<|>", "=_=", ">_<", "3_3", "6_9", ">_o",
            "@_@", "^_^", "o_o", "u_u", "x_x", "|_|", "||_||"}

_lock = threading.Lock()
_model: tuple[Any, list[tuple[str, int]]] | None = None


def model_dir() -> Path:
    return Path(settings.data_dir) / "models" / REPO.split("/")[-1]


def _download() -> Path:
    folder = model_dir()
    folder.mkdir(parents=True, exist_ok=True)
    for name in FILES:
        target = folder / name
        if target.exists():
            continue
        part = target.with_suffix(target.suffix + ".part")
        with httpx.stream("GET", f"https://huggingface.co/{REPO}/resolve/main/{name}",
                          follow_redirects=True, timeout=600) as r:
            r.raise_for_status()
            with part.open("wb") as f:
                for chunk in r.iter_bytes(1 << 20):
                    f.write(chunk)
        part.rename(target)
    return folder


def _load() -> tuple[Any, list[tuple[str, int]]]:
    global _model
    with _lock:
        if _model is None:
            import onnxruntime as ort

            folder = _download()
            session = ort.InferenceSession(str(folder / "model.onnx"), providers=["CPUExecutionProvider"])
            with (folder / "selected_tags.csv").open(encoding="utf-8") as f:
                tags = [(row["name"] if row["name"] in _KAOMOJI else row["name"].replace("_", " "),
                         int(row["category"])) for row in csv.DictReader(f)]
            _model = (session, tags)
        return _model


def _prepare(path: str, size: int):
    import numpy as np

    im = Image.open(path)
    if im.mode in ("RGBA", "LA", "P"):
        im = im.convert("RGBA")
        bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
        bg.alpha_composite(im)
        im = bg
    im = im.convert("RGB")
    side = max(im.size)
    square = Image.new("RGB", (side, side), (255, 255, 255))
    square.paste(im, ((side - im.width) // 2, (side - im.height) // 2))
    square = square.resize((size, size), Image.BICUBIC)
    return np.asarray(square, dtype=np.float32)[:, :, ::-1]  # RGB -> BGR, 0..255


def tag_image(path: str) -> dict[str, float]:
    """Tag -> confidence for one picture (general and character tags above their thresholds)."""
    session, tags = _load()
    inp = session.get_inputs()[0]
    size = inp.shape[1] if isinstance(inp.shape[1], int) else 448
    probs = session.run(None, {inp.name: _prepare(path, size)[None]})[0][0]
    out = {}
    for (name, category), p in zip(tags, probs):
        if (category == 0 and p >= GENERAL_THRESHOLD) or (category == 4 and p >= CHARACTER_THRESHOLD):
            out[name] = float(p)
    return out


def _explicit(tag: str) -> bool:
    words = set(tag.lower().replace("-", " ").split())
    return any(w in words or (w + "s") in words for w in EXPLICIT)


def tags_from_pictures(paths: list[str], limit: int = 30) -> dict[str, Any]:
    """Tags that describe the character across its pictures: seen in at least
    half of them (or very sure in one), framing/expression noise left out,
    most frequent and confident first."""
    per_image = [tag_image(p) for p in paths]
    seen: Counter[str] = Counter()
    best: dict[str, float] = {}
    for tags in per_image:
        for name, p in tags.items():
            seen[name] += 1
            best[name] = max(best.get(name, 0.0), p)
    need = max(1, (len(per_image) + 1) // 2)
    keep = [t for t in seen if t not in NOISE and not _explicit(t) and (seen[t] >= need or best[t] >= 0.85)]
    keep.sort(key=lambda t: (-seen[t], -best[t]))
    return {"tags": keep[:limit],
            "per_image": [[k for k in sorted(t, key=lambda k: -t[k]) if not _explicit(k)] for t in per_image]}


# Words that make a tag part of who the character is (body, face, hair, marks);
# everything else (clothes, accessories, pose) belongs to a project's outfit.
IDENTITY_WORDS = (
    "girl", "boy", "female", "male", "woman", "man", "mature", "old", "young", "child", "hair", "bangs",
    "ponytail", "twintails", "braid", "bun", "sidelocks", "ahoge", "eyes", "eyelashes", "eyebrows", "pupils",
    "skin", "tan", "freckles", "mole", "scar", "tattoo", "birthmark", "lips", "teeth", "fang", "ears", "tail",
    "horns", "wings", "breasts", "muscular", "abs", "thick", "curvy", "plump", "slim", "tall", "short",
    "beard", "mustache", "stubble", "facial hair", "makeup", "lipstick", "nose", "forehead", "dark-skinned",
)
OUTFIT_WORDS = ("shirt", "dress", "skirt", "pants", "jacket", "coat", "uniform", "shoes", "boots", "footwear",
                "socks", "gloves", "hat", "belt", "tie", "collar", "sleeves", "hood", "bow", "ribbon", "glasses",
                "earrings", "necklace", "bag", "suit", "vest", "sweater", "hoodie", "shorts", "jeans", "heels",
                "top", "bra", "swimsuit", "bikini", "kimono", "armor", "cape", "scarf", "apron", "leotard",
                "pantyhose", "thighhighs", "underwear", "hairband", "hairclip", "hair ornament", "headband")


def is_identity_tag(tag: str) -> bool:
    """Whole words decide ('tank top' is clothing, not 'tan' skin)."""
    text = tag.lower()
    words = set(text.replace("-", " ").split())
    if any((w in text) if " " in w else (w in words) for w in OUTFIT_WORDS):
        return False
    words |= {w.rstrip("s") for w in words} | {w[1:] for w in words if w[:1].isdigit()}
    return any((w in text) if " " in w else (w in words) for w in IDENTITY_WORDS)


def split_identity(tags: list[str]) -> tuple[list[str], list[str]]:
    """(identity tags, the rest: clothes, accessories, pose)."""
    return [t for t in tags if is_identity_tag(t)], [t for t in tags if not is_identity_tag(t)]
