# TO_DISCUSS — Google Photos Publication, Sync, and Republish Model

**Purpose:** preserve this architectural discussion for a future session.
**Do not implement this yet unless explicitly asked.**

The immediate development priority remains the deterministic pipeline up to
Google Drive / built archive files (see `todo.md` sections 8–9). The Google
Photos synchronization model below is a design constraint and a topic to
revisit **before** implementing the Google Photos publishing phase — not
before.

## 1. Current high-level pipeline

The intended archive flow is approximately:

```text
Yandex Disk source
    ↓
scan / parse descriptions
    ↓
review.xlsx + catalog dictionaries
    ↓
manual review
    ↓
learn / propagate known People / Places / Tags / coordinates
    ↓
repeat review / learn until accepted
    ↓
REVIEWED
    ↓
build processed JPEG copy with ExifTool
    ↓
verify written EXIF / XMP metadata
    ↓
upload built archive copy to Google Drive
    ↓
BUILT
    ↓
explicit Google Photos publish step
    ↓
PUBLISHED
```

Important distinction:

- Yandex Disk remains the immutable source.
- Local files/cache are disposable working state.
- Google Drive stores durable archive state and built files.
- Google Photos is a downstream browsing/publishing surface, **not** the
  canonical source of archive metadata.

Publishing to Google Photos should be an explicit action, not an automatic
consequence of build.

## 2. Two operational modes

Before a photo has ever been published to Google Photos, the system has
broad freedom to regenerate it.

### Mode A — pre-publication / Drive-only workflow

A photo may be:

- rescanned;
- reviewed again;
- have metadata changed;
- rebuilt through ExifTool;
- replaced on Google Drive;
- regenerated repeatedly.

This is the preferred development focus for now.

### Mode B — post-publication workflow

As soon as a photo has successfully been published to Google Photos and has
a stored Google Photos `mediaItemId`, the system must treat it differently.

From this point onward:

- metadata changes must **not** automatically replace the Google Photos
  media item;
- Google Photos may contain user edits such as crop, enhance, rotation,
  colour corrections, etc.;
- replacing the media item could lose those edits or their edit history;
- changes should instead produce Google Photos synchronization issues.

The presence of a valid publication record / `mediaItemId` is the boundary
between the two modes.

## 3. Lifecycle status vs synchronization status

Keep the main photo lifecycle simple.

Possible lifecycle:

```text
NEW / REVIEW
→ REVIEWED
→ BUILT
→ PUBLISHED
```

`BUILT` should mean more than "ExifTool ran": the current reviewed image
revision was successfully built, verified, and safely stored in the archive
destination (Google Drive).

`PUBLISHED` means: a concrete Google Photos media item has been created and
its identifier has been persisted.

After `PUBLISHED`, do not keep extending the main lifecycle status with
every later inconsistency. Instead introduce a separate concept such as
**Sync Status** / **Sync Issues**. For example:

```text
Lifecycle Status: PUBLISHED
Sync Status: NEEDS_ATTENTION

Issues:
- Google Photos date differs from archive
- Description changed
- People / album membership changed
- Location changed and requires manual verification
```

When all applicable issues are resolved, Sync Status returns to something
equivalent to `IN_SYNC`.

## 4. Metadata drift after publication

Once a photo is published, archive metadata may later change — date,
people, place, GPS, description, album membership, tags. These changes
should create a synchronization task for the published photo.

The first version may be deliberately conservative: **prefer manual Google
Photos updates first**; automate only later where the API behavior is well
understood and useful at scale.

For each changed photo, the UI/dashboard should make it obvious:

- which fields changed;
- what the archive currently says;
- what action is required in Google Photos;
- whether the action is automatic, manually verifiable, or not automatically
  verifiable.

The unit of human work may ultimately be the photo, rather than one approval
action per field: *"Update this Google Photos item according to the listed
changes."* The implementation can still retain field-level differences
internally.

## 5. Manual verification and unverifiable fields

Some Google Photos state may not be readable back reliably through the API
— location/geo is the important example discussed so far.

If the system cannot verify a field automatically:

- it should never pretend that it has verified it;
- the user should be able to mark the relevant change as manually resolved;
- that manual approval applies only to the archive state that was approved.

If the archive changes that field again later, the old manual approval must
no longer suppress the new issue. In other words: **manual approval of
version X ≠ permanent approval of that field forever.**

## 6. Sync revisions / state machine — design problem to revisit

There is an unresolved synchronization problem.

A user may receive Google Photos issues, spend days or weeks fixing them
manually, and meanwhile new archive changes may happen before the next sync.
A simple boolean such as `manual_approved = true` is not sufficient, because
it is unclear which set of changes was approved.

Potential solution discussed:

- every synchronization run has an archive-wide sync revision / run ID;
- each detected change set is associated with a revision;
- a manual approval means approximately *"I handled the Google Photos
  changes required up to revision N"*;
- if later archive changes produce revision N+1, a new issue appears
  automatically.

An alternative formulation is to attach approval to a deterministic
change-set fingerprint rather than a numeric revision. This is conceptually
similar to reviewing a Git diff: diff A → reviewed; new change appears →
diff B → requires review again.

**Important: do not implement the revision model yet.** Record it as a
future design topic. Before implementing Google Photos synchronization,
explicitly revisit:

- archive-wide sync revision vs per-photo revision;
- numeric revision vs deterministic fingerprint;
- how partial/scoped syncs interact with revisions;
- how manual approvals are invalidated by later changes;
- whether a simpler model is sufficient.

## 7. Scoped synchronization

Synchronization should be able to run in at least two scopes:

- **Full configured run** — no source parameter: sync all enabled Yandex
  sources from config.
