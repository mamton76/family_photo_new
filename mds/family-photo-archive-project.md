# Family Photo Archive Pipeline

## GitHub Repository

The project source code is stored in this GitHub repository:

```text
https://github.com/mamton76/family_photo_new
```

Repository full name:

```text
mamton76/family_photo_new
```

Default branch:

```text
master
```

The working project name may remain `family-photo-archive`; do not rename the GitHub repository solely to match the internal project name.

## Project Purpose

This is a Python 3.12+ pipeline for processing a family photo archive.

The source archive is stored on **Yandex Disk**. Source folders may contain photos, one text description file, and nested subfolders. Source folders are processed one at a time and are always treated as **read-only**.

The processed archive is stored under one configured **Google Drive root folder**. For every Yandex Disk source folder supplied to the application, the application creates or reuses a corresponding folder under the Google Drive root and recursively mirrors the source hierarchy.

The workflow is iterative: source folders, photos, and descriptions may change over time, while human edits made during review must be preserved.

## High-Level Workflow

1. Supply one Yandex Disk folder URL.
2. Scan it recursively.
3. Detect photos and per-folder description files.
4. Propose metadata from:
   - folder path and names;
   - description text;
   - photo filename;
   - existing image metadata when useful;
   - archive-wide catalog knowledge when available.
5. Create or update `review.xlsx` files with embedded previews.
6. Human reviews and edits metadata in Excel.
7. Approved rows update the global `catalog.xlsx`.
8. Approved metadata is written to processed photo copies using EXIF/IPTC/XMP.
9. Processed copies are stored on Google Drive.
10. New or changed processed photos are published to Google Photos.
11. Photos are added to Google Photos albums according to reviewed metadata.

The initial MVP implements **scan only**.

## Storage Model

### Source: Yandex Disk

Initial implementation should support a public Yandex Disk folder URL. The architecture should allow a future authenticated adapter.

Example:

```text
https://disk.yandex.ru/d/<public-folder-id>
```

A supplied source root can contain arbitrary nesting:

```text
Family Archive/
├── 1988/
│   ├── Dacha/
│   │   ├── 001.jpg
│   │   ├── 002.jpg
│   │   └── description.docx
│   └── School/
│       ├── 010.jpg
│       └── some.docx
└── 1990/
    └── Valaam/
        ├── 020.jpg
        ├── 021.jpg
        └── text.docx
```

### Destination: Google Drive

One configured Google Drive folder is the processed archive root.

Current test root folder ID:

```text
1NhmSheM1A6XylV1HAwhq4oqRk5lvZpLY
```

The relative hierarchy from Yandex Disk must be preserved:

```text
Google Drive Root/
├── catalog.xlsx
└── Family Archive/
    ├── 1988/
    │   ├── Dacha/
    │   │   ├── review.xlsx
    │   │   ├── 001.jpg
    │   │   └── 002.jpg
    │   └── School/
    │       ├── review.xlsx
    │       └── 010.jpg
    └── 1990/
        └── Valaam/
            ├── review.xlsx
            ├── 020.jpg
            └── 021.jpg
```

Create `review.xlsx` only in folders that directly contain photos.

## Description Scope

By default, a description file applies only to photos directly contained in the same folder:

```yaml
description_scope: current_folder
```

The full relative folder path is also metadata context.

Example:

```text
Family Archive / 1990 / Valaam
```

plus:

```text
020.jpg — Tonya and Anya
```

may produce proposals such as year `1990`, place `Valaam`, people `Tonya; Anya`.

Values inferred from path context must be marked as inferred and require review.

## `review.xlsx`

One workbook per folder that directly contains photos, generated locally by
`scan --local-review`.

Every metadata field appears twice: a machine-owned `Suggested …` column and
the user-owned final column it seeds.

Visible columns, in order:

```text
Preview
Filename / Reference
Source Description
Section Context
Source Notes
Suggested Date
Date
Suggested Place
Place
Suggested LatLon
LatLon
Map Link
Suggested People
People
Suggested Tags
Tags
Event
Albums
Description
Status
Review Reason
Notes
```

There are no precision columns. Bookkeeping (hashes, previous status, row
identity) lives in `archive.sqlite`, not in hidden workbook columns.

### Suggested vs final semantics

- On **first creation**, each final field is seeded from its suggestion.
- On **every later scan**, suggestions are recomputed freely, and a **non-empty**
  final value is never overwritten.
