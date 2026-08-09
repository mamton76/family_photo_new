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

**`scan --local-review` generates real `review.xlsx` workbooks** with embedded
previews, machine suggestions and safe rescans, plus a `catalog.xlsx` view of
the dictionaries. Everything is written locally; no Google service is contacted.

```bash
python app.py scan "https://disk.yandex.ru/d/<id>" --local-review
python app.py scan "https://disk.yandex.ru/d/<id>" --local-review --output-dir ./out
```

### Suggested vs final metadata

Every metadata field appears twice. `Suggested Date` is machine-owned and may
be recomputed on any scan; `Date` is yours.

- **On first creation only**, each final field is seeded from its suggestion.
- **On every later scan**, suggestions are refreshed and final values are left
  alone — even when a final value still equals the old suggestion. The pipeline
  does not try to guess whether you edited it, because guessing wrong would
  discard a deliberate decision.
- The one exception is **`Map Link`**: pasting a Google Maps URL is an explicit
  instruction, so it updates the final `LatLon`. A link that carries no
  coordinates changes nothing and says so in `Review Reason`.

### Safe rescans

| What changed | What happens |
|---|---|
| Nothing | Row untouched; row order identical |
| Description text | Source columns and suggestions refresh; row → `REVIEW` |
| Description on an approved row | Same, but reason says *after approval*; final metadata kept |
| Photo bytes | Preview refreshes; row → `REVIEW`, reason *Source photo changed* |
| Photo disappears | Row → `SOURCE_MISSING`; row and final metadata kept |
| Absent photo appears | **Same row reused**, never duplicated; reason *Previously absent photo found* |

### Coordinates

One combined text field everywhere: `55.712345, 37.623456` — latitude first,
decimal degrees. Both coordinate cells are clickable Google Maps hyperlinks.

### Dictionaries

`People`, `Places` and `Tags` live in SQLite and are exported to
`catalog.xlsx`, where **candidate** aliases and coordinates are shaded amber to
keep them visibly distinct from **confirmed** knowledge. Matching prefers
canonical names, then confirmed aliases, longest phrase first; candidates are
hints and never become suggestions.

**Suggestions never teach the dictionary — only approved, human-reviewed final
metadata does.** Ambiguous mappings become candidates with evidence attached
rather than confirmed facts, and kinship words like `мама` are never promoted
to universal aliases. Confirmed place coordinates are never overwritten: a
materially different proposal is stored as a conflict for a human to settle.

### Diagnostic logs

Every run writes a full `DEBUG` transcript to `./logs/run-<id>.log` — decisions
per row, counts, HTTP calls and stack traces — whatever the console verbosity.
The path is printed at the end of each run, and the file is self-contained
enough to hand to someone (or something) for diagnosis. Use `--log-dir` to
change where it goes.


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

Still to come, and **not** implemented: Google Drive upload/sync,
EXIF/IPTC/XMP writing (`build`), Google Photos publishing (`publish`), and
automatic metadata extraction from free text. A plain `scan` — without
`--dry-run` or `--local-review` — needs Google Drive and fails explicitly
rather than pretending to work.

## Setup

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp config.example.yaml config.yaml   # then list your Yandex shares under 'sources:'
```

`config.yaml`, `credentials.json` and `token.json` are git-ignored: no secrets
belong in this repository.

[ExifTool](https://exiftool.org) is an external executable used by the future
`build` command. Install it separately (`brew install exiftool`); it is
deliberately not a Python dependency.

## Commands

### One command

List your Yandex shares once in `config.yaml`:

```yaml
sources:
  - url: "https://disk.yandex.ru/d/cWAy_XDLrIfMsg"
    label: "Ф-ТоняМам-76-83-разное"
  - url: "https://disk.yandex.ru/d/cFwfbSEQ7IB37g"
    label: "Ф-ТоняМам-83-93-школа"

output_dir: "./review-output"
```

Then the whole local pipeline is:

```bash
python app.py run                     # scan all sources → learn → dashboard → portable state
python app.py run --skip-learn        # scan and dashboard only
python app.py run --skip-dashboard    # scan and learn only
python app.py run --output-dir ./out  # override the configured directory
```

Safe to repeat: an unchanged run changes nothing, and it never overwrites a
final value you typed.

A bare URL works too, and `enabled: false` skips a folder without deleting it:

```yaml
sources:
  - "https://disk.yandex.ru/d/abc"
  - url: "https://disk.yandex.ru/d/def"
    enabled: false
