"""Building and re-building review rows across repeated scans.

The rescan contract, in one sentence: **a scan may rewrite everything the
machine owns and nothing the human owns.**

Concretely, on a row that already exists the builder refreshes source text and
``Suggested …`` values, then decides whether the row needs another look — but
it never *overwrites* a non-empty ``Date``, ``Place``, ``LatLon``, ``People``
or ``Tags``. It deliberately does not try to work out whether the reviewer
edited those fields; a value that still equals the old suggestion is treated as
theirs, because guessing wrong would silently discard a deliberate decision.

A **blank** final field is different. It holds no human decision to protect, so
it may be filled from the current suggestion. That is how dictionary knowledge
propagates: once ``learn`` teaches a place its coordinates, the next scan drops
them into rows whose ``LatLon`` nobody filled in, while leaving the ``Place``
typed by hand exactly as it was.

``Map Link`` remains the explicit exception: pasting a parsable Google Maps URL
is an instruction, so it may update even a non-empty final ``LatLon``.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass, field

from photoarchive.catalog.places import PlaceResolution
from photoarchive.coverage import is_stale_source_text
from photoarchive.geo import parse_map_link
from photoarchive.models import WorkflowStatus
from photoarchive.parsing.descriptions import ReconciledEntry, Reconciliation
from photoarchive.parsing.suggestions import Suggestion
from photoarchive.review.excel import identity_key
from photoarchive.review.model import (
    REASON_DESCRIPTION_CHANGED,
    REASON_DESCRIPTION_CHANGED_AFTER_APPROVAL,
    REASON_MAP_LINK_APPLIED,
    REASON_MAP_LINK_UNPARSED,
    REASON_PHOTO_RETURNED,
    REASON_PREVIOUSLY_ABSENT_FOUND,
    REASON_SOURCE_MISSING,
    REASON_SOURCE_TEXT_STALE,
    REASON_SOURCE_PHOTO_CHANGED,
    ReviewRow,
    join_values,
)

#: Resolves a place value to a confirmed dictionary entry (route B).
PlaceLookup = Callable[[str], PlaceResolution]

#: Statuses that mean "a human already looked at this".
_REVIEWED_STATUSES = frozenset(
    {
        WorkflowStatus.APPROVED,
        WorkflowStatus.BUILT,
        WorkflowStatus.PUBLISHED,
    }
)


def text_hash(*parts: str | None) -> str:
    """Stable hash of the source inputs behind a row."""
    joined = "\x00".join((part or "").strip() for part in parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


@dataclass(slots=True)
class RowState:
    """Bookkeeping the pipeline keeps about a row between scans.

    Held in SQLite rather than in workbook columns: it is the pipeline's
    memory, not something a reviewer should see or edit.
    """

    identity: str
    photo_hash: str = ""
    description_hash: str = ""
    suggestion_hash: str = ""
    status: str = WorkflowStatus.NEW.value
    was_absent: bool = False
    #: Whether the folder's description document had an entry for this row.
    #: A raw observation, not a classification: an entry with empty text and a
    #: photo the document never mentions leave identical workbook rows, so
    #: nothing else can tell them apart later.
    source_entry_exists: bool = False


@dataclass(slots=True)
class BuildOutcome:
    """What one folder's rebuild did."""

    rows: list[ReviewRow] = field(default_factory=list)
    created: list[str] = field(default_factory=list)
    description_changed: list[str] = field(default_factory=list)
    photo_changed: list[str] = field(default_factory=list)
    became_present: list[str] = field(default_factory=list)
    went_missing: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    map_links_applied: list[str] = field(default_factory=list)
    map_links_unparsed: list[str] = field(default_factory=list)
    #: "<row>.<field>" for every blank final field filled from a suggestion.
    autofilled: list[str] = field(default_factory=list)
    #: Rows whose non-empty final values were left untouched.
    preserved: list[str] = field(default_factory=list)
    #: Rows whose coordinates came from their own final Place (route B).
    place_lookups: list[str] = field(default_factory=list)
    #: Rows whose final Place matched several confirmed places.
    ambiguous_places: list[str] = field(default_factory=list)
    #: Rows carrying source text their description document no longer supplies.
    stale_source_text: list[str] = field(default_factory=list)
    #: Rows whose photo disappeared earlier and has now come back.
    photos_returned: list[str] = field(default_factory=list)


