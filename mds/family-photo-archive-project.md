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

**The archive must hold enough durable human and machine state to resume the
project on a clean computer. Local SQLite and cache are disposable — but only
once portable state has been successfully published.**

`python app.py run` refreshes portable state automatically as its final stage,
after scanning, learning and the dashboard have all succeeded. Portable state
never describes an archive that was not fully processed.

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
└── sources/<id>.json  per source root
```

Each source file records **two different things**, and recovery needs both:

* `source_items` — the files the provider actually reported;
* `items` — every logical review row, including `DESCRIBED_ABSENT` rows that
  have no file behind them, each with the full per-row bookkeeping
  (`source_hash`, `description_hash`, `suggestion_hash`, `status`,
  `was_absent`) a rescan compares against.

A folder of 12 photos described by a DOCX naming 24 references yields 12 of the
first and 24 of the second. Restoring only the files, or only the photo hash,
would make a recovered machine report the whole archive as changed.

A snapshot **merges into** the previous state. Drive ids, build fingerprints
and Photos ids cannot be recreated by a scan, so they are carried forward
rather than overwritten with scan-only data.

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

That file-level rule is the **safety gate**, not the answer. "Both changed"
does not mean the whole workbook is an opaque conflict.

### Semantic three-way merge

These workbooks have a known schema, stable row identity and known human-owned
fields, so `both changed` triggers a **field-level** merge:

| local vs base | remote vs base | result |
|---|---|---|
| changed | unchanged | take local |
| unchanged | changed | take remote |
| changed | changed, same value | take that value |
| changed | changed, differently | **true conflict** |

Two people editing different columns of the same row merge automatically and
nobody is asked. Only a genuine disagreement about one value reaches a person,
and there nothing is guessed.

Machine-owned columns — previews, `Source …`, every `Suggested …`, `Review
Reason` — take no part: they are regenerated from source and dictionary state
after the merge, so a difference in them means nothing.

**Human-owned review fields:** Date, Place, LatLon, People, Tags, Event,
Albums, Description, Status, Notes. `Status` is included because a reviewer
marking a row APPROVED is a decision worth protecting; the scan re-applies its
own lifecycle transitions afterwards.

**Human-owned catalog fields:** canonical value, confirmed and candidate
aliases, coordinates and candidate coordinates, map link, notes — keyed by
stable entity id.

### Semantic normalization

"Changed" and "same value" above are judged by *meaning*, not raw text, so a
merge never manufactures a conflict out of superficial formatting —
`"Тоня, Мама"` and `"Мама; Тоня"` are the same two people. One module,
`photoarchive.merge.semantic`, is the single place that decides what "the
same" means per field, reusing the domain's existing parsers
(`photoarchive.geo`, `split_list_field`) rather than a second copy of that
logic. The three-way algorithm itself stays a plain `semantic_equal(a, b)`
call; it never stores a normalised value.

| field(s) | normalised as |
|---|---|
| People, Tags, Albums | split on `,`/`;`, case-folded, order-free set |
| catalog confirmed/candidate aliases | split on `;`, order-free, case kept (matches the catalog importer) |
| LatLon | canonical `lat, lon` text when parseable; unparsable text falls back to whitespace-only |
| Date | whitespace only — `"1979"` and `"1979-05"` stay different on purpose, since collapsing them would fabricate a precision nobody stated |
| Place | whitespace only — dictionary aliases never decide two spellings mean the same place; that ownership stays with `Suggested Place` |
| Description, Notes | line-ending and outer-blank trivia only; prose is never reordered or reflowed |
| Status | exact value, once blank is normalised |

Normalisation exists only to answer "equal?". The **raw value** a person
typed is what is always carried forward — into the merged record, into the
conflict workbook's Base/This computer/Google Drive columns, into its
comment. Nothing normalised is ever displayed or written out.

### The semantic baseline

A hash detects change but cannot merge, so portable state also keeps the last
**agreed content** of each synced workbook in
`_archive_state/artifact_baselines/`: stable identity plus human-owned fields,
as compact JSON. No previews, no machine columns, no archived `.xlsx`.

A baseline is written only after a real transfer or an applied merge. Writing
one after a purely local generation would claim an agreement that never
happened.

### Resolving a conflict in Excel

When conflicts remain, **neither copy is touched**. A merge workbook is
generated in `_conflicts/<run-id>/`, clearly apart from the canonical files:

* **Info** — artifact, run, machine, commit, and the base/local/remote hashes,
  so an old merge workbook still explains itself months later;
* **Merge** — the reconstructed content with automatic merges already applied;
  only genuinely conflicting *cells* are shaded, each carrying a note with the
  base, local and Drive values. Shading one cell rather than the row keeps the
  eye on the actual decision;
* **Conflicts** — one row per conflicting field with a validated
  `Resolution Choice` dropdown: `LOCAL`, `DRIVE`, `BASE`, `CUSTOM`.

Opening or saving the workbook resolves nothing. Every conflict needs an
explicit choice — including "keep what I already had".

```bash
python app.py resolve-conflicts <path-to-merge.xlsx>
python app.py resolve-conflicts <path> --check
```

Applying re-checks the remote **before** writing anything. If Drive moved while
somebody was deciding, the run stops with `REMOTE CHANGED SINCE CONFLICT WAS
CREATED` and recomputes rather than overwriting newer work. Only after the
transfer succeeds do `last_common_hash`, the semantic baseline and `last_sync`
advance. The resolved workbook is renamed, not deleted — the reasoning is worth
keeping.

### First sync: no common baseline

`ArtifactSyncState.semantic_baseline == None` means exactly one thing: no
proven common ancestor. That is handled as four explicit cases — none of them
may guess an ancestor that never existed, and none may pick a side by
timestamp, machine or filename:

| situation | action | after a real transfer/adoption succeeds |
|---|---|---|
| local exists, remote absent | `ADOPT_LOCAL` — initial upload is safe | Drive file id, `last_common_hash`, semantic baseline and `last_sync` are recorded |
| local absent, remote exists | `ADOPT_REMOTE` — initial download is safe | local becomes the remote's content; baseline = remote's semantic model |
| both exist, semantically equal | `ADOPT_BASELINE` — no transfer needed | the agreed content becomes the first baseline; no human involved |
| both exist, genuinely differ | `FIRST_SYNC_CONFLICT` | nothing until a first-sync merge workbook is resolved |

"Semantically equal" is checked with the same comparator as an ordinary merge,
so two `.xlsx` files with different ZIP bytes but identical editable content
(`semantic_baselines_equal`) still adopt cleanly instead of becoming a
conflict. Without semantic content to compare, differing hashes are
conservatively treated as genuinely different — never guessed into agreement.

A first-sync merge reuses the ordinary field-level algorithm
(`merge_first_sync`) against an empty `base`: every record present on exactly
one side is a safe addition, and a record on both sides is compared field by
field exactly as above. The only conflict that can occur is the same stable id
present on both sides with differing content, reported as `ConflictKind.
FIRST_SYNC` rather than the ordinary `ADDED_BOTH`.

The resulting workbook reuses the same writer and formatting as an ordinary
conflict workbook, with three differences:

* the Info sheet leads with **"FIRST SYNC — NO COMMON BASELINE"** instead of
  the usual banner, and the base/last-sync rows read "no common baseline"
  rather than showing a hash that was never agreed on;
* the Base column and each conflict's comment show "— no common baseline —"
  instead of a base value, so it reads as *nothing to compare against* rather
  than a genuinely blank one;
* the `Resolution Choice` dropdown offers only `LOCAL`, `DRIVE` and `CUSTOM` —
  `BASE` is not in the list, and is rejected as unresolved even if typed in by
  hand, since there is no base value to apply.

No `last_common_hash`, semantic baseline or `last_sync` is written by deciding
or merging alone — only `record_sync`, after a real transfer or adoption
actually succeeds, ever advances them.

### Structural edits

Source scanning owns which review rows exist, so a row missing from somebody's
copy is **not** permission to delete an archive item; the row is kept. This
holds during a first sync too: a row absent from exactly one side is an
addition on the other, never a deletion, because first sync has no base from
which "deleted" could even be judged. In the catalog, deletion is a supported
edit: it is honoured when the other side left the entity alone, and raised as
a conflict when the other side changed it. Independent additions merge when
their stable ids do not collide; the same id created differently on both sides
is a conflict.

Generated artifacts use none of this ceremony — they are regenerated and
replaced.

### Build fingerprints

A processed photo's fingerprint depends on the source content hash, the
normalised final metadata a build writes, and the build mapping version — and
on nothing else. Timestamps, machines and run ids are excluded deliberately, so
a freshly bootstrapped machine can tell what is already built without
rebuilding anything to find out.

### Optimistic concurrency

A publish is skipped entirely when the deterministic content of the snapshot
already matches what is stored — provenance alone never bumps the generation.
(In practice `run` does bump it, because `.xlsx` files are ZIP archives whose
bytes change on every rewrite even when their content does not.)

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

**Not implemented yet.** `photoarchive/metadata/exiftool.py` is a skeleton —
every method raises `NotImplementedError`. What follows is the frozen
*contract* the eventual build step must implement against; it is not a
description of working code. The pure, tested parts of the date half of that
contract already exist in `photoarchive/dates.py` (see below) — everything
else (ExifTool invocation, GPS/IPTC/XMP writing beyond the date fields,
re-read verification) is still to be built.

A future build step will create processed copies and write metadata through
**ExifTool** (or another standards-correct writer — never hand-rolled EXIF
placement). Source photos are never modified; only a processed copy in the
local cache is written to.

Target metadata includes:

### EXIF

- `EXIF:DateTimeOriginal` — the capture-date compatibility timestamp, always
  addressed through its EXIF group (see "Archival date vs compatibility
  timestamp" below — writing it to the wrong TIFF IFD is why a naive first
  attempt failed a real Google Photos test);
- `EXIF:DateTimeDigitized` — only when the actual scan/digitisation date is
  known; otherwise left unset (never copied from `DateTimeOriginal`);
- GPS coordinates.

### IPTC/XMP

- description/caption;
- place;
- people;
- controlled tags;
- event;
- the archival date and its precision, in a small custom XMP namespace (see
  below) — not just "date precision" as a bare fact, but the actual value and
  a flag saying whether the EXIF timestamp was invented;
- internal archive ID.

### Re-read verification

The build is not complete after writing. For every built file: write, then
re-read with ExifTool, then validate — at minimum `EXIF:DateTimeOriginal`
**and its tag group** (a tag found by name alone, in the wrong IFD, is not a
pass — that is exactly the bug the manual Google Photos test caught), the XMP
archival date, its precision property, and the synthetic flag.

## Archival Dates and the Google Photos Compatibility Timestamp

Two different things, never conflated:

**Archival date** — the truth as actually known from the family archive: a
value plus its precision. `1979` never becomes `1979-01-01`; an unknown month,
day or time is never invented. This is what `review.xlsx`'s final `Date`
column already holds, as partial-ISO text (`1979`, `1979-05`, `1979-05-17`),
now extended to also accept a known time (`1979-05-17 14:30`,
`1979-05-17 14:30:45`).

**Compatibility timestamp** — a full timestamp derived *only* for a consumer
that requires one (Google Photos' `EXIF:DateTimeOriginal` above all). A
display aid, never a claim about what is actually known, and it never
overwrites or replaces the archival date.

Implemented, pure and tested in `photoarchive/dates.py`
(`tests/test_dates.py`): `ArchiveDate.parse`/`.text()`,
`DatePrecision` (`DATETIME`, `DAY`, `MONTH`, `SEASON`, `YEAR`, `UNKNOWN`),
`CompatibilityTimestamp`, and `derive_compatibility_timestamp`.

**Supported precisions — named explicitly, never by enum declaration
order:** `DATETIME`, `DAY`, `MONTH`, `YEAR` have a defined
compatibility-timestamp policy. **Unsupported:** `SEASON` and `UNKNOWN` — see
"Unsupported precision at build time" below for what that means in practice
(not a fatal build error).

### Precision and parsing

```text
YEAR      1979
MONTH     1979-05
DAY       1979-05-17
DATETIME  1979-05-17 14:30        (seconds optional; canonicalised to :00)
DATETIME  1979-05-17 14:30:45
```

Each accepted shape maps to exactly one precision. Malformed or
locale-ambiguous input (`05/06/79`, `1979/05/17`, an unpadded month, an
invalid calendar date) is rejected with `InvalidArchiveDate`, never guessed
at. `SEASON` (e.g. "лето 1980") remains a recognised precision but has no
parser or compatibility-timestamp policy defined in this pass — an open
question, not something to silently handle.

### Compatibility-timestamp policy

| precision | `EXIF:DateTimeOriginal` | synthetic |
|---|---|---|
| DATETIME | the known time, verbatim | `False` |
| DAY | known date, **noon** (`12:00:00`) | `True` |
| MONTH | known year/month, **day 15**, noon | `True` |
| YEAR | known year, **July 1**, noon | `True` |

Noon rather than midnight for DAY: a classic EXIF `DateTimeOriginal` string
carries no timezone, so noon keeps the instant far from either local
midnight — the case most likely to round the photo onto the wrong calendar
day. Day 15 and July 1 are **fixed conventions**, not computed
month-length-aware midpoints — deliberately, so the mapping never changes
shape by month or leap year. July 1 rather than January 1 specifically avoids
systematically pushing every year-only photo to the start of the year.

`SEASON`/`UNKNOWN` have no policy; `derive_compatibility_timestamp` raises
rather than inventing one.

### Unsupported precision at build time is not fatal

`derive_compatibility_timestamp` raising for `SEASON`/`UNKNOWN` is a
statement about that one pure function, not about whether a photo build may
succeed. The future build orchestrator must use
`try_derive_compatibility_timestamp` (returns `None` instead of raising) and
treat a `None` result as **non-fatal**:

- archival metadata (`authoritative_xmp_fields` — the archival date text and
  its precision) is still written, for every precision, `SEASON`/`UNKNOWN`
  included;
- `EXIF:DateTimeOriginal` is simply left absent — never invented from a
  season or an unknown date;
- the photo build may still succeed;
- a warning/diagnostic is recorded — always for `SEASON` (a real season was
  stated; only the timestamp policy is missing), only when useful for
  `UNKNOWN` (there may be nothing more to say).

### Synthetic provenance (XMP)

A synthetic EXIF timestamp is indistinguishable from a real one to a naive
reader, so the archival value and precision are also written to XMP, in a
small custom namespace kept apart from any standard field whose semantics
might differ. These are **authoritative and required**, for every precision
including `SEASON`/`UNKNOWN` (`authoritative_xmp_fields`):

- `XMP-archive:ArchiveDate` — the canonical archival text;
- `XMP-archive:ArchiveDatePrecision` — the precision, as its lower-case text;
- `XMP-archive:CompatibilityDateSynthetic` — `"True"`/`"False"`, present only
  when a compatibility timestamp was actually derived (omitted, not written
  as a placeholder, for `SEASON`/`UNKNOWN`).

**Standard-field mirror — optional, experimental, not a production
requirement.** As a best-effort convenience for generic XMP-aware tools that
do not know this custom namespace, `experimental_standard_date_created`
proposes the same value for the standard `XMP-photoshop:DateCreated` (the XMP
`Date` type explicitly permits year/year-month/year-month-day truncation,
unlike EXIF's fixed string). This mirror is **not authoritative** and **must
not be treated as required for correctness**: a consumer that pads or
reinterprets a truncated value must not be trusted over the `XMP-archive:*`
properties. Whether real tools actually honour a truncated
`photoshop:DateCreated` — for the `YEAR`/`MONTH` truncated forms especially —
rather than silently discarding or padding it, has not been verified against
a real file, an eventual real ExifTool round trip, or Google Photos
ingestion — **open question for the eventual acceptance test, not resolved by
this pass**. A full-precision `DAY`/`DATETIME` value is an ordinary
unambiguous ISO date that may prove safe to mirror once that verification
happens, but the authoritative `XMP-archive:*` properties are written
regardless of what is decided about this mirror.

### Custom XMP namespace: future ExifTool round-trip contract

Not implemented yet — no ExifTool config exists, and none is written by this
pass — but the acceptance contract is explicit so `XMP-archive:*` never stays
merely a conceptual label:

- **namespace** — a stable, versioned URI (`XMP_ARCHIVE_NAMESPACE_URI`) and
  prefix (`XMP_ARCHIVE_NAMESPACE_PREFIX`); the URI does not need to resolve to
  anything, only to uniquely and stably identify the schema;
- **property names** — exactly `ArchiveDate`, `ArchiveDatePrecision`,
  `CompatibilityDateSynthetic`, fixed strings;
- **types/serialization** — all three are XMP simple (string) properties;
  `CompatibilityDateSynthetic` is exactly the literal `"True"`/`"False"`, and
  is *absent*, not a placeholder, when there is no compatibility timestamp;
- **deterministic writing** — derived solely from the `ArchiveDate`/
  `CompatibilityTimestamp` being built; no runtime timestamp, machine id or
  run id;
- **re-read validation (acceptance test)** — once ExifTool integration
  exists: write with ExifTool, re-read with ExifTool, every property
  reproduces exactly, for `YEAR`, `MONTH`, `DAY`, `DATETIME`, `SEASON` and
  `UNKNOWN` (where applicable to each);
- **ExifTool config dependency** — ExifTool cannot write an arbitrary
  `-XMP-archive:ArchiveDate=...` tag without a user-defined tag config
  (`-config` / `.ExifTool_config`) declaring this namespace first. That
  config does not exist yet; writing it is future build-implementation work
  (tracked in `todo.md`), not something this pass creates.

### `DateTimeDigitized` vs `DateTimeOriginal`

For old scanned photographs, "when the photograph was taken" and "when the
digital file was created" are different facts. `DateTimeOriginal` carries the
historical-capture compatibility timestamp; `DateTimeDigitized` is written
only when the actual scan/digitisation date is genuinely known, and is never
auto-populated from `DateTimeOriginal` merely to satisfy a consumer. The
manual Google Photos test that populated both fields was a compatibility
experiment, not production policy — if later real-world testing shows Google
Photos needs a second populated field to ingest reliably, that must be
documented as an explicit, named compatibility exception, not silently folded
into the default policy.

### Timezone

Never invented. A classic EXIF `DateTimeOriginal` string carries no
timezone; if an offset field is written later, it must hold a genuinely known
offset. The noon convention exists specifically to blunt timezone-boundary
surprises without pretending to know a timezone.

### Existing metadata precedence

Build operates on managed copies only; Yandex originals are never modified.
Precedence for dates, to be enforced once the build step is implemented:

- do not overwrite a known original camera `DateTimeOriginal` with a weaker
  inferred archive date without an explicit precedence rule;
- for digitised historical scans where the archive's own metadata is
  authoritative, write per the policy above;
- if a source file already carries conflicting date metadata, surface/report
  it rather than silently discarding evidence.

### Build fingerprint

`photoarchive.dates.DATE_COMPATIBILITY_POLICY_VERSION` (currently `1`) is
folded into `build_fingerprint` (`photoarchive/portable/fingerprint.py`)
alongside the archival date value, precision and `BUILD_VERSION`. Changing
the policy — e.g. moving the YEAR midpoint off July 1 — must therefore force
a rebuild of every affected file on its own, without a source or metadata
change. No runtime timestamp, machine id or run id is ever part of it.

### Google Photos is a consumer, not the source of truth

Even if Google Photos later lets someone edit a displayed date, that UI edit
is not authoritative. The authoritative source stays `review.xlsx`'s final
`Date` + its precision + portable state/build metadata. Do not design the
archive around irreversible Google Photos UI behaviour. A real Google Photos
ingestion test — using files from the actual production ExifTool writer, not
an ad-hoc script — remains an acceptance test once the build step exists, for
all four precisions, checking both the Info-panel date and timeline
placement.

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
