"""Turn review findings into PSD text edits, apply them in Photoshop, and report what changed.

Usage:
  python apply.py pending v12_ch034 [...]                          -> numbered list of findings still in the PSDs
  python apply.py plan v12_ch034 [...] [--auto-typos] [--crossbar] [--replace Old=New ...]
                                      -> local/edits.json; nothing is changed
      (--replace: whole-word replacement in every PSD of the volume, e.g. a name spelled two ways)
  python apply.py run [--backup]      -> runs apply_edits.jsx in Photoshop, then writes the change list
  python apply.py changes             -> local/changed/<vol>_changed_<time>.txt (aligned before/after)

Which findings are used:
  - rows whose decision column is "accept" (or "ok"/"y"), or your own wording (used as the replacement)
  - with --auto-typos, also category "typo" at high/medium confidence, unless the change touches
    a hyphen or "|" (split words over redrawn art and the crossbar-I glyph are layout, not typos)
  - with --crossbar, also the crossbar-I case fixes from icheck.py
Rows marked "reject"/"no" are always skipped.

Edits go word by word: line breaks between unchanged words stay (also between words that are each
replaced by one word), and a reworded stretch is typed as plain words. Case-only and quote-style-only differences are ignored. Layers with more than one
character style (e.g. an italic word) are edited character by character so their styles stay
(keep_styles + style_map in edits.json); the few that can't be (vertical text) are listed as MANUAL.
"""

import csv
import difflib
import glob
import json
import os
import re
import shutil
import sys
import time

from psd_tools import PSDImage

from mp_config import LOCAL, REPO, chapter_psds, cleaned_dir, local, psd_path
from psdtext import fonts, paragraph_defaults, style_problem, uniform_style

EDITS = os.path.join(LOCAL, "edits.json")
LOG = os.path.join(LOCAL, "apply_log.txt")
EQUIV = {"'": "['’‘]", "’": "['’‘]", "‘": "['’‘]", '"': '["“”]', "“": '["“”]', "”": '["“”]'}


def loose(s):
    """Regex for text as written in the review sheet: spaces may be line breaks, and a word may be
    broken over a line (with or without a hyphen)."""
    out = []
    for i, c in enumerate(s):
        if c == " ":
            out.append(r"[ \r\u0003]+")
        else:
            out.append(EQUIV.get(c, re.escape(c)))
            if i + 1 < len(s) and s[i + 1] != " ":
                out.append(r"(?:-?[\r\u0003])?")
    # whole words only: "knock of" must not match inside "knock off"
    pre = r"(?<!\w)" if s[:1].isalnum() else ""
    post = r"(?!\w)" if s[-1:].isalnum() else ""
    return pre + "".join(out) + post


def split_diff(cur, new):
    """Common prefix / changed middle / common suffix."""
    p = 0
    while p < min(len(cur), len(new)) and cur[p] == new[p]:
        p += 1
    s = 0
    while s < min(len(cur), len(new)) - p and cur[-1 - s] == new[-1 - s]:
        s += 1
    return cur[:p], cur[p:len(cur) - s], new[p:len(new) - s], cur[len(cur) - s:]


def match_quotes(new, raw):
    """Use the layer's own apostrophe/quote style for inserted text."""
    if "’" in raw or "'" not in raw:
        new = re.sub(r"(?<!\w)'(?=\w)", "‘", new)  # opening single quote
        new = new.replace("'", "’")
    if "“" in raw or '"' not in raw:
        new = re.sub(r'"(?=\w)', "“", new)
        new = re.sub(r'"', "”", new)
    return new


