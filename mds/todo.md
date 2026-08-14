# Family Photo Archive — TODO

Operational checklist. Architecture lives in
[`family-photo-archive-project.md`](family-photo-archive-project.md) —
don't restate it here.

## NEXT

- [x] Commit the previous stable milestone
- [x] Build generated `review-all.html` (`Summary` + `Review`)
- [x] Portable archive state + clean-machine recovery, wired into `run`
- [ ] Manually inspect `review.xlsx` + `catalog.xlsx` + `review-all.html` together
- [ ] Fix UX issues found
- [ ] Validate People / Tags iterative propagation and catalog curation
- [ ] Improve candidate / evidence / rejection UX
- [ ] Harden source-change lifecycle
- [ ] Add Google Drive synchronization
- [ ] Implement metadata `build`
- [ ] Implement Google Photos publishing

The aggregate is HTML, not a workbook, so there is no third Excel layout to
keep consistent — inspection now happens once, across all three artefacts.

## Current stable checkpoint

Verified against the repo, 2026-08-10. **504 tests passing.**

- [x] Public Yandex Disk scan, pagination, nested traversal, `--dry-run`
- [x] DOCX grouping, section context, source notes, `DESCRIBED_ABSENT`
- [x] Per-folder `review.xlsx` with previews and Suggested/Final fields
- [x] Iterative `review → learn → catalog → rescan`
- [x] Bidirectional editable `catalog.xlsx`
- [x] People / Places / Tags dictionaries with aliases, candidates, evidence
- [x] Durable entity merges; merged duplicates never recreated
- [x] Place ↔ LatLon learning and propagation, including no-DOCX route B
- [x] Blank finals may be enriched; non-empty finals stay user-owned
- [x] Repeat learn/scan idempotency verified on real data
- [x] Per-run DEBUG logs with stable run ids

---

## 1. Commit the stable milestone

- [ ] Review `git diff --staged`
- [ ] Confirm no generated data staged (`review-output/`, `logs/`, backups, `cache/`, `archive.sqlite`, `.idea/`)
- [ ] Full test suite + compile check
- [ ] Commit

## 2. Manual Excel UX inspection

Enough Excel behaviour exists that human judgement is required, not just tests.

- [ ] `review.xlsx` — previews, widths, freeze panes, filters, LatLon hyperlinks,
      Suggested vs Final readability, Cyrillic rendering
- [ ] `catalog.xlsx` — candidate highlighting, evidence legibility, whether
      curation is discoverable
- [ ] Record layout fixes to apply before a third workbook exists

## 3. `review-all.html` (generated dashboard) — done, refinements open

`review-output/review-all.html` is generated and read-only; per-folder
`review.xlsx` and `catalog.xlsx` remain the editable surfaces.

- [x] `Summary` + grouped `Review`, collapsible, search and filters
- [x] Self-contained: inline CSS/JS, embedded thumbnails and medium previews
- [x] Previews generated locally — never a provider URL as `<img src>`
- [x] Destination links accumulate; Photos > Drive > Yandex priority modelled
- [x] Coordinates link to Google Maps; suggestions secondary to finals
- [ ] Switch preview storage to `review-all_files/` when the archive outgrows one
      file (currently 18.7 MB for 61 photos; the renderer is already decoupled)
- [ ] Per-photo Yandex links instead of folder-level, if the API exposes them
- [ ] Populate Drive / Photos links once those phases exist

### Source-description coverage — design frozen, not implemented

Semantics live in `family-photo-archive-project.md` → "Source-description
coverage". Ready to build; the open items below are deliberate defaults to
choose while implementing, not design questions.

- [ ] Portable-state read path in `dashboard/aggregate.py` — `collect()` reads
      only workbooks today, and both the folder record and `source_entry_exists`
      live in `_archive_state/`. No machine columns in `review.xlsx`, so the
      three-way merge stays untouched
- [ ] Per-folder summary + the four-value breakdown; no archive-wide count
- [ ] Where the breakdown lives: expanded folder details, a filter, or both
- [ ] Filter granularity — four values, two buckets, or both
- [ ] Wording/badges for `ABSENT` / `AMBIGUOUS` / `UNKNOWN`, and how visibly
      actionable `AMBIGUOUS` looks

