"""Shared settings and paths. Project-specific values live in config.json (git-ignored);
see config.example.json. Working data (extracted text, review sheets, edit plans) goes in local/.
To work on a second project side by side, set MP_CONFIG to another config file (e.g. config.other.json)
and give it its own "local_dir" so its working data stays separate."""

import glob
import json
import os
import sys

REPO = os.path.dirname(os.path.abspath(__file__))


def _load():
    path = os.path.join(REPO, os.environ.get("MP_CONFIG", "config.json"))
    if not os.path.exists(path):
        sys.exit("config.json not found: copy config.example.json to config.json and fill it in.")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


CFG = _load()
LOCAL = os.path.join(REPO, CFG.get("local_dir", "local"))


def local(*parts):
    """Path inside local/, creating the folder if needed."""
    path = os.path.join(LOCAL, *parts)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    return path


def volume_dir(vol):
    """The volume's folder. "volumes_root" can be one folder or a list; the first one with a match wins."""
    roots = CFG["volumes_root"]
    for root in [roots] if isinstance(roots, str) else roots:
        hits = [h for h in glob.glob(os.path.join(root, CFG["volume_dir"].format(vol=vol))) if os.path.isdir(h)]
        if len(hits) > 1:
            sys.exit(f"volume folder for {vol}: expected 1 match in {root}, found {len(hits)}")
        if hits:
            return hits[0]
    sys.exit(f"volume folder for {vol}: no match")


def _sub(vol, key):
    """A subfolder of the volume. The pattern can be one glob or a list; the first one with exactly 1 match wins."""
    pats = CFG[key]
    for pat in [pats] if isinstance(pats, str) else pats:
        hits = [h for h in glob.glob(os.path.join(volume_dir(vol), pat)) if os.path.isdir(h)]
        if len(hits) == 1:
            return hits[0]
    sys.exit(f"{key} for {vol}: no pattern had exactly 1 match")


def cleaned_dir(vol):
    """"cleaned_dir_map" in config can point a volume at a differently named folder."""
    if vol in CFG.get("cleaned_dir_map", {}):
        return CFG["cleaned_dir_map"][vol]
    return _sub(vol, "cleaned_dir")


def raw_dir(vol):
    """"raw_dir_map" in config can point a volume at a raw folder somewhere else."""
    if vol in CFG.get("raw_dir_map", {}):
        return CFG["raw_dir_map"][vol]
    return _sub(vol, "raw_dir")


def prefix(vol):
    return CFG["file_prefix"].format(vol=vol)


def psd_path(cid, page):
    """PSD for chapter id (e.g. v12_ch034) and page (e.g. 005)."""
    vol, ch = cid.split("_")
    path = os.path.join(cleaned_dir(vol), f"{prefix(vol)}{ch}_{page}.psd")
    return path if os.path.exists(path) else None


def chapter_psds(cid):
    vol, ch = cid.split("_")
    return sorted(glob.glob(os.path.join(cleaned_dir(vol), f"{prefix(vol)}{ch}_*.psd")))


def api_client():
    """Anthropic client for the checks. The key comes from ANTHROPIC_API_KEY or, failing that, from the file
    ~/.anthropic_batch_key, so it can stay out of the Windows environment (Claude Code would bill to it)."""
    import anthropic
    key = os.environ.get("ANTHROPIC_API_KEY")
    path = os.path.join(os.path.expanduser("~"), ".anthropic_batch_key")
    if not key and os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            key = f.read().strip()
    if not key:
        sys.exit(f"No API key: put it in {path} (one line).")
    return anthropic.Anthropic(api_key=key)