def minimal_edit(span, cur, new):
    """Apply only the words that differ between cur and new to the raw span."""
    cw, nw = cur.split(), new.split()
    m = re.fullmatch(r"[ \r\u0003]+".join(f"({loose(w)})" for w in cw), span, re.I)
    if not m:
        return span
    starts = [m.start(k + 1) for k in range(len(cw))]
    ends = [m.end(k + 1) for k in range(len(cw))]
    fold = lambda s: re.sub(r"[’‘]", "'", re.sub(r"[“”]", '"', s)).lower()
    ops = difflib.SequenceMatcher(None, [fold(w) for w in cw], [fold(w) for w in nw], autojunk=False).get_opcodes()
    out, last = [], 0
    for tag, i1, i2, j1, j2 in ops:
        if tag == "equal":
            # words equal apart from case: take the new case, unless the word has an i/I/| (crossbar typing)
            for k in range(i2 - i1):
                o, n = span[starts[i1 + k]:ends[i1 + k]], nw[j1 + k]
                diff = [a for a, b in zip(o, n) if a != b] if len(o) == len(n) else ["?"]
                if o != n and o.lower() == n.lower() and not any(a in "iI|" for a in diff):
                    out.append(span[last:starts[i1 + k]])
                    out.append(n)
                    last = ends[i1 + k]
            continue
        if tag == "replace" and i2 - i1 == j2 - j1:
            # as many words as before (e.g. quotes changed around a phrase): word for word, so the
            # line breaks between them stay
            for k in range(i2 - i1):
                out.append(span[last:starts[i1 + k]])
                out.append(nw[j1 + k])
                last = ends[i1 + k]
            continue
        words = " ".join(nw[j1:j2])
        if i1 < i2:                      # replace / delete words i1..i2-1
            a, b = starts[i1], ends[i2 - 1]
            if not words:                # deletion: also drop one neighbouring separator
                if i2 < len(cw):
                    b = starts[i2]
                elif i1 > 0:
                    a = ends[i1 - 1]
        else:                            # insertion before word i1 (or at the end)
            if i1 < len(cw):
                a = b = starts[i1]
                words += " "
            else:
                a = b = ends[-1]
                words = " " + words
        out.append(span[last:a])
        out.append(words)
        last = b
    out.append(span[last:])
    return "".join(out)


OPEN_Q, CLOSE_Q = "“‘", "”’"


def _is_open_quote(t, i):
    c = t[i]
    if c == "‘" and i > 0 and t[i - 1].isalnum():
        return False  # a backwards apostrophe inside a word
    return c in OPEN_Q or (c in "\"'" and (i == 0 or not t[i - 1].isalnum()) and i + 1 < len(t) and t[i + 1].isalnum())


def _is_close_quote(t, i):
    c = t[i]
    if c not in CLOSE_Q + "\"'":
        return False
    return c == "”" or ((i == 0 or not t[i - 1].isspace()) and (i + 1 == len(t) or not t[i + 1].isalnum()))


