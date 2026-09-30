"""Send extracted chapters to Claude and collect findings.

Usage:
  python check.py run v12_ch034 [...]      -> normal requests, results in minutes (full price)
  python check.py submit v12_ch034 [...]   -> Message Batches API (half price, can take hours); id saved to local/batches.txt
  python check.py fetch <batch id>         -> results of a batch

Input: local/data/<id>.tsv from extract.py, plus local/series_notes.txt (names/terms, optional).
Output: local/review/<id>.csv (with an empty "decision" column for the reviewer) and .json.
Series-specific prompt text comes from config.json.
"""

import csv
import json
import os
import re
import sys
import time

import anthropic

from mp_config import CFG, LOCAL, local

MODEL = CFG.get("model", "claude-opus-5-5")
EFFORT = CFG.get("effort", "medium")
PRICE_IN, PRICE_OUT = CFG.get("price_per_mtok", [4.0, 20.0])  # $/M tokens, normal requests; batch is half

SYSTEM = """You are proofreading an old English fan translation of the Japanese manga {series}, made {translated}. The translator, now far more experienced, will review each finding by hand.

Each input line is one English text layer: page, layer id, font, the Japanese OCR'd from the same area of the original page, and the English.

Report only real problems:
- typo: misspelling, doubled or missing word, broken punctuation (e.g. mismatched quotes).
- grammar: an ungrammatical English sentence.
- inconsistency: a name, title or term spelled or rendered differently from elsewhere in the chapter or the series notes; a callback or recurring phrase translated inconsistently.
- mistranslation: the English says something the Japanese doesn't (misread grammar, wrong subject/object, reversed meaning, dropped key meaning, misunderstood idiom).

Do not report:
- style preferences, or lines that could merely be phrased better.
- deliberate localization. Puns, parody, crude jokes and references often need English that differs on purpose to keep a joke working; that is fine.
- sound effects, stylized titles, signs or text where the Japanese is missing or is OCR noise. OCR noise is common (wrong kanji, dropped characters, punctuation only). If the Japanese looks garbled, don't claim a mistranslation from it; use low confidence or skip.
- the order of lines. Lines can be listed out of reading order, and one Japanese bubble may be split over several English layers (or the other way around), so the Japanese on a line may be only part of, or more than, the English.
- capitalization. Almost every font is all caps, so case doesn't show on the page. "|" is some fonts' crossbar "I", and in other fonts a capital I renders as the crossbar I, so a sentence starting with a lowercase "it’s"/"is" is deliberate.{mixed_case}
- hyphens or odd splits inside words in text placed over redrawn art: those are split to fit the space.
- dropped letters in casual speech ("wasn’", "didn’", "doin’", "ya", "ta"). These are deliberate.
- honorifics (-san, -chan, -sama), romanized Japanese terms, "T/N:" notes, or the translator's house style.
{notes}
The series notes list names and words that are known to be spelled correctly.

For each finding give: page, layer, category, the exact current English (or the relevant part), a suggested replacement for that part, a short reason (for mistranslations, say what the Japanese means), and confidence (high/medium/low). Return an empty list if nothing is wrong. Be concise."""


def system_prompt():
    mixed = CFG.get("mixed_case_fonts", [])
    notes = "".join(f"- {n}\n" for n in CFG.get("prompt_notes", []))
    return SYSTEM.format(
        series=CFG["series"], translated=CFG.get("translated", "years ago"),
        mixed_case=(f" Only for layers in a mixed-case font ({', '.join(mixed)}) report a clearly wrong capital."
                    if mixed else ""),
        notes=("\nAbout this series:\n" + notes) if notes else "")


