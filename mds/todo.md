# Family Photo Archive — TODO

Living checklist. Architecture lives in `mds/family-photo-archive-project.md`;
this file is only "what's left and what's next".

---

## Current stable checkpoint

Verified against the repo on 2026-08-09. **307 tests passing.**

- [x] Public Yandex Disk scanning (read-only, no auth), pagination, nested traversal
- [x] `scan --dry-run` inspection report
- [x] DOCX-only description discovery, multiline entry grouping, `Далее …` section context
- [x] `нет фото` source notes; `DESCRIBED_ABSENT` for described-but-missing photos
- [x] Local per-folder `review.xlsx` with embedded previews, hyperlinks, filters
- [x] Suggested (machine-owned) vs Final (user-owned) columns
- [x] Blank final fields filled from suggestions; non-empty finals never overwritten
- [x] `Map Link` as the explicit user-action exception
- [x] `learn` from human-entered final values — **no `APPROVED` gate**
- [x] People / Places / Tags dictionaries in SQLite, confirmed vs candidate, evidence
- [x] Editable bidirectional `catalog.xlsx` (import before use, export after)
- [x] Entity merges via catalog edits; merged duplicates never recreated
- [x] Place ↔ LatLon linked; final Place resolves via canonical/confirmed alias
- [x] Coordinate propagation without any DOCX (route B) — 50/50 real rows filled
- [x] Repeat `learn` and `scan` are idempotent (0 diffs on second pass)
- [x] Per-run DEBUG diagnostic logs with run ids

---

## NEXT — in this order

1. **Commit the current stable milestone** (below)
2. **Generated `review-all.xlsx`** aggregate view
3. **Manual Excel UX inspection**
4. **Validate People/Tags iterative propagation**
5. **Harden source-change scenarios**
6. Google Drive sync → 7. `build` metadata → 8. `publish`

---

## 1. Commit the current stable milestone

- [ ] Review `git diff --staged` (29 files staged, nothing committed)
- [ ] Confirm nothing generated is staged: `review-output/`, `logs/`, `review-output-backup-*/`, `cache/`, `archive.sqlite`, `.idea/` (all currently gitignored — verify)
- [ ] Run the full suite once more
- [ ] Commit as: `feat: add iterative dictionary learning and place coordinate propagation`

---

## 2. Aggregate workbook — `review-output/review-all.xlsx`

**Ownership rule for v1:** per-folder `review.xlsx` stays the *authoritative
editable* input; `review-all.xlsx` is a **generated read-only aggregate view**.
Do not import edits from it — two equal editable sources would conflict.

- [ ] `Summary` sheet: one row per source folder — Source Root, Folder, Rows,
      Present Photos, DESCRIBED_ABSENT, status counts, Date/People/Place/LatLon/Tags
      filled counts, Needs Review; plus a totals row
- [ ] `Review` sheet: all rows combined, with Source Root + Folder prepended to
      the normal review columns
- [ ] Sort by Source Root → folder → Filename / Reference
- [ ] Freeze panes, filters, outline grouping so folders collapse
- [ ] Keep embedded previews, reusing the existing preview builder (no parallel implementation)
- [ ] CLI surface for generating it (see CLI polish)
- [ ] Tests: multiple workbooks aggregated; stable ordering/grouping;
      `DESCRIBED_ABSENT` included; previews present; summary totals equal detail rows;
      regeneration deterministic; **generation never mutates per-folder files**
- [ ] Later decision: after real use, decide whether `review-all.xlsx` should become
      the primary editable review UI — if so, design for exactly one authoritative
      edit source

---

## 3. Manual Excel UX inspection

Enough Excel behaviour exists now that this stays an explicit human task, not
only automated tests. Open and judge:

- [ ] Per-folder `review.xlsx`: previews, column widths, freeze panes, filters,
      LatLon hyperlinks, Suggested vs Final readability, Cyrillic rendering