def style_map(old, new):
    """For each character of `new`, the index of the character of `old` whose style it takes, so a
    mixed-style layer can be edited without flattening it (apply_edits.jsx). len(old) stands for the
    end of the text. Unchanged characters keep their own style, and a replaced stretch takes the style
    of the characters it replaces, one for one. Added characters take the style of the text they touch:
    the character before them (the start of the text: the one after), except that text added after a
    closing quote or before an opening quote stays outside the quote, and a word on its own between
    spaces takes the style of the word before it.
    Returned run-length encoded for edits.json: [[count, old index, step], ...]."""
    src = [None] * len(new)

    def chars(i0, a, j0, b):  # old[i0:] ~ new[j0:] for a stretch of replaced words: by character
        for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
            if op in ("equal", "replace"):
                for k in range(min(i2 - i1, j2 - j1)):
                    src[j0 + j1 + k] = i0 + i1 + k

    # words first (a letter-level diff would match stray letters of unrelated words)
    tok = lambda t: [(m.start(), m.group()) for m in re.finditer(r"\w+|[^\w]", t)]
    ot, nt = tok(old), tok(new)
    ops = difflib.SequenceMatcher(None, [w for _, w in ot], [w for _, w in nt], autojunk=False).get_opcodes()
    for op, i1, i2, j1, j2 in ops:
        if op in ("equal", "replace"):  # word for word; extra new words count as added
            for (io, ow), (jo, nw) in zip(ot[i1:i2], nt[j1:j2]):
                chars(io, ow, jo, nw)
    space = lambda c: c.isspace() or c == "\u0003"
    j = 0
    while j < len(new):
        if src[j] is not None:
            j += 1
            continue
        a = j
        while j < len(new) and src[j] is None:
            j += 1
        b, added = j, new[a:j]  # new[a:b] was added; new[a-1] and new[b] have their old index
        after = src[b] if b < len(new) else len(old)
        rn = next((x for x in range(b, len(new)) if not space(new[x])), None)
        right = src[rn] if rn is not None else len(old)  # the next character that isn't a space
        ln = next((x for x in range(a - 1, -1, -1) if not space(new[x])), None)
        if a == 0:
            pick = after
        elif _is_close_quote(new, a - 1):
            pick = right
        elif b < len(new) and _is_open_quote(new, b):
            pick = src[ln] if ln is not None else src[a - 1]
        elif not space(added[0]) and not space(new[a - 1]):
            pick = src[a - 1]
        elif b < len(new) and not space(added[-1]) and not space(new[b]):
            pick = after
        elif ln is not None and _is_close_quote(new, ln):
            pick = right
        elif rn is not None and _is_open_quote(new, rn):
            pick = src[ln] if ln is not None else src[a - 1]
        else:
            pick = src[a - 1]
        src[a:b] = [pick] * (b - a)
    runs = []
    for k, s in enumerate(src):
        if runs:
            n, i, step = runs[-1]
            if n == 1 and s in (i, i + 1):
                runs[-1] = [2, i, s - i]
                continue
            if s == i + n * step:
                runs[-1][0] += 1
                continue
        runs.append([1, s, 1])
    return runs


def wanted(row, auto):
    d = row["decision"].strip()
    if d.lower() in ("reject", "n", "no", "x", "hold", "later", "?"):
        return None
    if d.lower() in ("accept", "y", "yes", "ok"):
        return row["suggested"]
    if d:
        return d  # the reviewer's own wording
    if auto and row["category"] == "typo" and row["confidence"] in ("high", "medium"):
        _, old_mid, new_mid, _ = split_diff(row["current"], row["suggested"])
        if not re.search(r"[-|]", old_mid + new_mid):
            return row["suggested"]
    return None


def review_rows(cid):
    return list(csv.DictReader(open(os.path.join(LOCAL, "review", cid + ".csv"), encoding="utf-8-sig")))


KEPT = {"": "", "keep": "  [styles kept]"}


def style_mode(layer):
    """"" for a one-style layer (its text is simply replaced), "keep" for a mixed-style layer that is
    edited character by character, or why it has to be fixed by hand."""
    if uniform_style(layer):
        return ""
    problem = style_problem(layer)
    return f"mixed styles, {problem}" if problem else "keep"


def new_edit(path, layer, old, new, tag, mode):
    e = {"psd": path, "layer": layer.layer_id, "old": old, "new": new, "tag": tag}
    if mode == "keep":
        e["keep_styles"] = True
        e["para_keep"] = paragraph_defaults(layer)
    return e