SCHEMA = {
    "type": "object",
    "properties": {
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "page": {"type": "string"},
                    "layer": {"type": "integer"},
                    "category": {"type": "string", "enum": ["typo", "grammar", "inconsistency", "mistranslation"]},
                    "current": {"type": "string"},
                    "suggested": {"type": "string"},
                    "reason": {"type": "string"},
                    "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                },
                "required": ["page", "layer", "category", "current", "suggested", "reason", "confidence"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["findings"],
    "additionalProperties": False,
}


def load_rows(cid):
    with open(os.path.join(LOCAL, "data", cid + ".tsv"), encoding="utf-8") as f:
        return list(csv.DictReader(f, delimiter="\t"))


def chapter_text(rows):
    out = []
    for r in rows:
        jp = r["japanese"]
        if not re.search(r"[぀-ヿ一-鿿]", jp) or "point/SFX" in r["font"]:
            jp = "-"
        out.append(f"p{r['page']} #{r['layer']} [{r['font'].split(' ')[0]}] JP: {jp} | EN: {r['english']}")
    return "\n".join(out)


def notes():
    path = os.path.join(LOCAL, "series_notes.txt")
    return open(path, encoding="utf-8").read().strip() if os.path.exists(path) else "(none)"


def params(cid):
    user = f"Series notes:\n{notes()}\n\nChapter {cid}:\n{chapter_text(load_rows(cid))}"
    return {
        "model": MODEL,
        "max_tokens": 32000,
        "system": system_prompt(),
        "output_config": {"effort": EFFORT, "format": {"type": "json_schema", "schema": SCHEMA}},
        "messages": [{"role": "user", "content": user}],
    }


def run(ids):
    """Normal (non-batch) requests: results in minutes, at full price."""
    client = anthropic.Anthropic()
    total = 0.0
    for cid in ids:
        with client.messages.stream(**params(cid)) as stream:
            msg = stream.get_final_message()
        total += save(cid, msg, PRICE_IN, PRICE_OUT)
    print(f"total ${total:.3f}")


def submit(ids):
    client = anthropic.Anthropic()
    batch = client.messages.batches.create(requests=[{"custom_id": cid, "params": params(cid)} for cid in ids])
    with open(local("batches.txt"), "a", encoding="utf-8") as f:
        f.write(f"{time.strftime('%Y-%m-%d %H:%M')}\t{batch.id}\t{' '.join(ids)}\n")
    print(batch.id, batch.processing_status)


def fetch(batch_id):
    client = anthropic.Anthropic()
    batch = client.messages.batches.retrieve(batch_id)
    if batch.processing_status != "ended":
        print("still", batch.processing_status, batch.request_counts)
        return
    total = 0.0
    for res in client.messages.batches.results(batch_id):
        if res.result.type != "succeeded":
            print(res.custom_id, res.result.type, getattr(res.result, "error", ""))
            continue
        total += save(res.custom_id, res.result.message, PRICE_IN / 2, PRICE_OUT / 2)
    print(f"total ${total:.3f}")


def save(cid, msg, price_in, price_out):
    """Write local/review/<cid>.json and .csv; return the cost in dollars."""
    text = next((b.text for b in msg.content if b.type == "text"), "")
    with open(local("review", cid + ".json"), "w", encoding="utf-8") as f:
        f.write(text)
    u = msg.usage
    cost = (u.input_tokens * price_in + u.output_tokens * price_out) / 1e6
    if msg.stop_reason != "end_turn":
        print(cid, "stop_reason:", msg.stop_reason)
    findings = json.loads(text)["findings"] if text else []
    rows = {(r["page"], str(r["layer"])): r for r in load_rows(cid)}
    out = local("review", cid + ".csv")
    with open(out, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["decision", "page", "layer", "category", "confidence", "current", "suggested", "reason",
                    "full English", "Japanese (OCR)"])
        for x in findings:
            r = rows.get((x["page"].lstrip("p"), str(x["layer"])), {})
            w.writerow(["", x["page"], x["layer"], x["category"], x["confidence"], x["current"],
                        x["suggested"], x["reason"], r.get("english", ""), r.get("japanese", "")])
    print(f"{cid}: {len(findings)} findings, in {u.input_tokens} / out {u.output_tokens} tokens, ${cost:.3f} -> {out}")
    return cost


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    cmd, *rest = sys.argv[1:]
    {"submit": submit, "run": run}.get(cmd, lambda r: fetch(r[0]))(rest)
