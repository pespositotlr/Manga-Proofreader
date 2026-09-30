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
    layer's text from a script flattens mixed styles, so those layers are edited by hand."""
    runs = layer.engine_dict["StyleRun"]["RunArray"]
    lengths = layer.engine_dict["StyleRun"]["RunLengthArray"]
    sheets = [str(r["StyleSheet"]["StyleSheetData"]) for r, n in zip(runs, lengths) if n > 1]
    return len(set(sheets)) <= 1
