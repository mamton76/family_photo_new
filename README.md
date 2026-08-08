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

## What works today

**`scan --dry-run` against a public Yandex Disk folder.** It reads the real
archive over the official public-resources API and prints what a scan would
do — resolved root name, discovered folders and files, photo-containing
folders, description files and any description conflicts.

```bash
python app.py scan "https://disk.yandex.ru/d/<public-folder-id>" --dry-run
python app.py --verbose scan "https://disk.yandex.ru/d/<id>" --dry-run  # list filenames
```

- **Public folders work without authentication.** No OAuth, no Yandex token,
  no credentials in configuration.
- **This phase is strictly read-only.** The Yandex adapter issues only `GET`
  requests and has no upload, delete, move or rename method at all, so it is
  safe to run repeatedly.
- **`--dry-run` does not touch Google Drive.** It contacts no Google service,
  creates no folders, uploads nothing, writes no `review.xlsx` and records
  nothing in `archive.sqlite`. It is inspection only.

### Description documents

Descriptions live in a Word document beside the photos, and the parser is
built around the format the real archive actually uses:

- **Only `.docx` is parsed.** A folder needs exactly one; zero means no
  description, and several is a genuine conflict where none is chosen
  automatically. `.rtf`, `.txt`, `.doc` and `.pdf` are **ignored but
  reported** in the dry run, so nothing goes unnoticed.
- **One DOCX may describe photos that are not in the folder.** Those entries
  are kept with status `DESCRIBED_ABSENT` — distinct from `SOURCE_MISSING`,
  which means a photo the pipeline saw before has since disappeared. Enough
  context is stored to look for them in other source roots later; that
  cross-folder search is not implemented yet.
- **Multi-paragraph descriptions are grouped.** A new entry starts at a
  paragraph beginning with a photo reference; following paragraphs belong to
  that entry. What counts as a reference is decided *per folder*, strongest
  signal first: the filenames actually present (`020` next to `020.jpg`),
  then filename-shaped tokens (`20200512_150442`, `IMG_001`, `DSC0042`), then
  bare numbers whose width matches a style already established there. So
  `020` is a reference beside `018.jpg`/`019.jpg`, while `1979. Тоня…` stays
  ordinary continuation text. When in doubt the paragraph is treated as
  continuation — merging text is recoverable, inventing a photo is not.
- **`Далее …` is a section divider**, inherited as *section context* by every
  entry that follows it and never merged into a photo's own description.
- **`нет фото` means the paper original has been lost**, not that the digital
  file is missing. It is lifted out of the description and preserved verbatim
  as a source note.

Descriptions are captured as **source text only**. No people, places, dates,
tags, events or birth years are extracted yet — that is a later phase.

Still to come, and **not** implemented: Google Drive access, `review.xlsx` and
`catalog.xlsx` generation, EXIF/IPTC/XMP writing (`build`), Google Photos
publishing (`publish`), and metadata inference (people, places, tags, dates).
A `scan` without `--dry-run` fails explicitly rather than pretending to work.

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
python app.py scan "https://disk.yandex.ru/d/<id>" --dry-run   # works today
python app.py scan "https://disk.yandex.ru/d/<id>"             # needs Google Drive
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
  scanning/               scan orchestration, description matching, dry-run report
  parsing/                DOCX extraction, description entries, proposals
  review/                 review.xlsx schema and workbook service
  catalog/                catalog.xlsx knowledge base
  metadata/               ExifTool writer (processed copies only)
  publishing/             Google Photos publisher
tests/                    local-only unit tests
cache/                    disposable local scratch space
```

See [`mds/family-photo-archive-project.md`](mds/family-photo-archive-project.md)
for the full specification.
