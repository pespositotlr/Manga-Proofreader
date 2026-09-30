"""Shared settings and paths. Project-specific values live in config.json (git-ignored);
see config.example.json. Working data (extracted text, review sheets, edit plans) goes in local/."""

import glob
import json
import os
import sys

REPO = os.path.dirname(os.path.abspath(__file__))
LOCAL = os.path.join(REPO, "local")


def _load():
    path = os.path.join(REPO, "config.json")
    if not os.path.exists(path):
        sys.exit("config.json not found: copy config.example.json to config.json and fill it in.")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


CFG = _load()


def local(*parts):
    """Path inside local/, creating the folder if needed."""
    path = os.path.join(LOCAL, *parts)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    return path


def volume_dir(vol):
    hits = glob.glob(os.path.join(CFG["volumes_root"], CFG["volume_dir"].format(vol=vol)))
    if len(hits) != 1:
        sys.exit(f"volume folder for {vol}: expected 1 match, found {len(hits)}")
    return hits[0]


def _sub(vol, key):
    hits = glob.glob(os.path.join(volume_dir(vol), CFG[key]))
    if len(hits) != 1:
        sys.exit(f"{key} for {vol}: expected 1 match, found {len(hits)}")
    return hits[0]


def cleaned_dir(vol):
    return _sub(vol, "cleaned_dir")


def raw_dir(vol):
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