```

`scan` with no URL does the same across every configured source:

```bash
python app.py scan --local-review
```

### The steps behind it

`run` is exactly these commands in order — use them individually when you want
one stage at a time:

```bash
# 1. Read Yandex, generate/refresh per-folder review.xlsx with previews
python app.py scan "https://disk.yandex.ru/d/<id>" --local-review

# 2. …edit review.xlsx by hand: People, Place, LatLon, Tags, Map Link…

# 3. Teach the dictionaries from what you typed, refresh catalog.xlsx
python app.py learn

# 4. Scan again — the dictionary now fills blank fields it can
python app.py scan "https://disk.yandex.ru/d/<id>" --local-review

# 5. Regenerate the read-only dashboard covering the whole archive
python app.py dashboard
```

Steps 3–5 are safe to repeat as often as you like: an unchanged run adds
nothing and overwrites nothing.

### Every command

```bash
# --- everything at once ----------------------------------------------------
python app.py run                                      # scan all sources → learn → dashboard
python app.py run --skip-learn                         # scan and dashboard only
python app.py run --skip-dashboard                     # scan and learn only
python app.py run --output-dir ./out                   # override the configured directory

# --- inspect ---------------------------------------------------------------
python app.py scan "<yandex-url>" --dry-run            # read-only report, writes nothing
python app.py --verbose scan "<yandex-url>" --dry-run  # + per-entry detail

# --- review workbooks ------------------------------------------------------
python app.py scan "<yandex-url>" --local-review
python app.py scan --local-review                      # every configured source
python app.py scan "<yandex-url>" --local-review --output-dir ./review-output
python app.py scan "<yandex-url>"                      # plain scan: needs Google Drive (not implemented)

# --- dictionaries ----------------------------------------------------------
python app.py learn                                    # reads ./review-output
python app.py learn --source ./review-output
python app.py learn --dry-run                          # propose without writing
python app.py --verbose learn

# --- dashboard -------------------------------------------------------------
python app.py dashboard                                # -> review-output/review-all.html
python app.py dashboard --source ./review-output --output ./review-all.html

# --- portable state / new machine -----------------------------------------
python app.py bootstrap                                # rebuild local state from _archive_state
python app.py bootstrap --machine-label "Tonya MacBook"
python app.py bootstrap --publish                      # publish a new state generation
python app.py bootstrap --archive ./review-output

# --- not implemented yet ---------------------------------------------------
python app.py build                                    # EXIF/IPTC/XMP into processed copies
python app.py publish                                  # upload to Google Photos
```

Unimplemented commands fail explicitly (exit code 3) instead of reporting false
success.

### Global options

Valid before or after the subcommand:

```bash
python app.py --config ./config.yaml scan "<yandex-url>" --local-review
python app.py --log-dir ./logs learn
python app.py --verbose dashboard
```

| Option | Meaning |
|---|---|
| `--config PATH` | Config file (default `./config.yaml`, then `config.example.yaml`) |
| `--verbose`, `-v` | Debug logging on the console and per-entry report detail |
| `--log-dir PATH` | Where the per-run DEBUG log goes (default `./logs`) |

### Exit codes

| Code | Meaning |
|---|---|
| `0` | Success |
| `1` | Unexpected error — see the log path printed on stderr |
| `2` | Configuration problem |
| `3` | Command not implemented yet |
| `4` | Storage/network failure (e.g. Yandex unreachable) |
| `5` | Portable state changed remotely during the run; nothing overwritten |

### A first run, end to end

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp config.example.yaml config.yaml

# add your Yandex shares under 'sources:' in config.yaml, then:
python app.py run
open review-output/<source-folder>/review.xlsx     # edit metadata by hand
python app.py run                                  # learn from the edits, refresh
open review-output/review-all.html
```

### On a new or rebuilt machine

`archive.sqlite` and `cache/` are disposable — everything durable lives in Git,
Yandex and the archive's `_archive_state/`, which `run` refreshes automatically
as its last stage. They are only safe to delete once a `run` has published that
state successfully.

```bash
git clone <repo> && cd family_photo
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp config.example.yaml config.yaml

python app.py bootstrap --machine-label "Home PC"     # restore dictionaries, ids, fingerprints
python app.py run
```

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