## 4. People / Tags iterative propagation

Places are verified on real data; People and Tags are not. No NLP extraction —
manual review plus learning is the deliberate bootstrap.

- [ ] Canonical + confirmed-alias matching, candidates stay inert
- [ ] Merges, catalog edits, rescan propagation into blank finals
- [ ] Non-empty finals preserved; repeat runs idempotent
- [ ] Verify against the real workbooks (14 people, 1 tag learned so far)

## 5. Catalog candidate / evidence UX

Design **decided** — see the architecture doc's "Confirmed vs candidate" →
"Rejecting a proposal". Rejection ships together with the evidence view.

- [ ] `rejected_aliases` column in `catalog.xlsx`: export rejected aliases, and
      import the column as a positive decision (an emptied cell means nothing)
- [ ] Store: a status setter that moves an alias in **both** directions on
      explicit human instruction — `add_alias`'s "never degrade" rule guards
      machine passes, not people
- [ ] Precedence rule when one alias appears in two columns, plus a collision
      report in the import outcome
- [ ] `Evidence` sheet — generated, read-only, ignored by import; entity,
      candidate text, reason, run, source folder
- [ ] Verify a rejected alias stays rejected across `learn` → `scan` → export
      on real data (`add_alias` already refuses to revive it)
- [ ] Bulk candidate review flow
- [ ] Coordinate-candidate rejection — **deferred** until a real coordinate
      conflict has been seen; same mechanism is expected to apply
- [ ] Validate the coordinate-conflict UX against a real conflict

Entities are never rejected: an entity exists because final metadata says so.
Removing one means fixing `review.xlsx` or merging — otherwise the next `learn`
recreates it from the same typo.

## 6. Source-change lifecycle

Unit-tested; not yet exercised end to end against real sources.

- [ ] Image changed → REVIEW, preview refreshed, finals kept
- [ ] Description changed (and changed after APPROVED) → distinct reasons
- [ ] Photo removed → `SOURCE_MISSING`, row and metadata kept
- [ ] Description document deleted → the row keeps yesterday's source text and
      is marked **stale** (derived from `source_entry_exists` + non-empty source
      columns, nothing new persisted); diagnostic in `Review Reason`, coverage
      still `NO_ENTRY`
- [ ] Photo returns after `SOURCE_MISSING` → `REVIEW` with a new `Photo
      returned` reason, not `Source photo changed`; the previous status is not
      restored
- [ ] `DESCRIBED_ABSENT` photo appears → same row reused
- [ ] New photos added to an existing folder
- [ ] Nested folders (both real sources are flat — untested live)
- [ ] Source folder renamed
- [ ] Same filename in different folders (identity is scoped; confirm live)
- [ ] Multiple source roots in one run
- [ ] Photo moved between folders — covered by the reconciliation pass (§13)

## 7. Yandex source hardening

- [ ] Retry / rate-limit / transient network handling
- [ ] Download and cache efficiency
- [ ] Pagination against >200 items (never exercised live)
- [ ] Stronger remote identity if the public API allows better than URL-derived

## 8. Google Drive destination

Topology: `root/{catalog.xlsx, <source-root>/<mirrored folders>/{review.xlsx, photos}}`.
No workbooks in pure intermediate directories.

- [ ] OAuth flow, token cache
- [ ] One destination folder per source root; mirror nested paths
- [ ] Idempotent re-sync of an existing subtree
- [ ] Upload photo copies; never touch originals
- [ ] Sync `review.xlsx` without clobbering reviewer edits
- [ ] `catalog.xlsx` at the root; decide placement for `review-all.html`
- [x] Portable `_archive_state/` holding Drive ids, hashes, fingerprints, evidence
- [x] `run` publishes a complete snapshot; `record_listing` tracks source items
- [x] Three-way workbook sync rules, generation guard, `bootstrap` command
- [x] Semantic three-way merge + Excel conflict workbook + `resolve-conflicts`
- [x] Semantic normalization before conflict classification (People/Tags/Albums/
      LatLon/Date/Place/Status/prose policy in `merge/semantic.py`)