def build_rows(
    reconciliation: Reconciliation,
    suggestions: dict[str, Suggestion],
    existing: dict[str, ReviewRow] | None = None,
    states: dict[str, RowState] | None = None,
    photo_hashes: dict[str, str] | None = None,
    place_lookup: PlaceLookup | None = None,
    descriptions_readable: bool = True,
) -> tuple[BuildOutcome, dict[str, RowState]]:
    """Produce the rows for one folder, merging with what came before.

    ``existing`` is the previous workbook keyed by :func:`identity_key`, and
    ``states`` is the matching bookkeeping. Both empty means a first scan.

    Row order is: described entries in document order, then any undescribed
    photos by filename — deterministic, so an unchanged rescan produces an
    identical sheet.
    """
    existing = existing or {}
    states = states or {}
    photo_hashes = photo_hashes or {}

    outcome = BuildOutcome()
    next_states: dict[str, RowState] = {}
    seen: set[str] = set()

    for reconciled in reconciliation.entries:
        row, state = _build_entry_row(
            reconciled, suggestions, existing, states, photo_hashes, outcome,
            place_lookup,
        )
        outcome.rows.append(row)
        next_states[state.identity] = state
        seen.add(state.identity)

    for photo in reconciliation.undescribed_photos:
        key = identity_key(photo.name)
        if key in seen:
            continue
        row, state = _build_photo_row(
            photo.name, photo.relative_path, suggestions, existing, states,
            photo_hashes, outcome, place_lookup,
        )
        outcome.rows.append(row)
        next_states[state.identity] = state
        seen.add(key)

    # A row we have seen before but that appears in neither list has lost its
    # source. Keep the row and everything the reviewer put in it.
    #
    # Unless the folder's description document could not be read: then every
    # described-but-absent row would look like it had disappeared, and a
    # momentary network failure would rewrite a dozen rows. An unreadable
    # description is reported, never acted on.
    for key, previous in existing.items():
        if key in seen:
            continue
        if not descriptions_readable:
            outcome.rows.append(previous)
            outcome.unchanged.append(key)
            next_states[key] = states.get(key) or RowState(
                identity=key, status=previous.status.value
            )
            continue
        _flag(previous, WorkflowStatus.SOURCE_MISSING, REASON_SOURCE_MISSING)
        outcome.rows.append(previous)
        outcome.went_missing.append(key)
        next_states[key] = RowState(identity=key, status=previous.status.value)

    return outcome, next_states


def _build_entry_row(
    reconciled: ReconciledEntry,
    suggestions: dict[str, Suggestion],
    existing: dict[str, ReviewRow],
    states: dict[str, RowState],
    photo_hashes: dict[str, str],
    outcome: BuildOutcome,
    place_lookup: PlaceLookup | None = None,
) -> tuple[ReviewRow, RowState]:
    entry = reconciled.entry
    key = identity_key(entry.reference)
    photo = reconciled.photo

    suggestion = suggestions.get(entry.reference, Suggestion())
    source_notes = join_values(entry.display_source_notes)
    description_hash = text_hash(entry.text, entry.section_context, source_notes)
    suggestion_hash = text_hash(
        suggestion.date,
        suggestion.place,
        suggestion.latlon,
        join_values(suggestion.people),
        join_values(suggestion.tags),
    )
    photo_hash = photo_hashes.get(key, "") if photo is not None else ""

    row = existing.get(key)
    previous = states.get(key)

    if row is None:
        row = ReviewRow(reference=entry.reference)
        _apply_source(row, entry, photo, source_notes)
        _apply_suggestions(row, suggestion)
        # First creation only: the reviewer starts from the machine's best guess.
        row.seed_final_from_suggestions()
        row.status = (
            WorkflowStatus.NEW if photo is not None else WorkflowStatus.DESCRIBED_ABSENT
        )
        outcome.created.append(key)
        return row, RowState(
            identity=key,
            photo_hash=photo_hash,
            description_hash=description_hash,
            suggestion_hash=suggestion_hash,
            status=row.status.value,
            was_absent=photo is None,
            source_entry_exists=True,
        )

    # Existing row: source text and suggestions are refreshed, finals are not.
    row.reference = entry.reference
    _apply_source(row, entry, photo, source_notes)
    _apply_suggestions(row, suggestion)
    _apply_place_coordinates(row, place_lookup, outcome, key)
    # Blank final fields may be filled from suggestions; non-empty ones never.
    if _has_final_values(row):
        outcome.preserved.append(key)
    outcome.autofilled.extend(f"{key}.{name}" for name in row.fill_empty_final_fields())
    _apply_map_link(row, outcome, key)

    was_absent = previous.was_absent if previous else row.status is WorkflowStatus.DESCRIBED_ABSENT
    description_moved = bool(previous) and previous.description_hash != description_hash
    photo_moved = bool(previous) and bool(photo_hash) and previous.photo_hash != photo_hash

    if was_absent and photo is not None:
        _flag(row, WorkflowStatus.REVIEW, REASON_PREVIOUSLY_ABSENT_FOUND)
        outcome.became_present.append(key)
    elif _returned(previous, photo is not None):
        # Not "the source photo changed": nothing changed, it came back.
        _flag(row, WorkflowStatus.REVIEW, REASON_PHOTO_RETURNED)
        outcome.photos_returned.append(key)
    elif photo_moved:
        _flag(row, WorkflowStatus.REVIEW, REASON_SOURCE_PHOTO_CHANGED)
        outcome.photo_changed.append(key)
    elif description_moved:
        approved = _status_of(previous, row) in _REVIEWED_STATUSES
        _flag(
            row,
            WorkflowStatus.REVIEW,
            REASON_DESCRIPTION_CHANGED_AFTER_APPROVAL if approved
            else REASON_DESCRIPTION_CHANGED,
        )
        outcome.description_changed.append(key)
    elif photo is None:
        if row.status is not WorkflowStatus.SKIP:
            row.status = WorkflowStatus.DESCRIBED_ABSENT
        outcome.unchanged.append(key)
    else:
        outcome.unchanged.append(key)

    return row, RowState(
        identity=key,
        photo_hash=photo_hash or (previous.photo_hash if previous else ""),
        description_hash=description_hash,
        suggestion_hash=suggestion_hash,
        status=row.status.value,
        was_absent=photo is None,
        source_entry_exists=True,
    )


