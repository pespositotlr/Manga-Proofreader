# Manga-Proofreader

Proofreads typeset comic pages (Photoshop PSDs) for typos, grammar, inconsistencies and possible
mistranslations, then writes the accepted fixes back into the PSD text layers.

1. **Extract** (free, offline): reads every English text layer with psd-tools, crops the same area
   from the original Japanese page, and OCRs it with manga-ocr.
2. **Check** (paid, Claude API): sends each chapter's Japanese/English pairs to Claude, which returns
   findings only. It skips style preferences and deliberate localization.
3. **Review**: a CSV per chapter, or a numbered list in the terminal, where you accept, reject or
   reword each finding.
4. **Apply**: a Photoshop script changes only the accepted words, keeps line breaks, and skips
   layers that have changed since the plan was made. It also lists every change, with before and
   after aligned, so you can double-check.

A separate free check (`icheck.py`) flags crossbar-I inconsistencies for fonts where the capital I
or `|` is the crossbar I.

## Setup

- Python 3 with `psd-tools`, `manga-ocr`, `pillow`, `pyspellchecker`, `anthropic`, `pywin32`.
- Photoshop (Windows) for the apply step.
- An Anthropic API key (`ANTHROPIC_API_KEY`) for the check step.
- Copy `config.example.json` to `config.json` and fill it in. `config.json` and `local/` are
  git-ignored: project names, paths and all working data stay on your machine.

File layout it expects (set in `config.json`):

```
<volumes_root>/<volume_dir>/<cleaned_dir>/<file_prefix>ch012_034.psd   typeset pages
<volumes_root>/<volume_dir>/<raw_dir>/<file_prefix>ch012_034.jpg       original pages (any size, same ratio)
```

`{vol}` in the patterns is the volume id, e.g. `v01`. Chapter ids look like `v01_ch012`.

## Usage

```
python extract.py v01 --chapters 012,013          # -> local/data/v01_ch012.tsv
python check.py run v01_ch012 v01_ch013           # -> local/review/v01_ch012.csv (normal requests)
python check.py submit v01_ch012 ...              # or: Batch API, half price, can take hours
python check.py fetch <batch id>

python apply.py pending v01_ch012                 # numbered list of findings still in the PSDs
python apply.py decide v01_ch012 1,2,5=ok 3=no "4=Your own wording"
python apply.py plan v01_ch012 --auto-typos --crossbar   # -> local/edits.json (nothing changed yet)
python apply.py run                               # applies in Photoshop, writes local/changed/*.txt

python icheck.py v01 [--match ch012]              # crossbar-I check
```

`local/series_notes.txt` (optional) is sent with every chapter: names and terms known to be spelled
correctly.

Notes:
- `apply.py run` skips PSDs that are open in Photoshop with unsaved changes.
- Layers with mixed character styles (e.g. one italic word) are listed as MANUAL: setting their text
  from a script would flatten the styles.
- `--backup` on `apply.py run` copies the PSDs first. Leave it off if they're in a folder with
  version history.
