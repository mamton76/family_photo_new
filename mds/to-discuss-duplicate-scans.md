# TO_DISCUSS — Several Scans of One Photograph

**Purpose:** preserve this topic for a future session.
**Do not implement this yet unless explicitly asked.**

Decided so far (§8): grouping is **manual**, duplicates get their **own
status**, and an **archive-wide photo id** is introduced to address copies.
The perceptual fingerprint is deferred — manual comparison is enough at this
size of archive.

Found in the real archive: the same paper photograph scanned more than once.
Not a copied file — genuinely different scans, at different times, possibly at
different quality.

## 1. Why nothing we already have finds them

The reconciliation pass (architecture doc → "When a photo moves between
folders") knows three signals, and all three answer *"is this the same file?"*:

| Event | `resource_id` | `sha256` | filename |
| --- | --- | --- | --- |
| Photo moved | preserved | preserved | preserved |
| Photo copied | new | preserved | preserved |
| Photo re-uploaded | new | new | preserved |
| **Rescanned print** | **new** | **new** | **usually different** |

Two scans of one print are different files by every honest measure, and saying
so is correct. The question being asked here is a different one: *is this the
same photograph?* That needs a signal about the **image**, not the bytes.

## 2. What this is, in the archive's terms

A fourth finding for the reconciliation pass, beside *moved*, *true duplicate*
and *orphan description*: **same photograph, several files**. It differs from a
true duplicate — those are byte-identical and one of them is redundant. Here
both files are real, both may be worth keeping, and one of them is *better*.

## 3. The signal: a perceptual fingerprint — implemented, unused

A small hash of the picture rather than the file — reduce to 9×8, greyscale,
one bit per comparison of neighbouring pixels — compared by Hamming distance.
Two scans of one print stay close despite different resolution, brightness,
dust and a small crop.

Cheap in this codebase specifically:

- **Pillow is already a dependency** and every photo already passes through
  `build_preview` (`photoarchive/review/excel.py`), which opens and downscales
  it. The fingerprint costs no extra download and no new library.
- It belongs with `source_hash` as a **raw observation**: `RowState` → portable
  `ItemState`. The hash is a fact about the image; "these are one photograph"
  is a judgement built on top of it, and judgements are not stored.

**Recorded from now on**, and read by nothing: `image_fingerprint()` in
`review/excel.py` runs where the photo is already open for its thumbnail, and
the value is kept in `RowState`. Matching is still to be designed. Starting
early is what makes that possible without re-fetching the archive, since a
fingerprint exists only for a photo that has been downloaded.

Limits, stated honestly: a heavy crop defeats it; rotation and mirroring defeat
a naive version, though hashing the four rotations is cheap and fixes that.

## 4. Measured on the real archive

81 cached photos, 3240 pairs, `dHash` over 64 bits. What one photograph looks
like under rescanning:

| Change to the same picture | Distance |
| --- | --- |
| Different resolution (60%) | 0 |
| Brightness +25% | 1 |
| Contrast 1.3 | 1 |
| Crop 3% | **7** |
| Crop 15% | 27 |
| Rotation 90° | 21 |

And what the archive itself contains, with the owner's verdicts:

| Distance | Pair | Verdict |
| --- | --- | --- |
| 7 | `20191030_133718` ↔ `20191030_133726` | **a real duplicate** |
| 10 | `20191030_134811` ↔ `20191030_134833` | **false — merely similar** |

**There is no separating threshold.** A genuine rescan with a small crop scores
7; two different photographs score 10. The two distributions overlap from the
very start, so any cutoff either misses real duplicates or invents them.

A note on reading the filenames: the timestamps are **scan times, not shooting
times**. Two files eight seconds apart mean the same print passed the scanner
twice — which is why the closest pairs are consecutive scans, and why one pair
turned out to be scans made half a year apart.

So the fingerprint is a **ranking**, never a verdict: sort pairs by distance,
show both pictures, and let a person answer. That is what §5's sheet is for.

## 5. The hazard that decides the UX

A family album is full of **series** — two frames taken seconds apart, same
people, same background, a head turned slightly. A perceptual hash separates
those from "two scans of one print" badly, sometimes not at all.

So this can only ever propose. A machine that decides on its own would merge
two different moments of a childhood into one. Both previews go side by side,
and a person answers.

## 6. Proposed UX (from the archive's owner)

A **separate sheet** in the workbook, listing each proposed group with the full
information for every member side by side:

- mark which copy is the best one;
- decide by hand which metadata is current, since the two rows may have been
  reviewed differently;
- afterwards only the chosen copy is built and published, and the others are
  marked as duplicates.

This keeps the decision where every other decision lives — in Excel, next to
the evidence — and matches the existing conflict-workbook pattern.

## 7. What a "link to the other copy" has to contain

There is **no photo UID in `review.xlsx`** today. The only identifying column is
`Filename / Reference`, and identity is *source root + folder + reference stem*
(`identity_key` strips the extension; `row_key` joins folder and identity).
A filename alone cannot address a row: two folders may each hold `001.jpg`, and
that is an expected condition, not an edge case.

So a cross-copy reference needs all three parts, or a new stable per-photo id.
Introducing such an id is itself a decision: it would be the first archive-wide
photo identifier, and everything downstream — Drive, Photos, republish — would
be tempted to key off it.

## 8. Decided

### 8.1 Grouping is manual, for now

No fingerprint, no automatic proposals: a person states that two rows are the
same photograph. This is the same shape as the dictionary's merges — a human
statement the machine records and never second-guesses — and it sidesteps the
series problem (§5) entirely rather than tuning around it.

### 8.2 Duplicates get their own status

Not `SKIP`. The two mean different things and are acted on differently:

| | Meaning | Downstream |
| --- | --- | --- |
| `SKIP` | do not archive this photograph at all | never built, never published |
| duplicate | this *is* archived — under a better copy | this file not built; the chosen one is |

A duplicate row keeps its metadata and its place in the archive; it simply is
not the copy that gets built and published. Conflating it with `SKIP` would
lose the fact that the photograph *is* in the archive.

Like `SKIP`, it is a human decision and a source change must not undo it — the
same guard `_flag` already applies. And a status alone is not enough: a
duplicate has to point at the copy that was chosen, which is what §8.3 is for.

### 8.3 An archive-wide photo id

Introduced deliberately, as the first stable identifier of a photograph.

**Assigned once and persisted**, not derived. A deterministic id computed from
root + folder + reference would look tempting and would break in exactly the
case that matters: a photo that moves between folders changes its derived id,
which is the opposite of what an identity is for. The dictionary already does
this correctly — `person-<uuid12>` assigned at creation — and the same shape
applies: `photo-<uuid12>`.

It must be visible in `review.xlsx`, since addressing the other copy by hand is
the point. That makes it the first machine-owned column in a human-edited
workbook: read-only for a person, regenerated by the pipeline, and outside the
human fields the three-way merge arbitrates.

**No hurry, and that is worth stating**: unlike a fingerprint, an id can be
assigned retroactively to every existing row at any time — it is a label, not
an observation, so nothing needs re-fetching. Starting late costs nothing.

## Questions for the next discussion

1. How does a person state the grouping — type the other copy's id into a
   column on both rows, or fill one sheet listing the group?
2. Does a group get an identity of its own, or is it just rows pointing at the
   chosen copy?
3. Where is the photo id stored besides the workbook — portable `ItemState`
   only, or also SQLite? What assigns it, and when: row creation, or a
   one-off pass over existing rows?
4. What else should key off the photo id once it exists — Drive filenames,
   Photos publication records, the reconciliation pass?
5. Choosing the best copy: purely manual, or with a hint from resolution and
   file size?
6. Metadata across a group: inherited into blank fields the way dictionary
   knowledge already is, or reconciled entirely by hand?
7. Is the duplicate status reversible, and what happens if the chosen copy
   later disappears from the source?
8. Does the duplicate status belong in `WorkflowStatus`, or beside it — like
   the rebuild-needed dimension, which is deliberately not a status?
