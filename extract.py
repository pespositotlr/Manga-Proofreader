"""Extract English text layers from typeset PSDs and OCR the Japanese under each one.

Usage:
  python extract.py v12 [--chapters 034,035]

Writes local/data/<vol>_ch<NNN>.tsv with: page, layer_id, font, japanese, english.
Offline only (psd-tools + manga-ocr). Raws may be smaller than the PSDs; boxes are scaled.

File naming: <prefix>ch<NNN>_<page>.psd in the cleaned folder, and the same name as .jpg in the raw
folder. Spreads (<page>-<page>) are rebuilt from two raw pages. Misnamed raws: "raw_map" in config.json.
"""

import argparse
import glob
import os
import re
import sys

from PIL import Image
from psd_tools import PSDImage
from spellchecker import SpellChecker

from mp_config import CFG, cleaned_dir, local, raw_dir
from psdtext import font_of, is_point_text, layer_text

PAD = 0.12  # padding around the English box, as a fraction of its size


def raw_image(raw_folder, name):
    """Raw page for a PSD base name (spreads are joined), or None."""
    mapped = CFG.get("raw_map", {}).get(name)
    if mapped:
        return Image.open(os.path.join(raw_folder, mapped + ".jpg")).convert("RGB")
    base = os.path.join(raw_folder, name + ".jpg")
    if os.path.exists(base):
        return Image.open(base).convert("RGB")
    m = re.match(r"(.*_)(ch\d+|extra)_(\d+)(?:-(\d+))?$", name)
    if not m:
        return None
    pre, _, a, b = m.groups()
    imgs = []
    for p in [a] + ([b] if b else []):
        hits = glob.glob(os.path.join(raw_folder, f"{pre}*_{p}.jpg"))
        if len(hits) != 1:
            return None
        imgs.append(Image.open(hits[0]).convert("RGB"))
    if len(imgs) == 1:
        return imgs[0]
    # Spread: Japanese reading order puts the lower page number on the right.
    w, h = imgs[0].size
    both = Image.new("RGB", (w * 2, h), "white")
    both.paste(imgs[1], (0, 0))
    both.paste(imgs[0], (w, 0))
    return both


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("vol", help="volume id, e.g. v12")
    ap.add_argument("--chapters", default="", help="comma-separated chapter numbers (default: all)")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    cleaned, raws = cleaned_dir(args.vol), raw_dir(args.vol)
    want = {c.strip() for c in args.chapters.split(",") if c.strip()}

    from manga_ocr import MangaOcr
    ocr = MangaOcr()
    spell = SpellChecker()

    rows = {}
    for psd_path in sorted(glob.glob(os.path.join(cleaned, "*.psd"))):
        name = os.path.splitext(os.path.basename(psd_path))[0]
        if "_color" in name or "credits" in name or re.search(r"_000[a-z]$", name):
            continue
        m = re.search(r"_(ch(\d+)|extra)_(\d+(?:-\d+)?)$", name)
        if not m:
            continue
        chap = m.group(2) or "extra"
        if want and chap not in want:
            continue
        page = m.group(3)
        psd = PSDImage.open(psd_path)
        W, H = psd.size
        raw = raw_image(raws, name)
        sx, sy = (raw.size[0] / W, raw.size[1] / H) if raw else (0, 0)
        for layer in psd.descendants():
            if layer.kind != "type" or not layer.visible:
                continue
            x1, y1, x2, y2 = layer.bbox
            if x2 <= 0 or y2 <= 0 or x1 >= W or y1 >= H:
                continue  # off-canvas leftovers
            en = layer_text(layer, spell)
            if not en:
                continue
            pt = is_point_text(layer)
            jp = ""
            if raw is not None and not pt:
                pw, ph = (x2 - x1) * PAD, (y2 - y1) * PAD
                box = (max(0, (x1 - pw) * sx), max(0, (y1 - ph) * sy),
                       min(raw.size[0], (x2 + pw) * sx), min(raw.size[1], (y2 + ph) * sy))
                if box[2] - box[0] > 4 and box[3] - box[1] > 4:
                    jp = ocr(raw.crop(tuple(int(v) for v in box)))
            elif raw is None:
                jp = "(no raw)"
            font = font_of(layer) + (" [point/SFX]" if pt else "")
            rows.setdefault(chap, []).append((page, layer.layer_id, font, jp, en))
        print(name, "done", flush=True)

    for chap, rs in rows.items():
        out = local("data", f"{args.vol}_ch{chap}.tsv")
        with open(out, "w", encoding="utf-8") as f:
            f.write("page\tlayer\tfont\tjapanese\tenglish\n")
            for r in rs:
                f.write("\t".join(str(v).replace("\t", " ") for v in r) + "\n")
        print("wrote", out, len(rs), "rows")


if __name__ == "__main__":
    main()