- [x] Explicit first-sync (no-baseline) decision table + first-sync merge
      workbook UX (`FIRST_SYNC_CONFLICT`, `merge_first_sync`, no BASE offered)
- [ ] Wire real Drive upload/download to the sync decisions (rules are done, transport is not)
- [ ] Connect the merge path to real Drive transfers and advance baselines after them
- [ ] Record Drive file ids into portable state during upload
- [ ] Reserved `state.py` methods: `record_description`, `mark_built`,
      `mark_published`
- [ ] Per-folder description record + `source_entry_exists` in portable state,
      so coverage is reportable without a Yandex rescan (see §11)
- [ ] Handle renamed/moved source folders — recognised as one pattern by the
      reconciliation pass (§13), not as many unrelated photo moves
- [ ] Complete plain `scan` as a real end-to-end command: Yandex → workbooks →
      Drive mirror, for one source (`Scanner.scan` / `mirror_folder` are stubs)
- [ ] Split the Drive side into a `sync` command beside `resolve-conflicts` and
      `bootstrap` — three-way sync is not "scanning a source"
- [ ] Every scanning command publishes a portable snapshot, `scan` included
      (today only `run` does, so a single scan leaves `_archive_state/` behind)

## 9. Metadata `build`

Date contract frozen (docs + pure helpers only — see
`family-photo-archive-project.md`'s "Archival Dates and the Google Photos
Compatibility Timestamp"); the ExifTool build pipeline itself is not
implemented.

- [x] Archival date vs. compatibility-timestamp split, precision model —
      supported `DATETIME`/`DAY`/`MONTH`/`YEAR` (named explicitly, not by
      enum order), unsupported `SEASON`/`UNKNOWN` — strict parser,
      noon/day-15/July-1 policy, synthetic flag — `photoarchive/dates.py` +
      `tests/test_dates.py`
- [x] `DATE_COMPATIBILITY_POLICY_VERSION` folded into the build fingerprint
- [x] Non-fatal `SEASON`/`UNKNOWN` contract documented + pure helpers
      (`try_derive_compatibility_timestamp`, `authoritative_xmp_fields`): a
      missing compatibility timestamp must not fail the whole photo build
- [ ] Select only rows ready per workflow rules; work on copies only
- [ ] ExifTool without re-encoding
- [ ] Write `EXIF:DateTimeOriginal` into the correct EXIF sub-IFD (not IFD0 —
      a naive placement silently failed a real Google Photos test); when
      precision is `SEASON`/`UNKNOWN`, leave it absent rather than inventing
      one, and continue the build (see the non-fatal contract above)
- [ ] `EXIF:DateTimeDigitized` only if the scan/digitisation date is actually
      known, never copied from `DateTimeOriginal`
- [ ] Write the required `XMP-archive:ArchiveDate`/`ArchiveDatePrecision`/
      `CompatibilityDateSynthetic` properties (`authoritative_xmp_fields`),
      for every precision including `SEASON`/`UNKNOWN`
- [ ] Custom ExifTool tag config (`-config`/`.ExifTool_config`) declaring the
      `archive` XMP namespace — required before ExifTool can write
      `-XMP-archive:*` at all; does not exist yet
- [ ] Decide whether to also write the optional, experimental
      `XMP-photoshop:DateCreated` mirror (`experimental_standard_date_created`)
      — **not required for correctness**; open question whether real tools
      honour a truncated YEAR/MONTH value, to verify before relying on it
- [ ] Field mapping: GPS from final LatLon, XMP/IPTC caption, keywords,
      People, Place, Description, Event, archive id
- [ ] GPS validation; no invented timezone
- [ ] Preserve existing camera/scanner metadata; explicit precedence rule for
      conflicting dates rather than silently overwriting
- [ ] Re-read built files with ExifTool and validate — including the EXIF tag
      *group*, not just the tag name, and that every `XMP-archive:*` property
      (incl. `SEASON`/`UNKNOWN`, where `CompatibilityDateSynthetic` must be
      genuinely absent) round-trips exactly; deterministic rebuild; per-photo
      failure reporting
- [ ] Real Google Photos ingestion test with production-writer files, all four
      precisions, once build exists (Info-panel date + timeline placement)

## 10. Google Photos publishing

Design **decided** — see the architecture doc: "What Google Photos actually
allows", "The published description", "Publication identity, drift and
republishing". Not implemented; nothing here is blocked on further discussion.

- [x] API capabilities re-checked (Aug 2026): only `appcreateddata` scopes;
      `description` is the only writable field (1000 chars); no delete method;
      location never readable — see the architecture doc
- [ ] Auth; upload only built copies; albums from final `Albums`
- [ ] Persist media ids, never upload twice; error handling and retries
- [ ] Compose the published description deterministically (prose · date ·
      place · coordinates · people · tags · archive ref), reserve the metadata
      tail inside the 1000-character budget, truncate only the prose
- [ ] Composition policy version, so a format change flags every published
      photo instead of looking like user drift
- [ ] Verify by reading the description back and comparing exactly with the
      string that was sent (the truncated one, if truncation happened)
- [ ] v1 automates description + album membership; everything else is a manual
      task with a link to the item
- [ ] Albums: one per source folder in v1. People-derived albums deferred —
      reversible later via `albums.batchAddMediaItems` on already-published
      items (50 per request, 20k per album, no partial success)
- [ ] A per-person view in the dashboard, with links into Photos, as the
      lightweight alternative to People albums
- [ ] Sync status beside `PUBLISHED`, driven by a deterministic per-photo
      fingerprint; no archive-wide revision counter
- [ ] Republish as an explicit intent, entered in `review.xlsx` like a
      `Map Link`: the pipeline acts and reports in `Review Reason`, never edits
      the cell back. Fulfilment is decided by comparing the stored
      `mediaItemId` with the one the request was made against — no clearing
- [ ] A vanished item **without** a request means withdrawn by choice: record
      it, never re-upload. Never duplicate while the original still exists
- [ ] Quotas and batching: 10k requests/day, 50 items per album request, 20k
      per album, no partial success; `batchCreate` can place an item in an
      album during upload

Face recognition and person naming are Photos **UI** features; EXIF/XMP People
metadata does not train Google's face identities — and the API exposes no
person or cluster resource at all, by policy.

## 11. Workflow / status

`APPROVED` is **decided** — a human act, no validation, no auto-approval; see
the architecture doc's "`APPROVED` is a human act". Nothing to build there
beyond what already exists; the follow-ons are below.

- [x] When a row becomes `APPROVED` — only a person types it; the pipeline
      never sets or infers it, and never checks completeness
- [ ] `build` → `BUILT`; `publish` → `PUBLISHED`
- [ ] `build` skips rows with no photo behind them (`DESCRIBED_ABSENT`,
      `SOURCE_MISSING`) whatever their status, and reports what it wrote rather
      than refusing rows with empty finals
- [ ] Rebuild detection after `BUILT`: a human edit to final metadata must mark
      the row as needing a rebuild — a dimension beside `Status`, not a new
      status. Needs a finals hash; `RowState` keeps none today (the unused
      `PhotoReviewRecord.metadata_hash` was meant for this)
- [ ] Source changing after `BUILT`/`PUBLISHED`; rebuild/republish policy —
      see the architecture doc's "Publication identity, drift and republishing"
- [x] **`SKIP` is terminal** — `_flag()` in `review/builder.py` reports the
      change in `Review Reason` without overwriting a skipped row's `Status`;
      covered for description, image, disappearance and return
- [ ] Keep diagnostics in `Review Reason`, not `Status`

Source-description coverage is **not** a workflow status and never gates
anything — see the architecture doc. Implementation steps:

- [ ] Persist the folder record (status + selected document) in `SourceState`;
      decide keying and what happens when a folder disappears
- [ ] Persist `source_entry_exists` per row — `RowState` → portable
      `ItemState` via `_apply_row_state`. Do **not** reuse the latent
      `description_hash is not None` signal: it is an accident of change
      detection
- [ ] Derive `PhotoDescriptionCoverage` from those observations plus the
      workbook source columns; never store the classification
- [ ] Classify source-note patterns explicitly (`SOURCE_STATE` / `DESCRIPTIVE`);
      make `SOURCE_NOTE_PATTERNS` a pattern → class mapping so an unclassified
      pattern cannot be added
- [ ] Make "no coverage outside `FOUND`" structural (`| None` or a result type
      carrying folder status), not a convention
- [ ] Delete or repurpose the unused `PhotoReviewRecord` (`models.py:110`) and
      its never-populated `description_document` field
- [ ] Tests: the invariant above; "`UNKNOWN` never counts as needing
      description"; "no photo-level value outside `FOUND`"; the stale
      source-column case (§6)
- [ ] Pin the invariant with tests: every physical source photo always gets a
      normal editable `review.xlsx` row, whatever its coverage value and
      whatever the folder's description-document state (holds today via
      `Reconciliation.undescribed_photos`; untested as a rule)