- [ ] `catalog.xlsx`: candidate amber highlighting, whether evidence counts are
      understandable, whether candidate curation is discoverable
- [ ] `review-all.xlsx` once it exists
- [ ] Note anything that needs layout changes before more data is entered

---

## 4. People and Tags — validate the iterative loop

Places/LatLon are verified against real data; People and Tags are not.
**Do not add NLP extraction to manufacture suggestions** — manual review plus
learning is the deliberate bootstrap strategy.

- [ ] Canonical + confirmed-alias matching for People and Tags
- [ ] Candidate aliases stay inert (no suggestions, no facts)
- [ ] Merges via catalog edits
- [ ] Human catalog edits → `learn` → rescan propagation into blank finals
- [ ] Non-empty finals preserved; repeated runs idempotent
- [ ] Verify against the real workbooks (14 people, 1 tag currently learned)

---

## 5. Catalog usability

- [ ] Practical flow for reviewing candidate aliases in bulk
- [ ] Explicit reject/dismiss path for candidates (currently only promote works)
- [ ] `Evidence` sheet in `catalog.xlsx` — provenance is currently only in SQLite
      and logs; `evidence_count` is exposed but not the reasons themselves
- [ ] Surface a few example evidence rows per entity
- [ ] Document merge behaviour where a user will actually find it
- [ ] Validate the coordinate-conflict UX with a real conflict

---

## 6. Source-change lifecycle hardening

Unit-tested; not yet exercised end to end against the real sources.

- [ ] Source image changed (hash moves) → REVIEW, preview refreshed, finals kept
- [ ] Description changed → suggestions refresh, finals kept, reason set
- [ ] Description changed after APPROVED → distinct reason
- [ ] Source photo removed → `SOURCE_MISSING`, row and metadata kept
- [ ] `DESCRIBED_ABSENT` photo later appears → same row reused, not duplicated
- [ ] New photos added to an existing folder
- [ ] Nested folders (both real sources are currently flat — untested in the wild)
- [ ] Source folder renamed / display name changed
- [ ] Same filename in different folders (identity is scoped; confirm live)
- [ ] Repeat scan after manual edits
- [ ] Multiple source roots in one run

---

## 7. Yandex Disk source improvements

Public MVP works and is sufficient for the current archive.

- [ ] Robust retry / rate-limit / transient network handling
- [ ] Download and cache efficiency for large folders
- [ ] Pagination against a folder with >200 items (never exercised live)
- [ ] Stronger remote identity if the public API offers better than URL-derived
- [ ] Optional: authenticated/private folders — **not a blocker**

---

## 8. Google Drive destination

Next major infrastructure phase. `GoogleDriveStorage` is a skeleton; plain
`scan` (without `--local-review` / `--dry-run`) still raises.

Target topology:

```text
Drive root/
├── catalog.xlsx
├── review-all.xlsx          # if we decide to sync it
└── <source-root-name>/
    └── <mirrored folders>/
        ├── review.xlsx
        └── photos...
```

- [ ] OAuth flow, token cache, credential handling
- [ ] Use the configured destination root from config
- [ ] One destination folder per Yandex source root; mirror nested paths
- [ ] Reuse the existing destination subtree on rerun (idempotent, no duplicates)
- [ ] Upload photo copies; never touch source originals
- [ ] Upload/sync per-folder `review.xlsx` without clobbering reviewer edits
- [ ] Decide placement/sync of `review-all.xlsx`
- [ ] `catalog.xlsx` at the root
- [ ] Stable mapping source folder ↔ Drive folder id (state tables exist, unused)
- [ ] Handle renamed/moved source folders safely
- [ ] No workbooks in pure intermediate directories
- [ ] Verify idempotent sync

---

## 9. Metadata build — `python app.py build`

- [ ] Select only rows ready per workflow rules
- [ ] Work on processed copies; never rewrite source originals
- [ ] ExifTool invocation without image re-encoding
- [ ] Map final review fields → tags:
      EXIF `DateTimeOriginal`/`CreateDate`, EXIF GPS from final LatLon,
      XMP/IPTC caption, keywords from Tags, People (XMP person field), Place,
      Description, Event, archive/provenance id