def plan(ids, auto, crossbar=False, replace=()):
    edits, report = [], []
    for cid in ids:
        for row in review_rows(cid):
            new_text = wanted(row, auto)
            if new_text is None:
                continue
            page, lid = row["page"].lstrip("p"), int(row["layer"])
            tag = f"{cid} p{page} #{lid}"
            path = psd_path(cid, page)
            layer = path and next((l for l in PSDImage.open(path).descendants() if l.layer_id == lid), None)
            if layer is None:
                report.append(f"NOT FOUND  {tag}")
                continue
            prev = next((e for e in edits if e["psd"] == path and e["layer"] == lid), None)
            raw = prev["new"] if prev else layer.text  # several findings on one layer stack up
            # applied in an earlier run: only provable here when the old text is part of the new one
            # (otherwise a deletion like "is a is a" -> "is a" would always look done)
            if flat(row["current"]).lower() in flat(new_text).lower() and \
                    re.search(loose(match_quotes(new_text, raw)), raw, re.I):
                report.append(f"ALREADY OK {tag}")
                continue
            m = re.search(loose(row["current"]), raw, re.I)  # case may differ after crossbar-I fixes
            if not m:
                done = re.search(loose(new_text), raw, re.I)
                report.append(f"{'ALREADY OK' if done else 'NO MATCH  '} {tag}: {row['current']!r}")
                continue
            new_span = minimal_edit(m.group(0), row["current"], match_quotes(new_text, raw))
            if new_span == m.group(0):
                report.append(f"NO CHANGE  {tag}: only case/quote style differed")
                continue
            mode = style_mode(layer)
            if mode not in ("", "keep"):
                report.append(f"MANUAL     {tag}: {mode}; change {row['current']!r} -> {new_text!r}")
                continue
            if prev:
                prev["new"] = raw[:m.start()] + new_span + raw[m.end():]
            else:
                edits.append(new_edit(path, layer, raw, raw[:m.start()] + new_span + raw[m.end():], tag, mode))
            report.append(f"EDIT       {tag}: {m.group(0)!r} -> {new_span!r}{KEPT[mode]}")
    for old_word, new_word in replace:
        # whole-word replacement in every PSD of the volumes involved, e.g. a name spelled two ways
        by_key = {(e["psd"], e["layer"]): e for e in edits}
        pat = re.compile(rf"(?<!\w){loose(old_word)}(?!\w)", re.I)
        for vol in sorted({cid.split("_")[0] for cid in ids}):
            for path in sorted(glob.glob(os.path.join(cleaned_dir(vol), "*.psd"))):
                ch_page = re.search(r"_((?:ch\d+|extra))_(\d+(?:-\d+)?)\.psd$", path)
                for layer in PSDImage.open(path).descendants():
                    if layer.kind != "type":
                        continue
                    key = (path, layer.layer_id)
                    base = by_key[key]["new"] if key in by_key else layer.text
                    new = pat.sub(lambda m: new_word.upper() if m.group().isupper() else new_word, base)
                    if new == base:
                        continue
                    tag = f"{vol}_{ch_page.group(1)} p{ch_page.group(2)} #{layer.layer_id}" if ch_page else path
                    mode = style_mode(layer)
                    if mode not in ("", "keep"):
                        report.append(f"MANUAL     {tag}: {mode}; {old_word} -> {new_word}")
                        continue
                    if key in by_key:
                        by_key[key]["new"] = new
                    else:
                        by_key[key] = new_edit(path, layer, layer.text, new, tag, mode)
                        edits.append(by_key[key])
                    report.append(f"REPLACE    {tag}: {old_word} -> {new_word}{KEPT[mode]}")
    if crossbar:
        from icheck import apply_fixes
        by_key = {(e["psd"], e["layer"]): e for e in edits}
        for cid in ids:
            for path in chapter_psds(cid):
                if "_color" in path:
                    continue
                m = re.search(r"_(\d+(?:[-_]\d+)?)\.psd$", path)
                if not m:  # e.g. a sync tool's "conflicted copy"
                    continue
                page = m.group(1)
                for layer in PSDImage.open(path).descendants():
                    if layer.kind != "type" or not layer.visible:
                        continue
                    try:
                        fn = fonts(layer)
                    except Exception:
                        continue
                    key = (path, layer.layer_id)
                    base = by_key[key]["new"] if key in by_key else layer.text
                    new = apply_fixes(base, fn)
                    if new == base:
                        continue
                    tag = f"{cid} p{page} #{layer.layer_id}"
                    changed = [f"{a}->{b}" for a, b in zip(re.findall(r"\S+", base), re.findall(r"\S+", new)) if a != b]
                    mode = style_mode(layer)
                    if mode not in ("", "keep"):
                        report.append(f"MANUAL     {tag}: {mode}; crossbar I: {', '.join(changed)}")
                        continue
                    if key in by_key:
                        by_key[key]["new"] = new
                    else:
                        by_key[key] = new_edit(path, layer, layer.text, new, tag, mode)
                        edits.append(by_key[key])
                    report.append(f"CROSSBAR   {tag}: {', '.join(changed)}{KEPT[mode]}")
    for e in edits:  # mixed-style layers: which old character each new one takes its style from
        if e.get("keep_styles"):
            e["style_map"] = style_map(e["old"], e["new"])
    with open(local("edits.json"), "w", encoding="utf-8") as f:
        json.dump(edits, f, ensure_ascii=True, indent=1)
    print("\n".join(report))
    print(f"\n{len(edits)} edits in {len({e['psd'] for e in edits})} PSDs -> {EDITS}")