## 12. CLI, diagnostics, docs

- [x] `dashboard` command generates `review-all.html`
- [x] CLI shape decided — see the architecture doc's "What each command is for".
      `--local-review` **stays**: it is the local path for a single source
- [x] `scan` reports the missing Drive transport in plain words and names the
      commands that do work, instead of surfacing `Scanner.scan is not
      implemented yet` (`tests/test_cli_scan.py`)
- [ ] Backup before workbook rewrite/import (currently manual)
- [ ] Retention/cleanup for `logs/` and `review-output-backup-*/` (3 already accumulated)
- [ ] Recovery from an interrupted run
- [ ] Document generated directories, backup/log locations, and a one-page "how I run this"

## 13. Reconciliation pass (moved photos, duplicates, orphan descriptions)

Design **decided** — see the architecture doc's "When a photo moves between
folders". A separate archive-wide command; reads durable state, no provider
access; proposes, never mutates on its own.

- [x] Verified on the real source: folder listings expose `resource_id`, `md5`
      and `sha256` per item; the provider's `sha256` equals the locally computed
      one; identical content in two files gets two different `resource_id`s
- [x] Verified by moving a real file: `resource_id` survives a move (`created`
      unchanged, `modified` bumped). Copies get a new id; a re-upload changes
      both id and hash, leaving only the filename
