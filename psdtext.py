"""Reading text layers with psd-tools."""

import re


def layer_text(layer, spell):
    """Layer text on one line: manual line breaks undone. A hyphen at a line break is either manual
    hyphenation ("consis-/tently") or a real hyphen ("Nishi-/Shinjuku"): the word is joined only if
    the result is a dictionary word."""
    t = layer.text.replace("\u0003", "\r")

    def join(m):
        joined = m.group(1) + m.group(2)
        return joined if not spell.unknown([joined.lower()]) else m.group(1) + "-" + m.group(2)
    t = re.sub(r"(\w+)-\r(\w+)", join, t)
    return re.sub(r"\s+", " ", t.replace("\r", " ")).strip()


def is_point_text(layer):
    """Point text (no text box). Sound effects are usually point text."""
    try:
        layer.engine_dict["Rendered"]["Shapes"]["Children"][0]["Cookie"]["Photoshop"]["BoxBounds"]
        return False
    except Exception:
        return True


def font_of(layer):
    """Font of the first style run."""
    try:
        fs = layer.engine_dict["StyleRun"]["RunArray"][0]["StyleSheet"]["StyleSheetData"]
        return str(layer.resource_dict["FontSet"][fs.get("Font", 0)]["Name"]).strip("'")
    except Exception:
        return "?"


def fonts(layer):
    """All fonts used in the layer."""
    fs = layer.resource_dict["FontSet"]
    runs = layer.engine_dict["StyleRun"]["RunArray"]
    return {str(fs[r["StyleSheet"]["StyleSheetData"].get("Font", 0)]["Name"]).strip("'") for r in runs}


def uniform_style(layer):
    """True if every run (longer than one character) has the same character style. Setting a
    layer's text from a script flattens mixed styles, so apply_edits.jsx edits those layers
    character by character instead (see style_problem)."""
    runs = layer.engine_dict["StyleRun"]["RunArray"]
    lengths = layer.engine_dict["StyleRun"]["RunLengthArray"]
    sheets = [str(r["StyleSheet"]["StyleSheetData"]) for r, n in zip(runs, lengths) if n > 1]
    return len(set(sheets)) <= 1


def style_problem(layer):
    """Why a mixed-style layer can't be edited character by character (apply_edits.jsx keeps its
    styles), or "" if it can."""
    try:
        if layer.engine_dict["Rendered"]["Shapes"].get("WritingDirection", 0) != 0:
            return "vertical text"
    except Exception:
        return "no text engine data"
    return ""


def paragraph_defaults(layer):
    """Paragraph settings Photoshop's Action Manager leaves out when they equal the document's
    default paragraph style, and then resets to its own defaults when the text is written back:
    {Action Manager key: value} for the ones that are off in every paragraph of the layer."""
    try:
        sheets = layer.resource_dict["ParagraphSheetSet"]
        out = {}
        for key, am, off in (("EveryLineComposer", "textEveryLineComposer", False), ("Burasagari", "burasagari", "burasagariNone")):
            values = set()
            for run in layer.engine_dict["ParagraphRun"]["RunArray"]:
                sheet = run["ParagraphSheet"]
                props = sheet.get("Properties", {})
                default = sheets[int(sheet.get("DefaultStyleSheet", 0))]["Properties"]
                values.add(bool(props[key] if key in props else default.get(key, True)))
            if values == {False}:
                out[am] = off
        return out
    except Exception:
        return {}