- **One source/folder** — explicit source: sync only this configured Yandex
  source.

When running a scoped sync:

- only that source participates;
- other sources are not inspected;
- their sync state must not be silently updated;
- "not checked in this run" must not be treated as "in sync".

It may be useful to retain `last_checked` / `last_sync` information per
source.

## 8. Google Photos republish intent

There must eventually be an explicit republish workflow for cases where
updating a published photo manually is more work than recreating it —
many metadata fields changed, the built image itself changed, the user
wants to completely rebuild a photo, or rebuild/re-publish an entire
folder/album.

Because Google Photos deletion may require a manual UI action, republish
should be modeled as an **intent**, not as an assumption that the
application can delete anything itself.

Possible conceptual flow for one photo:

```text
photo is PUBLISHED
    ↓
user requests REPUBLISH
    ↓
dashboard provides direct Google Photos link
    ↓
user manually removes the old item from Google Photos
    ↓
next publish/sync checks stored mediaItemId
    ↓
old media item no longer exists
    ↓
photo becomes eligible for fresh publication
    ↓
current JPEG is rebuilt / refreshed if necessary
    ↓
Drive archive copy is updated
    ↓
new Google Photos item is created
    ↓
new mediaItemId is persisted
    ↓
album memberships are recreated
```

**Important safety rule:** republish must not silently create a duplicate
while the previous Google Photos media item still exists. If the old item
is still present, report that manual deletion is still required and do not
publish another copy automatically.

## 9. Folder / album republish

The same concept should work at folder scope. Typical desired user
workflow:

1. User decides a whole source folder / corresponding Google Photos album
   should be regenerated.
2. User removes the affected photos from Google Photos manually.
3. User runs publish/sync for that source.
4. For each previously published photo: use the stored `mediaItemId` to
   check whether the old item still exists; if it still exists, do not
   duplicate it; if it no longer exists, allow normal fresh publication.
5. Persist new `mediaItemId` values and rebuild album membership.

**Open question to revisit:** whether an explicit `republish_requested` flag
is required at photo/folder level, or whether "previously published
`mediaItemId` is now missing" is sufficient to return the item to
publication eligibility. Do not decide this prematurely.

## 10. Do not identify publication by filename

Do not make filename lookup the identity mechanism for Google Photos. After
the first publication, persist the Google Photos `mediaItemId` against the
archive photo's stable internal identity. Conceptually:

```text
archive_photo_id
google_photos_media_item_id
published_at
published_build_fingerprint
```

The archive's own stable photo identity remains primary. Google Photos IDs
are destination-specific references.

## 11. Albums / people

People metadata may imply album membership. Example: `People = Tonya;
Sergey` may mean the published photo should belong to `Album: Tonya` and
`Album: Sergey`. In addition, the source/folder may have its own
corresponding Google Photos album.

Exact album semantics still need to be designed, but synchronization issues
should be able to represent "required album membership changed", and
publishing/republishing must reconstruct the intended memberships.

Do not confuse:

- People metadata stored in archive metadata;
- Google Photos face recognition;
- explicit Google Photos album membership.

They are separate concepts.

## 12. Image edits in Google Photos

**Critical safety constraint: metadata drift must never automatically
trigger media replacement.**

A Google Photos item may have user edits: crop, enhance, rotation, colour
correction, other Google Photos edits. Those edits belong to the Google
Photos media item. The archive master remains independent. Therefore:

- metadata change → sync issue;
- pixel/image revision change → potentially republish workflow.

These are different operations. A future explicit image-revision model may
be useful, but it does not need to be implemented now.

## 13. Recovery / destructive-test requirement

During development it should remain easy to destroy downstream generated
state and recreate it.

Desired property before Google Photos publication exists: starting from
Yandex sources + durable review/catalog/archive state, all built JPEGs and
Google Drive generated copies can be recreated deterministically.

This is important for development and testing. Do not let generated Google
Drive JPEGs become an irreplaceable source of truth. Likewise, deleting a
subset of generated Drive files should eventually be recoverable through
normal build/sync behavior.

## 14. Immediate implementation priority

For now, focus on completing and validating the pipeline before Google
Photos publishing:

```text
Yandex
→ scan
→ review
→ learn
→ review
→ REVIEWED
→ ExifTool build
→ metadata re-read verification
→ Google Drive upload
→ BUILT
```

This pipeline should be: deterministic; repeatable; safe; idempotent where
appropriate; recoverable; able to rebuild individual photos or whole
sources.

Only after this is reliable should Google Photos publishing be implemented.
**Before implementing Google Photos publication, return to this
`TO_DISCUSS` section and resolve the open synchronization/state-machine
questions below.**

## Questions to reopen in the next discussion

1. Do we need a global sync revision number, per-photo revision, or
   deterministic change-set fingerprint?
2. What exactly invalidates a manual Google Photos approval?
3. How should a scoped source sync interact with global revision state?
4. What Google Photos fields can currently be read back and verified
   reliably?
5. Which updates should v1 automate versus deliberately leave manual?
6. What is the exact state machine for:
   - `PUBLISHED` + metadata changed;
   - `PUBLISHED` + image revision changed;
   - `PUBLISHED` + media item missing;
   - `REPUBLISH` requested;
   - manual deletion completed?
7. Is `republish_requested` required, or can missing stored `mediaItemId`
   state drive the workflow safely?
8. What is the folder/album-level republish UX?
9. How should People-derived albums interact with source/folder albums?
10. Should image revisions become a first-class archive concept before
    Google Photos support?

## Working principle

Until these questions are resolved: build the archive so everything up to
Google Drive is freely reproducible. Treat Google Photos publication as a
one-way boundary that introduces synchronization and preservation concerns,
and never silently destroy or replace an already-published media item.
