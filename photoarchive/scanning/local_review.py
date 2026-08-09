"""Generating local ``review.xlsx`` workbooks for a scanned source root.

This is the whole scan pipeline short of the cloud: read Yandex, parse the
DOCX, suggest, merge with any previous workbook, embed previews and save. It
writes only to the local output directory — no Google service is contacted.

**Order matters, and it is the transaction.** Every observation a folder needs
is made before anything is persisted: the listing is materialised, then the
description is fetched and parsed, and only then are rows merged, the workbook
saved and row state written. A provider failure during the reads raises before
any state is recorded, so a source is never left half-new and half-old.

The one failure that cannot abort the run — a folder's DOCX being unreadable
while the rest of the archive is fine — is handled explicitly rather than
implicitly: the folder is reported with an error and its rows are left exactly
as they were. A failed read is never evidence that photos were deleted.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from photoarchive.catalog.models import Dictionary
from photoarchive.catalog.places import resolve_place
from photoarchive.models import RemoteSourceItem, SourceRoot, WorkflowStatus
from photoarchive.parsing.descriptions import (
    ParsedDescriptionDocument,
    Reconciliation,
    reconcile_entries,
)
from photoarchive.parsing.suggestions import Suggestion, suggest
from photoarchive.review.builder import BuildOutcome, RowState, build_rows
from photoarchive.review.excel import (
    REVIEW_FILENAME,
    ReviewWorkbookService,
    WorkbookPreview,
    build_preview,
    identity_key,
    rows_signature,
)
from photoarchive.scanning.report import FolderReport
from photoarchive.scanning.scanner import destination_path

LOG = logging.getLogger(__name__)

#: Fetches one photo into a local path; returns ``None`` when unavailable.
PhotoFetcher = Callable[[RemoteSourceItem, Path], Path | None]


@dataclass(slots=True)
class FolderReviewResult:
    """What generating one folder's workbook produced."""

    folder_path: str
    workbook_path: Path
    rows: int = 0
    previews: int = 0
    absent_rows: int = 0
    outcome: BuildOutcome | None = None
    suggested_dates: int = 0
    suggested_places: int = 0
    suggested_people: int = 0
    suggested_tags: int = 0
    people_matched: int = 0
    places_matched: int = 0
    tags_matched: int = 0
    coordinates_reused: int = 0
    place_lookups: int = 0
    ambiguous_places: int = 0
    suggested_latlon: int = 0
    candidates: list[str] = field(default_factory=list)
    #: Bookkeeping to persist after the workbook is written.
    states: dict[str, RowState] = field(default_factory=dict)
    #: False when the workbook was already up to date and left untouched.
    written: bool = True


def suggestions_for(
    document: ParsedDescriptionDocument | None,
    reconciliation: Reconciliation | None,
    dictionary: Dictionary,
) -> dict[str, Suggestion]:
    """Build one suggestion per described entry and per undescribed photo."""
    result: dict[str, Suggestion] = {}

    if document is not None:
        for entry in document.entries:
            result[entry.reference] = suggest(
                entry.text, dictionary, entry.section_context
            )

    if reconciliation is not None:
        for photo in reconciliation.undescribed_photos:
            # No description means no evidence; an empty suggestion is correct.
            result[photo.name] = Suggestion()

    return result