- [ ] `_parse_item` keeps only the first of `md5`/`sha256`, discarding the
      digest that is comparable with `photo_hash` — record `sha256`, or both
- [ ] Matching: `resource_id` (same file) → `sha256` (same image) → filename
      (survives a re-upload; weak, always proposed, never auto-applied)
- [ ] Re-upload into a different folder is the hardest case — both strong
      signals change at once; make sure the pass says *why* it proposed a link
- [ ] Three findings kept distinct: moved photo, true duplicate (**report
      only** in v1), orphan description (cross-folder link, manual only)
- [ ] Proposals shown in `review-all.html` with both previews; decisions
      entered in a proposals workbook, like `resolve-conflicts`
- [ ] Applying a move carries finals, `Status` and portable `ItemState`
      (`drive_file_id`, `build_fingerprint`, `google_photos_media_id`) — the
      row leaves one `review.xlsx` and enters another, so work out what that
      does to both workbooks' sync baselines
- [ ] Durable rejection of a proposal, so the pass never re-offers it
- [ ] Evidence for every applied link: which signal matched, what joined what
- [ ] Folder-level moves proposed as one action
- [ ] Command name, and it stays out of `run` — on demand only

## 14. Tests

- [x] Unit, mocked-HTTP, round-trip and idempotency suites
- [ ] Source-change integration tests against the real folders
- [x] Dashboard aggregation, preview and link tests
- [ ] Later: Drive integration, metadata re-read, Photos mocks

Real-source regression baseline:

- `Ф-ТоняМам-76-83-разное` — 24 description rows, 12 present + 12 absent
- `Ф-ТоняМам-83-93-школа` — 49 photos, no DOCX
- Dictionary — 14 people, 5 places (all with coordinates), 1 tag

Use fixtures, not the live workbooks, for anything user-entered.

## Later / optional

- [ ] Authenticated/private Yandex folders
- [ ] External geocoder — reuse confirmed coordinates first, never invent precision
- [ ] Google Maps short-link (`maps.app.goo.gl`) resolution — needs a redirect follow
- [ ] Person relationship / family modelling
- [ ] Additional reporting and statistics