- A **blank** final field may be filled from the current suggestion. This is how
  dictionary knowledge propagates once `learn` has taught it.

```text
final non-empty                      -> preserve exactly
final empty AND suggestion non-empty -> copy suggestion into final
```

`Map Link` is the explicit exception: a parsable Google Maps URL pasted by a
reviewer may update the final `LatLon`, because pasting it is an instruction.
An unparsable link changes nothing and says so in `Review Reason`.

### Source columns

- **Section Context** — the inherited `Далее …` heading, kept apart from the
  photo's own description and never merged into it.
- **Source Notes** — verbatim notes lifted out of the description, such as
  `нет фото`, which means the paper original has been lost, *not* that the
  digital file is missing.
- **Review Reason** — why a row needs another look: `Description changed`,
  `Description changed after approval`, `Source photo changed`,
  `Previously absent photo found`, or a `Map Link` outcome.

### Place and LatLon are one linked concept

`LatLon` is **not** an independent dictionary concept. Coordinates are an
attribute of a canonical Place, so learning a coordinate always enriches a
Place entity, and any row naming a known Place can receive that Place's
coordinates.

One combined text field carries them everywhere:

```text
55.751244, 37.618423
```

Latitude first, decimal degrees, `.` decimal separator, `, ` delimiter, stored
as text. Both `Suggested LatLon` and `LatLon` are clickable Google Maps
hyperlinks; they are never split back into separate latitude/longitude columns.

There are **two routes** by which a row gets coordinates:

```text
A.  source text -> confirmed Place -> Suggested Place -> Suggested LatLon
B.  existing final Place -> confirmed Place -> Suggested LatLon
```

**Route B matters most.** A folder with no DOCX has no source text to match, so
the only thing identifying the location is the `Place` a reviewer typed. That
value is resolved against the dictionary — exact canonical, then confirmed
alias, then the same compared normalized — and the resolved Place's confirmed
coordinates become `Suggested LatLon`. The blank-fill rule then decides whether
they reach `LatLon`.

### What `Suggested Place` means

`Suggested Place` is **the system's current canonical interpretation of the
place** — not merely "what the source-text parser extracted". It may be filled
either by matching the description or by resolving the `Place` a reviewer
already typed.

That is deliberate: seeing the canonical name beside their own wording is how a
reviewer can tell at a glance that their spelling is being recognised, and
through which confirmed alias.

```text
Suggested Place: Дома на Днепропетровской    <- canonical interpretation
Place:           Дом на Днепропетровской     <- exactly what was typed
```

The reviewer's own `Place` spelling is **never** rewritten to the canonical
form. `Suggested Place` is machine-owned; `Place` is user-owned once non-empty.

A **confirmed** alias may therefore produce a canonical `Suggested Place` with
no DOCX present at all, and that canonical Place then supplies
`Suggested LatLon`. **Candidate** aliases produce neither. An ambiguous value
picks no canonical Place silently.

Candidate aliases and candidate coordinates never supply values this way. If a
place value resolves to two different confirmed Places, both `Suggested Place`
and `Suggested LatLon` stay empty and the ambiguity is reported.

### Map Link, end to end

```text
one row:  Place = Днепропетровская, Москва
          Map Link = <Google Maps URL>
    -> parsed into that row's final LatLon
    -> learn stores it on the Place
    -> rescan gives every other row using that Place the same coordinates
```

So a single manually placed map point can locate an entire group of photos.

### Statuses

```text
NEW
REVIEW
APPROVED
BUILT
PUBLISHED
SOURCE_CHANGED
SOURCE_MISSING
DESCRIBED_ABSENT
ERROR
SKIP
```

`DESCRIBED_ABSENT` means the folder's DOCX describes a photo that is not in
this folder. The row exists with no preview and keeps its description, so the
photo can be looked for later. It is distinct from `SOURCE_MISSING`, which
means a photo the pipeline saw before has since disappeared.

### Row identity

A row is identified by source root + relative folder + reference stem. The
stem alone is not an identity: `folder A/001.jpg` and `folder B/001.jpg` are
different photos. Within one folder, `20200512_150442` and
`20200512_150442.jpg` are the same row, so a `DESCRIBED_ABSENT` row is reused
when its photo appears rather than duplicated.

## Description Documents