def _build_photo_row(
    filename: str,
    source_path: str,
    suggestions: dict[str, Suggestion],
    existing: dict[str, ReviewRow],
    states: dict[str, RowState],
    photo_hashes: dict[str, str],
    outcome: BuildOutcome,
    place_lookup: PlaceLookup | None = None,
) -> tuple[ReviewRow, RowState]:
    """A present photo that no description mentions."""
    key = identity_key(filename)
    suggestion = suggestions.get(filename, Suggestion())
    photo_hash = photo_hashes.get(key, "")

    row = existing.get(key)
    previous = states.get(key)

    if row is None:
        row = ReviewRow(reference=key, filename=filename, source_path=source_path)
        _apply_suggestions(row, suggestion)
        row.seed_final_from_suggestions()
        row.status = WorkflowStatus.NEW
        outcome.created.append(key)
        return row, RowState(
            identity=key,
            photo_hash=photo_hash,
            status=row.status.value,
        )

    row.filename = filename
    row.source_path = source_path
    _apply_suggestions(row, suggestion)
    _apply_place_coordinates(row, place_lookup, outcome, key)
    if _has_final_values(row):
        outcome.preserved.append(key)
    outcome.autofilled.extend(f"{key}.{name}" for name in row.fill_empty_final_fields())
    _apply_map_link(row, outcome, key)

    if _returned(previous, True):
        _flag(row, WorkflowStatus.REVIEW, REASON_PHOTO_RETURNED)
        outcome.photos_returned.append(key)
    elif previous and photo_hash and previous.photo_hash != photo_hash:
        _flag(row, WorkflowStatus.REVIEW, REASON_SOURCE_PHOTO_CHANGED)
        outcome.photo_changed.append(key)
    else:
        outcome.unchanged.append(key)

    _mark_stale_source_text(row, outcome, key)

    return row, RowState(
        identity=key,
        photo_hash=photo_hash or (previous.photo_hash if previous else ""),
        status=row.status.value,
    )


def _apply_place_coordinates(
    row: ReviewRow, place_lookup: PlaceLookup | None, outcome: BuildOutcome, key: str
) -> None:
    """Route B: resolve the row's *own* final Place against the dictionary.

    ``Suggested Place`` means **"how the system currently understands this
    place canonically"** — not "what the source-text parser extracted". So it
    may be filled either by matching the description or, here, by resolving the
    Place a reviewer already typed. Seeing the canonical name beside their own
    wording is how a reviewer can tell that their spelling is being recognised
    through a confirmed alias.

    Coordinates belong to a canonical Place, so that same resolution supplies
    ``Suggested LatLon``. This is the only route available in a folder with no
    DOCX, where there is no source text to match at all.

    The contract, in full:

    * ``Suggested Place`` is machine-owned and may come from either route.
    * ``Place`` is user-owned once non-empty and is **never** rewritten into the
      canonical spelling.
    * A **confirmed** alias may therefore produce a canonical ``Suggested
      Place`` with no DOCX present.
    * The resolved Place supplies ``Suggested LatLon``; the blank-fill rule then
      decides whether it reaches ``LatLon``, and a non-empty ``LatLon`` is left
      alone.
    * **Candidate** aliases resolve to nothing — no canonical name, no
      coordinates.
    * An ambiguous value picks no canonical Place silently.
    """
    if place_lookup is None or not (row.place or "").strip():
        return

    resolution = place_lookup(row.place)
    if resolution.is_ambiguous:
        # Several confirmed places answer to this name; guessing one would
        # attach the wrong coordinates.
        outcome.ambiguous_places.append(f"{key}: {row.place}")
        return
    if not resolution.resolved:
        return

    # Showing the canonical name is informative when the reviewer used an
    # alias; it never rewrites what they typed in Place.
    if not row.suggested_place:
        row.suggested_place = resolution.canonical

    point = resolution.latlon
    if point is None or row.suggested_latlon:
        return

    row.suggested_latlon = point.format()
    outcome.place_lookups.append(f"{key}: {row.place} -> {point.format()}")