def flat(s):
    return re.sub(r"\s+", " ", s.replace("\u0003", " ").replace("\r", " ")).strip()


def aligned(old, new, width=100):
    """before/after lines padded word by word so unchanged text lines up, plus a ^ row under the
    changes. Long lines are cut into chunks of `width` columns; only chunks with changes are shown."""
    a, b = flat(old).split(" "), flat(new).split(" ")
    top, bot, mark = [], [], []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if op == "equal":
            x = " ".join(a[i1:i2])
            top.append(x); bot.append(x); mark.append(" " * len(x))
            continue
        x, y = " ".join(a[i1:i2]), " ".join(b[j1:j2])
        n = max(len(x), len(y))
        top.append(x.ljust(n)); bot.append(y.ljust(n)); mark.append("^" * n)
    top, bot, mark = " ".join(top), " ".join(bot), " ".join(mark)
    rows = []
    for s in range(0, len(top), width):
        if "^" in mark[s:s + width] or len(top) <= width:
            rows += [f"before: {top[s:s + width].rstrip()}", f"after:  {bot[s:s + width].rstrip()}",
                     f"        {mark[s:s + width].rstrip()}"]
    return "\n".join(rows)


def pending(ids):
    """Numbered list of findings whose text is still in the PSD. Numbers are the row numbers in
    local/review/<id>.csv (1 = first finding)."""
    for cid in ids:
        cache, shown, gone = {}, 0, 0
        print(f"=== {cid} ===")
        rows = list(enumerate(review_rows(cid), 1))
        rows.sort(key=lambda r: ([int(n) for n in re.findall(r"\d+", r[1]["page"])] or [0], int(r[1]["layer"])))
        for i, row in rows:  # page order; the numbers stay the CSV row numbers
            if row["decision"].strip():
                continue
            page, lid = row["page"].lstrip("p"), int(row["layer"])
            path = psd_path(cid, page)
            if path not in cache:
                cache[path] = {l.layer_id: l for l in PSDImage.open(path).descendants()} if path else {}
            layer = cache[path].get(lid)
            if layer is None or not re.search(loose(row["current"]), layer.text, re.I):
                gone += 1
                continue
            shown += 1
            mode = style_mode(layer)
            print(f"\n[{i}] p{page} #{lid}  {row['category']} ({row['confidence']})"
                  + (f"  [{mode}: fix by hand]" if mode not in ("", "keep") else ""))
            m = re.search(loose(row["current"]), layer.text, re.I)
            after = layer.text[:m.start()] + minimal_edit(m.group(0), row["current"],
                                                          match_quotes(row["suggested"], layer.text)) + layer.text[m.end():]
            print(f"line:   {flat(layer.text)}")
            print(f"after:  {flat(after)}")  # the whole line, so the change is easy to see in context
            print(f"why:    {row['reason']}")
            jp = row.get("Japanese (OCR)", "")
            if row["category"] == "mistranslation" and jp:
                print(f"JP:     {jp}")
        print(f"\n{shown} to review, {gone} already fixed or changed\n")