Only `.docx` is parsed. Exactly one per folder is required: zero means no
description, and several is a conflict where none is chosen automatically.
`.rtf`, `.txt`, `.doc` and `.pdf` are reported in diagnostics but never parsed.
This rule is not configurable.

A new description entry starts at a paragraph beginning with a photo
reference; following paragraphs belong to it.

## Dictionaries and `catalog.xlsx`

`People`, `Places` and `Tags` live in `archive.sqlite` and are exported to an
**editable** `catalog.xlsx` with one sheet each.

### Confirmed vs candidate

- **CONFIRMED** knowledge may drive suggestions.
- **CANDIDATE** knowledge is a hint only, shaded amber in the workbook, and
  never becomes a suggestion until a human promotes it.

Every alias and coordinate carries **evidence**: where it came from, which row,
which description, and why. Evidence is append-only and survives promotion, so
"why does the system think this?" is always answerable. Each catalog row shows
an `evidence_count`.

### Learning

**Suggestions never teach the dictionary. Human-entered final values do.**
Only the user-owned `People`, `Place`, `LatLon` and `Tags` columns are read.

`APPROVED` is *not* required — it is a publication state, and a name typed on a
row still in `REVIEW` is just as much a stated fact. Only `ERROR` and `SKIP`
rows are ignored. This lets the dictionaries bootstrap while review is still in
progress.

Ambiguous mappings become candidates with evidence rather than confirmed
aliases, and context-dependent kinship words (`мама`, `папа`, …) are never
promoted to universal aliases. Confirmed place coordinates are never
overwritten: a materially different proposal becomes a conflict for a human.

If several distinct confirmed places match one description, `Suggested Place`
and `Suggested LatLon` are both left empty rather than guessing.

### `catalog.xlsx` is bidirectional

The workbook is read back before every `learn` and used by every `scan`:

```text
load SQLite
    -> import and validate catalog.xlsx edits
    -> sync valid edits into SQLite
    -> learn or scan
    -> write refreshed catalog.xlsx
```

Stable `person_id` / `place_id` / `tag_id` columns mean renaming a canonical
value edits that entity instead of creating a duplicate.

**Merging duplicates.** Two rows that turn out to name the same thing are
merged by adding the duplicate's canonical value to the survivor's
`confirmed_aliases` and deleting the duplicate row. On import that is treated
as an intentional merge: aliases and evidence are re-pointed rather than
dropped, coordinates are adopted if the survivor had none, conflicting
coordinates become a conflict rather than a silent overwrite, and the duplicate
is removed.

**A merge stays merged.** Learning resolves a value through confirmed aliases
before creating anything, so a review row still carrying the old spelling
resolves to the surviving entity instead of recreating the duplicate. The
resolution order is: stable id, exact canonical, confirmed alias, normalized
match, and only then a new canonical entity. Candidates never resolve and never
block creation. Moving an alias from
`candidate_aliases` to `confirmed_aliases` promotes it; typing coordinates into
`latlon` confirms them. Invalid edits are reported and skipped, and never
destroy the last known-good confirmed value.

## `review-all.html` — the archive dashboard

A generated, **read-only** page covering the whole archive:
`review-output/review-all.html`. It is deliberately not a workbook — an
aggregate `.xlsx` would look exactly like the file people *do* edit and would
eventually be edited by mistake. HTML makes the ownership rule structural.

```text
review.xlsx      editable photo review metadata
catalog.xlsx     editable dictionaries
review-all.html  generated read-only dashboard
```

It writes nothing to SQLite, the workbooks or the catalog, and contains no
controls implying anything can be saved.

Contents: a `Summary` table (per folder — rows, photos, described-absent,
status counts, filled Date/People/Place/LatLon/Tags, needs-review, totals) and a
`Review` section grouping every row by source root → folder → reference, with
collapsible groups, search, status/folder filters and quick filters for missing
fields. Coordinates link to Google Maps; suggestions render secondary to final
values.

### Preview assets vs destination links

Two separate concepts, and the separation matters:

* **Preview** — an image this pipeline generated from a locally cached photo,
  embedded in the page. A static page must never use a provider thumbnail URL
  as `<img src>`: those expire and need live authorization, so the dashboard
  would rot into broken images.
* **Destinations** — where a reader can go: Yandex source, Google Drive
  archive copy, Google Photos published item. These are navigation links.

