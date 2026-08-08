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

Visible columns:

| Column | Purpose |
|---|---|
| Preview | Embedded thumbnail |
| Filename | Original filename |
| Source Description | Source text associated with the photo |
| Date | Reviewed date |
| Date Precision | `exact`, `month`, `season`, `year`, `unknown` |
| Place | Human-readable place |
| Latitude | GPS latitude |
| Longitude | GPS longitude |
| People | Semicolon-separated reviewed people |
| Tags | Semicolon-separated controlled tags |
| Event | Optional event name |
| Albums | Google Photos album names |
| Description | Final human-readable description |
| Status | Workflow status |
| Notes | Human notes |

Hidden technical columns:

```text
source_id
source_path
source_hash
description_hash
metadata_hash
processed_hash
google_photos_media_id
last_scan
```

Statuses:

```text
NEW
REVIEW
APPROVED
BUILT
PUBLISHED
SOURCE_CHANGED
SOURCE_MISSING
ERROR
SKIP
```

## Human Edits Are Authoritative

A repeated scan must never silently overwrite reviewed values.

Rules:

- new photo -> add a row;
- changed source photo -> mark changed;
- changed description -> preserve reviewed values and surface the new source text;
- missing source photo -> mark `SOURCE_MISSING`;
- unchanged source -> leave reviewed row untouched.

## Global `catalog.xlsx`

`catalog.xlsx` lives in the Google Drive archive root and grows from reviewed data.

Suggested sheets:

### People

Fields may include:

- stable person ID;
- canonical display name;
- aliases;
- relationship/context notes;
- default Google Photos album name.

Ambiguous aliases such as `mom` must require confirmation.

### Places

Fields may include:

- stable place ID;
- canonical place name;
- historical/display names;
- aliases;
- latitude;
- longitude;
- geographic precision;
- confirmation status.

### Tags

Use a controlled vocabulary rather than extracting every noun.

Examples:

```text
school
dacha
birthday
travel
New Year
```

## Learning Workflow

The catalog is populated **after** human correction.

Conceptual command:

```bash
python app.py learn
```

The feedback loop is:

```text
source descriptions
        ↓
automatic proposals
        ↓
review.xlsx
        ↓
human corrections
        ↓
APPROVED
        ↓
catalog.xlsx
        ↓
better proposals for the next folder
```

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

Planned commands:

```bash
python app.py scan "<yandex-folder-url>"
python app.py learn
python app.py build
python app.py publish
```

### `scan`

Current MVP:

- recursively inspect one supplied Yandex root;
- preserve nested relative paths;
- detect photo-containing folders;
- locate same-folder description files;
- associate description fragments with photos;
- use folder/path context for metadata proposals;
- create/update destination folders on Google Drive;
- create/update `review.xlsx`;
- embed previews;
- preserve human edits;
- detect new, changed, and missing source files.

### `learn`

Future:

- read approved review rows;
- update `catalog.xlsx`;
- flag ambiguous aliases.

### `build`

Future:

- obtain original source image;
- create processed copy;
- write reviewed EXIF/IPTC/XMP;
- upload processed copy to Google Drive.

### `publish`

Future:

- upload new/changed built files to Google Photos;
- create/reuse application-managed albums;
- add photos to albums;
- persist Google Photos IDs.

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