def decide(cid, spec):
    """Write decisions into local/review/<cid>.csv. spec: "1,2,5=ok 3=no" or "all=ok"."""
    path = os.path.join(LOCAL, "review", cid + ".csv")
    rows = list(csv.reader(open(path, encoding="utf-8-sig")))
    for part in spec:
        nums, _, value = part.partition("=")
        idx = range(1, len(rows)) if nums == "all" else [int(n) for n in nums.split(",")]
        for i in idx:
            rows[i][0] = "accept" if value.lower() in ("ok", "accept", "y", "yes") else value
    csv.writer(open(path, "w", encoding="utf-8-sig", newline="")).writerows(rows)


def changes():
    """Write local/changed/<vol>_changed_<time>.txt: every layer Photoshop changed, aligned before/after."""
    edits = json.load(open(EDITS, encoding="utf-8"))
    if os.path.exists(LOG):  # only list what Photoshop actually changed
        ok = {l[4:].split(" (font ")[0].strip() for l in open(LOG, encoding="utf-8") if l.startswith("OK: ")}
        edits = [e for e in edits if e["tag"] in ok]
    vol = edits[0]["tag"].split("_")[0] if edits else "v"
    out = [f"{os.path.basename(e['psd'])[:-4]}  #{e['layer']}\n{aligned(e['old'], e['new'])}\n"
           for e in sorted(edits, key=lambda e: (e["psd"], e["layer"]))]
    stamp = time.strftime("%Y%m%d-%H%M", time.localtime(os.path.getmtime(LOG))) if os.path.exists(LOG) else "plan"
    path = local("changed", f"{vol}_changed_{stamp}.txt")
    open(path, "w", encoding="utf-8").write("\n".join(out))
    print(f"{len(edits)} changes -> {path}")


def run(backup=False):
    """Local backups are opt-in (the PSDs may already be in a synced folder with version history)."""
    edits = json.load(open(EDITS, encoding="utf-8"))
    if backup:
        stamp = time.strftime("%Y%m%d-%H%M")
        for psd in sorted({e["psd"] for e in edits}):
            dst = os.path.join(os.path.dirname(psd), "..", "Proofread Backups", stamp)
            os.makedirs(dst, exist_ok=True)
            shutil.copy2(psd, dst)
        print(f"backed up to ...\\Proofread Backups\\{stamp}")
    import win32com.client
    ps = win32com.client.Dispatch("Photoshop.Application")
    if os.path.exists(LOG):
        os.remove(LOG)
    target = os.path.join(REPO, "local", "edits_target.txt")  # tells apply_edits.jsx which edits.json to use
    with open(target, "w", encoding="utf-8") as f:
        f.write(EDITS.replace("\\", "/") + "\n")
    try:
        ps.DoJavaScriptFile(os.path.join(REPO, "apply_edits.jsx"))
    finally:
        os.remove(target)
    print(open(LOG, encoding="utf-8").read())


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    args = sys.argv[1:] or [""]
    if args[0] == "plan":
        reps = [tuple(args[i + 1].split("=", 1)) for i, a in enumerate(args) if a == "--replace"]
        skip = {i + 1 for i, a in enumerate(args) if a == "--replace"}
        plan([a for i, a in enumerate(args[1:], 1) if not a.startswith("--") and i not in skip],
             "--auto-typos" in args, "--crossbar" in args, reps)
    elif args[0] == "pending":
        pending(args[1:])
    elif args[0] == "decide":
        decide(args[1], args[2:])
    elif args[0] == "changes":
        changes()
    elif args[0] == "run":
        run("--backup" in args)
        changes()
    else:
        sys.exit("usage: apply.py plan|pending|decide|changes|run ...")