Destinations accumulate rather than replace each other. Once published, Google
Photos becomes the *primary* button because that is where browsing, albums and
search live, but the Drive and Yandex links remain visible as archive and
provenance. Links a photo does not have simply are not rendered — no dead
buttons.

Drive and Photos fields are modelled now and populate themselves when those
phases exist, without touching the review workbooks.

## Portable state and clean-machine recovery

**The Google Drive archive must hold enough durable human and machine state to
resume the project on a clean computer. Local SQLite and cache are disposable.**

```text
GitHub          code
Yandex Disk     source originals and descriptions
Drive archive   workbooks, dashboard, processed photos, portable state
local machine   disposable cache and index
```

Four kinds of state, with different rules:

| | |
|---|---|
| `review.xlsx`, `catalog.xlsx` | **human truth** — three-way synchronised |
| `_archive_state/*.json` | **portable machine truth** — identities, hashes, Drive ids, fingerprints, sync baselines, evidence |
| processed photos | built artifacts — regenerated when their fingerprint changes |
| `review-all.html` | generated view |
| `archive.sqlite`, `cache/` | local acceleration — rebuildable, never the sync primitive |

Layout under the archive root:

```text
_archive_state/
├── manifest.json      generation, machines, archive-wide artifact baselines
├── catalog.json       dictionaries with ids, aliases, coordinates, evidence
└── sources/<id>.json  per source root: items, hashes, Drive ids, fingerprints
```

No secrets, tokens, local paths, logs, caches or the SQLite file itself are
ever stored there.

### Provenance beside every hash

A bare `"last_synced_hash": "8d3f17…"` tells a machine everything and a person
nothing. Each recorded hash therefore carries who, when, which run and which
code version:

```json
"last_sync": {
  "content_hash": "sha256:8d3f17…",
  "at": "2026-08-09T10:42:17Z",
  "machine_id": "7c26e8…", "machine_label": "Tonya MacBook",
  "run_id": "run-…", "app_commit": "df0fb26"
}
```

The commit is suffixed `-dirty` when the working tree had uncommitted changes,
and is `unknown` when Git metadata is unavailable — never silently presented as
a clean commit. Machine identity is an opaque local UUID plus a label a person
chose; it is provenance only, never a correctness key, so a reinstall that
changes the id breaks nothing.

### Three-way workbook synchronisation

Because people edit workbooks on more than one machine, each editable artifact
records the content hash both sides last agreed on:

| local vs baseline | remote vs baseline | action |
|---|---|---|
| same | same | nothing |
| changed | same | upload |
| same | changed | download |
| **changed** | **changed** | **conflict — overwrite neither** |

There is no automatic `.xlsx` merge: losing an afternoon of review typing is
worse than asking which copy to keep. Generated artifacts use no such
ceremony — they are regenerated and replaced.

### Build fingerprints

A processed photo's fingerprint depends on the source content hash, the
normalised final metadata a build writes, and the build mapping version — and
on nothing else. Timestamps, machines and run ids are excluded deliberately, so
a freshly bootstrapped machine can tell what is already built without
rebuilding anything to find out.

### Optimistic concurrency

`manifest.json` carries a `state_generation`, written last so a manifest at
generation N implies that generation is complete. A run records the generation
it started from and re-checks before publishing; if another machine has moved
it, the run aborts with `REMOTE STATE CHANGED DURING RUN` rather than
discarding that work. Portable JSON also carries an explicit `schema_version`,
and state from a newer version is refused rather than half-understood.

## The Iterative Loop

```text
review.xlsx
    -> learn from human-entered final values
    -> SQLite dictionaries  <->  editable catalog.xlsx
    -> rescan
    -> dictionary-backed Suggested fields
    -> fill only still-empty final fields
    -> more manual edits
    -> learn again
```

Safely repeatable: repeated unchanged runs add no entities, aliases, candidates
or evidence, and never overwrite a non-empty final value.

## Human Edits Are Authoritative

A repeated scan must never silently overwrite reviewed values.

Rules:

- new photo -> add a row;
- changed source photo -> mark changed;
- changed description -> preserve reviewed values and surface the new source text;
- missing source photo -> mark `SOURCE_MISSING`;
- unchanged source -> leave reviewed row untouched.

## Metadata Output

A future build step will create processed copies and write metadata through **ExifTool**.

Source photos are never modified.