def _has_final_values(row: ReviewRow) -> bool:
    """True when the reviewer has put something in a user-owned field."""
    return any(
        (getattr(row, attribute) or "").strip()
        for attribute, _ in ReviewRow.AUTOFILL_PAIRS
    )


def _apply_source(row: ReviewRow, entry, photo, source_notes: str) -> None:
    row.source_description = entry.text
    row.section_context = entry.section_context or ""
    row.source_notes = source_notes
    row.is_present = photo is not None
    if photo is not None:
        row.filename = photo.name
        row.source_path = photo.relative_path


def _apply_suggestions(row: ReviewRow, suggestion: Suggestion) -> None:
    """Suggestions are machine-owned and always safe to rewrite."""
    row.suggested_date = suggestion.date
    row.suggested_place = suggestion.place
    row.suggested_latlon = suggestion.latlon
    row.suggested_people = join_values(suggestion.people)
    row.suggested_tags = join_values(suggestion.tags)


def _apply_map_link(row: ReviewRow, outcome: BuildOutcome, key: str) -> None:
    """Honour a Google Maps URL the reviewer pasted into ``Map Link``.

    This is the one place a final field is written by the pipeline, because
    pasting the link *is* the reviewer's instruction. An unparsable link
    changes nothing and says so in ``Review Reason``.
    """
    if not row.map_link:
        return

    point = parse_map_link(row.map_link)
    if point is None:
        row.review_reason = REASON_MAP_LINK_UNPARSED
        outcome.map_links_unparsed.append(key)
        return

    formatted = point.format()
    if row.latlon == formatted:
        return

    row.latlon = formatted
    row.review_reason = REASON_MAP_LINK_APPLIED
    outcome.map_links_applied.append(key)


def _returned(previous: RowState | None, present: bool) -> bool:
    """True when a photo the pipeline had given up on is back in the source."""
    return bool(
        present
        and previous is not None
        and previous.status == WorkflowStatus.SOURCE_MISSING.value
    )


def _mark_stale_source_text(row: ReviewRow, outcome: BuildOutcome, key: str) -> None:
    """Flag source text the description document no longer supplies.

    Derived, not stored: a row with no entry whose source columns still hold
    text is stale by definition, and the flag clears itself when the entry
    comes back. The text stays — it is the last thing the source said — and the
    diagnostic goes in ``Review Reason`` without touching ``Status``, because
    this says something about the source, not about the review.
    """
    stale = is_stale_source_text(
        source_entry_exists=False,
        text=row.source_description,
        section_context=row.section_context,
        source_notes=row.source_notes,
    )
    if not stale:
        return
    outcome.stale_source_text.append(key)
    # A stronger reason from this same pass wins the single Reason cell.
    if not row.review_reason.strip():
        row.review_reason = REASON_SOURCE_TEXT_STALE


def _flag(row: ReviewRow, status: WorkflowStatus, reason: str) -> None:
    """Record why a row changed, and move it to ``status`` — unless it is SKIP.

    ``SKIP`` is a person saying *do not archive this photograph*. It is about
    the photo itself, not about its description, so neither a rewritten DOCX
    entry nor a re-scanned image overturns it. The change is still reported in
    ``Review Reason``, so the decision can be revisited deliberately rather
    than reversed silently.
    """
    row.review_reason = reason
    if row.status is not WorkflowStatus.SKIP:
        row.status = status


def _status_of(previous: RowState | None, row: ReviewRow) -> WorkflowStatus:
    if previous is not None:
        try:
            return WorkflowStatus(previous.status)
        except ValueError:
            pass
    return row.status
