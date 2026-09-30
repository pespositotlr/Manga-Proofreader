"""Free check of crossbar-I usage in typeset PSDs.

Usage: python icheck.py v12 [--match ch034]

Font lists come from config.json (PostScript-name prefixes, case-insensitive):
  "crossbar_fonts": a capital I always renders as the crossbar I. Flags
      - a capital I that isn't the pronoun ("It", "If", "In"...)  -> should be typed lowercase
        (skipped for "mixed_case_fonts", where a lowercase i would show)
      - the pronoun typed as "i" or "|"                            -> should be a capital I
  "pipe_fonts": "|" is the crossbar I. Flags the pronoun typed as "I" or "i" (should be "|").
Other fonts (e.g. ones that pick the crossbar I automatically) aren't checked.
"""

import glob
import os
import re
import sys

from psd_tools import PSDImage

from mp_config import CFG, cleaned_dir
from psdtext import fonts


def _prefixes(key):
    items = CFG.get(key, [])
    return re.compile("|".join(f"^{re.escape(p)}" for p in items), re.I) if items else None


CROSSBAR_FONTS = _prefixes("crossbar_fonts")
PIPE_FONTS = _prefixes("pipe_fonts")
MIXED_CASE = _prefixes("mixed_case_fonts")
PRONOUN = re.compile(r"^I(?:['’](?:m|ll|d|ve))?$")
WORD = re.compile(r"[A-Za-z|]+(?:['’][A-Za-z]+)?")


def _all(pattern, names):
    return bool(pattern) and all(pattern.search(f) for f in names)


def fixes(raw, fnames):
    """List of (start, end, old word, new word) in the raw layer text."""
    flat = re.sub(r"\s+", " ", raw.replace("\u0003", " ").replace("\r", " "))
    if re.search(r"(?:\b\w ){4}", flat):
        return []  # spaced-out letters ("W A A A i T")
    out = []
    if _all(PIPE_FONTS, fnames):
        for m in WORD.finditer(raw):
            w = m.group()
            if w[0] in "Ii" and PRONOUN.match(w.replace("i", "I", 1)) and raw[m.end():m.end() + 1] not in "-.~":
                out.append((m.start(), m.end(), w, "|" + w[1:]))
        return out
    if not _all(CROSSBAR_FONTS, fnames):
        return []
    mixed_case = bool(MIXED_CASE) and any(MIXED_CASE.search(f) for f in fnames)
    for m in WORD.finditer(raw):
        w, after = m.group(), raw[m.end():m.end() + 1]
        if "I" in w and not PRONOUN.match(w) and not w.isupper() and not mixed_case:
            out.append((m.start(), m.end(), w, w.replace("I", "i")))  # "It" -> "it"
        elif PRONOUN.match(w.replace("|", "I").replace("i", "I", 1)) and w[0] in "i|" \
                and after not in "-.~" and not re.search(r"\b\w \w $", raw[:m.start()]):
            out.append((m.start(), m.end(), w, "I" + w[1:]))  # pronoun typed as i / |
    return out


def apply_fixes(raw, fnames):
    new = raw
    for s, e, _, w in reversed(fixes(raw, fnames)):
        new = new[:s] + w + new[e:]
    return new


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    folder = cleaned_dir(sys.argv[1])
    match = sys.argv[sys.argv.index("--match") + 1] if "--match" in sys.argv else ""
    n = 0
    for path in sorted(glob.glob(os.path.join(folder, "*.psd"))):
        name = os.path.basename(path)
        if match not in name or "_color" in name:
            continue
        for layer in PSDImage.open(path).descendants():
            if layer.kind != "type" or not layer.visible:
                continue
            try:
                fnames = fonts(layer)
            except Exception:
                continue
            found = fixes(layer.text, fnames)
            if found:
                n += 1
                text = re.sub(r"\s+", " ", layer.text.replace("\u0003", " ").replace("\r", " "))
                print(f"{name[:-4]} #{layer.layer_id} [{'/'.join(sorted(fnames))}]: "
                      f"{', '.join(dict.fromkeys(f'{o}->{w}' for _, _, o, w in found))}  |  {text[:80]}")
    print(f"\n{n} layers flagged")


if __name__ == "__main__":
    main()