Target metadata includes:

### EXIF

- capture date/time;
- GPS coordinates.

### IPTC/XMP

- description/caption;
- place;
- people;
- controlled tags;
- event;
- date precision;
- internal archive ID.

## Approximate Dates

Historic photos may have incomplete dates.

Keep precision separately:

```text
exact
month
season
year
unknown
```

A future build step may use configurable fallback dates for EXIF, but the original precision must always remain stored.

## Local Cache

Cloud storage is the source of truth. Local files are temporary:

```yaml
cache:
  directory: "./cache"
  cleanup: true
```

## Change Tracking

Repeated runs must detect:

- new files;
- changed files;
- missing files;
- changed description files;
- unchanged files;
- processed versions;
- published versions.

Use SQLite for durable local processing state:

```text
archive.sqlite
```

Do not use filenames alone as immutable identity.

## CLI

```bash
python app.py scan "<yandex-public-url>" --dry-run       # inspect, writes nothing
python app.py scan "<yandex-public-url>" --local-review   # generate review.xlsx
python app.py learn                                       # learn from review-output
python app.py learn --dry-run                             # propose without writing
python app.py build                                       # not implemented
python app.py publish                                     # not implemented
```

`learn` reads `./review-output` by default; `--source` points it elsewhere.
Every run writes a full DEBUG transcript to `./logs/run-<id>.log`.

## Proposed Python Stack

Target: Python 3.12+

Suggested dependencies:

```text
httpx
PyYAML
openpyxl
Pillow
google-api-python-client
google-auth
google-auth-oauthlib
pytest
```

Use stdlib `sqlite3`.

ExifTool is an external executable.

## Proposed Repository Structure

```text
family-photo-archive/
├── README.md
├── PROJECT.md
├── app.py
├── config.example.yaml
├── requirements.txt
├── .gitignore
│
├── photoarchive/
│   ├── __init__.py
│   ├── config.py
│   ├── models.py
│   ├── state.py
│   │
│   ├── storage/
│   │   ├── __init__.py
│   │   ├── base.py
│   │   ├── yandex.py
│   │   └── google_drive.py
│   │
│   ├── scanning/
│   │   ├── __init__.py
│   │   ├── scanner.py
│   │   └── matcher.py
│   │
│   ├── parsing/
│   │   ├── __init__.py
│   │   ├── descriptions.py
│   │   └── metadata.py
│   │
│   ├── catalog/
│   │   ├── __init__.py
│   │   └── service.py
│   │
│   ├── review/
│   │   ├── __init__.py
│   │   └── excel.py
│   │
│   ├── metadata/
│   │   ├── __init__.py
│   │   └── exiftool.py
│   │
│   └── publishing/
│       ├── __init__.py
│       └── google_photos.py
│
├── tests/
│   ├── __init__.py
│   ├── test_scanner.py
│   ├── test_matcher.py
│   └── test_review_excel.py
│
└── cache/
    └── .gitkeep
```

## Configuration

```yaml
google_drive:
  root_folder_id: "1NhmSheM1A6XylV1HAwhq4oqRk5lvZpLY"

cache:
  directory: "./cache"
  cleanup: true

review:
  filename: "review.xlsx"
  preview_width_px: 180

catalog:
  filename: "catalog.xlsx"

descriptions:
  scope: "current_folder"
```

The Yandex source URL is supplied at runtime.

## Current Test Resources

Yandex source:

```text
https://disk.yandex.ru/d/cFwfbSEQ7IB37g
```

Google Drive root:

```text
https://drive.google.com/drive/folders/1NhmSheM1A6XylV1HAwhq4oqRk5lvZpLY
```

These are temporary public test resources. The architecture must not depend on them remaining public.

## MVP Definition of Done

This command:

```bash
python app.py scan "https://disk.yandex.ru/d/cFwfbSEQ7IB37g"
```

must eventually:

1. read the supplied Yandex folder recursively;
2. detect nested folders;
3. detect images and description files;
4. mirror the source hierarchy under the configured Google Drive root;
5. create one `review.xlsx` in every folder that directly contains photos;
6. embed usable thumbnails;
7. include source description and initial metadata proposals;
8. preserve manual edits when run again;
9. identify newly added, changed, and missing source files;
10. perform no destructive operation against source files.

EXIF writing and Google Photos publishing are outside the first MVP.
