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

Verified against the repo, 2026-08-09. **307 tests passing.**

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

## 4. People / Tags iterative propagation

Places are verified on real data; People and Tags are not. No NLP extraction —
manual review plus learning is the deliberate bootstrap.

- [ ] Canonical + confirmed-alias matching, candidates stay inert
- [ ] Merges, catalog edits, rescan propagation into blank finals
- [ ] Non-empty finals preserved; repeat runs idempotent
- [ ] Verify against the real workbooks (14 people, 1 tag learned so far)

## 5. Catalog candidate / evidence UX

- [ ] Evidence view — `catalog.xlsx` exposes `evidence_count` but no reasons;
      likely an `Evidence` sheet
- [ ] Candidate **reject/dismiss**: promotion works, but there's no way to say
      "this is wrong, stop proposing it"
- [ ] Bulk candidate review flow
- [ ] Validate the coordinate-conflict UX against a real conflict

## 6. Source-change lifecycle

Unit-tested; not yet exercised end to end against real sources.

- [ ] Image changed → REVIEW, preview refreshed, finals kept
- [ ] Description changed (and changed after APPROVED) → distinct reasons
- [ ] Photo removed → `SOURCE_MISSING`, row and metadata kept
- [ ] `DESCRIBED_ABSENT` photo appears → same row reused
- [ ] New photos added to an existing folder
- [ ] Nested folders (both real sources are flat — untested live)
- [ ] Source folder renamed
- [ ] Same filename in different folders (identity is scoped; confirm live)
- [ ] Multiple source roots in one run

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
- [ ] Wire real Drive upload/download to the sync decisions (rules are done, transport is not)
- [ ] Record Drive file ids into portable state during upload
- [ ] Reserved `state.py` methods: `record_listing`, `record_description`,
      `mark_built`, `mark_published`
- [ ] Handle renamed/moved source folders
- [ ] Complete plain `scan` (no `--dry-run` / `--local-review`) as a real end-to-end command

## 9. Metadata `build`

- [ ] Select only rows ready per workflow rules; work on copies only
- [ ] ExifTool without re-encoding
- [ ] Field mapping: EXIF `DateTimeOriginal`/`CreateDate`, GPS from final LatLon,
      XMP/IPTC caption, keywords, People, Place, Description, Event, archive id
- [ ] Date normalisation including partial dates; GPS validation
- [ ] Preserve existing camera/scanner metadata unless deliberately replaced
- [ ] Re-read built files to verify; deterministic rebuild; per-photo failure reporting

## 10. Google Photos publishing

- [ ] **Re-check current API capabilities first** — don't rely on old assumptions
- [ ] Auth; upload only built copies; albums from final `Albums`
- [ ] Persist media ids, never upload twice; error handling and retries

Face recognition and person naming are Photos **UI** features; EXIF/XMP People
metadata does not train Google's face identities.

## 11. Workflow / status

- [ ] When a row becomes `APPROVED` (never set automatically today)
- [ ] `build` → `BUILT`; `publish` → `PUBLISHED`
- [ ] Source changing after `BUILT`/`PUBLISHED`; rebuild/republish policy
- [ ] `SKIP` and `SOURCE_MISSING` follow-up
- [ ] Keep diagnostics in `Review Reason`, not `Status`

## 12. CLI, diagnostics, docs

- [x] `dashboard` command generates `review-all.html`
- [ ] Decide whether `--local-review` becomes the default for `scan`
- [ ] Backup before workbook rewrite/import (currently manual)
- [ ] Retention/cleanup for `logs/` and `review-output-backup-*/` (3 already accumulated)
- [ ] Recovery from an interrupted run
- [ ] Document generated directories, backup/log locations, and a one-page "how I run this"

## 13. Tests

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
