# Family Photo Archive

A Python 3.12+ pipeline that turns a raw family photo archive into a curated,
metadata-rich one — with a human in the loop for every value that ends up in
the final photos.

## How it works

- **Yandex Disk is the read-only source.** A public folder URL is supplied to
  the CLI at runtime. Nothing in this project ever writes to, moves or deletes
  source data.
- **Google Drive is the processed archive destination.** Everything is created
  below one configured root folder (`google_drive.root_folder_id`).
- **Every source root gets its own dedicated folder**, named after the source
  folder, and nested paths are mirrored recursively below it. With a source
  root named `Family Archive`, `1990/Valaam/020.jpg` becomes
  `<drive root>/Family Archive/1990/Valaam/020.jpg`. Two source roots can
  never write into each other's subtree.
- **`review.xlsx` is the human review surface.** One workbook is created in
  every folder that *directly* contains photos, with embedded previews, the
  source description and proposed metadata. Repeated scans preserve human
  edits: source changes are surfaced, never silently applied.
- **`catalog.xlsx` is the archive-wide knowledge base.** It lives at the Drive
  archive root with `People`, `Places` and `Tags` sheets, and grows from
  approved review rows — better proposals for the next folder.

The current MVP is **`scan`**. EXIF/IPTC/XMP writing (`build`) and Google Photos
publishing (`publish`) are scaffolded but not implemented.

## Setup

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp config.example.yaml config.yaml   # then edit root_folder_id
```

`config.yaml`, `credentials.json` and `token.json` are git-ignored: no secrets
belong in this repository.

[ExifTool](https://exiftool.org) is an external executable used by the future
`build` command. Install it separately (`brew install exiftool`); it is
deliberately not a Python dependency.

## Commands

```bash
python app.py scan "https://disk.yandex.ru/d/<public-folder-id>"
python app.py learn      # not implemented yet
python app.py build      # not implemented yet
python app.py publish    # not implemented yet
```

Unimplemented commands fail explicitly instead of reporting false success.

## Tests

```bash
pytest
```

All tests are local: none of them touch Yandex Disk, Google Drive or Google
Photos.

## Layout

```text
app.py                    CLI entry point (argparse)
photoarchive/
  config.py               typed YAML configuration
  models.py               domain models, workflow statuses, date precision
  state.py                SQLite bookkeeping for repeated runs
  storage/                provider interfaces + Yandex / Google Drive adapters
  scanning/               scan orchestration and description matching
  parsing/                description parsing and metadata proposals
  review/                 review.xlsx schema and workbook service
  catalog/                catalog.xlsx knowledge base
  metadata/               ExifTool writer (processed copies only)
  publishing/             Google Photos publisher
tests/                    local-only unit tests
cache/                    disposable local scratch space
```

See [`mds/family-photo-archive-project.md`](mds/family-photo-archive-project.md)
for the full specification.