- [ ] Date normalisation, including partial/approximate dates
- [ ] GPS conversion and validation from the canonical `lat, lon` text
- [ ] Preserve existing scanner/phone metadata unless deliberately replaced
- [ ] Re-read built files and validate what was written
- [ ] Deterministic rebuild / idempotency
- [ ] Per-photo failure reporting

---

## 10. Google Photos publishing

- [ ] **Re-check current API capabilities and restrictions first** — do not rely
      on old assumptions
- [ ] Authentication
- [ ] Upload only built copies
- [ ] Albums from the final `Albums` column
- [ ] Persist media ids; never upload twice
- [ ] Error handling and retries

Note: face recognition and person naming are Google Photos **UI** features.
EXIF/XMP People metadata does not train Google's face identities — do not design
around unsupported API behaviour.

---

## 11. Workflow / status completion

- [ ] When does a row become `APPROVED`? (currently never set automatically)
- [ ] `build` → `BUILT`; `publish` → `PUBLISHED`
- [ ] What happens when the source changes after `BUILT` / `PUBLISHED`
- [ ] Rebuild / republish policy
- [ ] `SKIP` semantics
- [ ] `SOURCE_MISSING` follow-up
- [ ] Keep diagnostics in `Review Reason`, not overloaded into `Status`

---

## 12. CLI polish

Current: `scan [--dry-run|--local-review] [--output-dir]`, `learn [--source] [--dry-run]`,
`build`/`publish` (explicit not-implemented).

- [ ] Command/flag to generate `review-all.xlsx`
- [ ] Decide whether `--local-review` stays a flag or becomes the default for `scan`
- [ ] Optional catalog validation command
- [ ] Keep the surface small — no commands without a real workflow

---

## 13. Diagnostics and recoverability

- [x] Stable run ids, per-run DEBUG log, log path printed at end
- [ ] Backup before workbook rewrite/import (currently manual)
- [ ] Recovery from an interrupted run
- [ ] Retention/cleanup for `logs/` and `review-output-backup-*/`
      (3 backup dirs already accumulated)
- [ ] End-to-end trace in one place: source row → evidence → suggestion → final

---

## 14. Test strategy

- [x] Unit tests (307)
- [x] Mocked Yandex HTTP tests (no live network in unit tests)
- [x] Workbook and dictionary round-trip tests
- [x] Idempotency tests
- [ ] Source-change integration tests against the real folders
- [ ] Aggregate workbook tests
- [ ] Later: Drive integration, metadata re-read, Google Photos mocks

Real-source regression expectations:

- DOCX source `Ф-ТоняМам-76-83-разное`: 24 description rows, 12 present + 12 absent
- No-DOCX source `Ф-ТоняМам-83-93-школа`: 49 photos
- Dictionary: 14 people, 5 places (all with coordinates), 1 tag

Do not hardcode live user-entered values into tests — use fixtures, not the live
workbooks.

---

## 15. Documentation

- [x] `mds/family-photo-archive-project.md` matches the implementation
- [x] README documents the real CLI
- [ ] Keep `todo.md` short and current
- [ ] Document generated directories and what is gitignored
- [ ] Document backup and log locations
- [ ] One-page "how I actually run this" in a few commands

---

## Later / optional

- [ ] Authenticated/private Yandex folders
- [ ] External geocoder for places lacking coordinates — must reuse confirmed
      coordinates first and never invent precision
- [ ] Google Maps short-link (`maps.app.goo.gl`) resolution — needs a redirect follow
- [ ] Richer candidate/evidence UI
- [ ] `review-all.xlsx` as the primary editable review surface
- [ ] More automatic metadata extraction, only if manual bootstrapping gets too slow
- [ ] Person relationship / family modelling
- [ ] Additional reporting and statistics