def generate_folder_review(
    source_root: SourceRoot,
    folder: FolderReport,
    dictionary: Dictionary,
    output_dir: Path,
    cache_dir: Path,
    fetch_photo: PhotoFetcher | None = None,
    existing_states: dict[str, RowState] | None = None,
) -> FolderReviewResult:
    """Build and save one folder's ``review.xlsx``.

    Returns the result, including the exact path written.
    """
    plan = folder.plan
    reconciliation = folder.reconciliation or reconcile_entries([], plan.photos)

    suggestions = suggestions_for(folder.document, reconciliation, dictionary)
    workbook_path = (
        Path(output_dir) / destination_path(source_root, plan.folder_path) / REVIEW_FILENAME
    )

    service = ReviewWorkbookService()
    existing = service.read(workbook_path)
    # The content already on disk. If the merge produces the same thing, the
    # file is left completely alone: same bytes, same mtime, nothing to upload.
    previous_signature = rows_signature(list(existing.values()))

    previews, photo_hashes = _prepare_previews(
        plan.photos, cache_dir, fetch_photo, source_root, plan.folder_path
    )

    outcome, _states = build_rows(
        reconciliation=reconciliation,
        suggestions=suggestions,
        existing=existing,
        states=existing_states,
        photo_hashes=photo_hashes,
        # Route B: a Place the reviewer typed is itself a dictionary key for
        # coordinates, which is the only route available without a DOCX.
        place_lookup=lambda value: resolve_place(dictionary, value),
        # A folder whose DOCX failed to load must not look like a folder whose
        # photos all vanished.
        descriptions_readable=folder.error is None,
    )

    written = service.write(
        workbook_path, outcome.rows, previews, previous_signature=previous_signature
    )

    result = FolderReviewResult(
        folder_path=plan.folder_path,
        workbook_path=workbook_path,
        rows=len(outcome.rows),
        previews=sum(
            1
            for row in outcome.rows
            if row.reference in previews or identity_key(row.reference) in previews
        ),
        absent_rows=sum(
            1 for row in outcome.rows if row.status is WorkflowStatus.DESCRIBED_ABSENT
        ),
        outcome=outcome,
        states=_states,
        written=written,
    )
    _count_suggestions(result, outcome, suggestions)
    result.place_lookups = len(outcome.place_lookups)
    result.ambiguous_places = len(outcome.ambiguous_places)
    return result


def _count_suggestions(
    result: FolderReviewResult, outcome: BuildOutcome, suggestions: dict[str, Suggestion]
) -> None:
    for row in outcome.rows:
        if row.suggested_date:
            result.suggested_dates += 1
        if row.suggested_place:
            result.suggested_places += 1
        if row.suggested_people:
            result.suggested_people += 1
        if row.suggested_tags:
            result.suggested_tags += 1
        if row.suggested_latlon:
            result.suggested_latlon += 1

    for suggestion in suggestions.values():
        result.people_matched += suggestion.people_matched
        result.places_matched += suggestion.places_matched
        result.tags_matched += suggestion.tags_matched
        result.coordinates_reused += suggestion.coordinates_reused
        result.candidates.extend(
            f"{match.entity_type.value}: {match.matched_text} -> {match.canonical}"
            for match in suggestion.candidates
        )


def _prepare_previews(
    photos: list[RemoteSourceItem],
    cache_dir: Path,
    fetch_photo: PhotoFetcher | None,
    source_root: SourceRoot,
    folder_path: str,
) -> tuple[dict[str, WorkbookPreview], dict[str, str]]:
    """Download photos and build thumbnails; sources are never modified."""
    previews: dict[str, WorkbookPreview] = {}
    hashes: dict[str, str] = {}

    if fetch_photo is None:
        return previews, hashes

    for photo in photos:
        key = identity_key(photo.name)
        local = Path(cache_dir) / "photos" / source_root.identity / photo.relative_path
        try:
            fetched = fetch_photo(photo, local)
        except Exception as error:  # noqa: BLE001 - one bad photo is not fatal
            LOG.debug("Could not fetch %s: %s", photo.name, error)
            continue
        if fetched is None or not Path(fetched).exists():
            continue

        hashes[key] = photo.content_hash or _file_hash(Path(fetched))

        thumbnail = (
            Path(cache_dir) / "previews" / source_root.identity / folder_path / f"{key}.png"
        )
        size = build_preview(Path(fetched), thumbnail)
        if size is None:
            continue

        # Keyed by both, so entry rows and undescribed-photo rows both find it.
        preview = WorkbookPreview(
            reference=key, image_path=thumbnail, width_px=size[0], height_px=size[1]
        )
        previews[key] = preview
        previews[photo.name] = preview
        previews[Path(photo.name).stem] = preview

    return previews, hashes


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
