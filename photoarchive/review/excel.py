"""``review.xlsx`` schema and workbook service.

Invariants of this workbook — the whole review model depends on them:

1. **One workbook per folder that directly contains photos.** Folders holding
   only subfolders get no workbook.
2. **Previews are embedded** in the workbook itself, so a reviewer never has
   to open the images separately.
3. **Repeated scans preserve human edits.** A rescan may add rows and update
   technical columns, but it never overwrites a value a reviewer has touched.
4. **Source changes are surfaced, not applied.** When a source photo or its
   description changes, the row is flagged (``SOURCE_CHANGED``) and the new
   source text is shown alongside the reviewed values — reviewed data is never
   silently replaced.

Column order below is the on-sheet order: visible columns first, then the
technical columns, which are written but hidden.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from photoarchive.models import PhotoReviewRecord

VISIBLE_COLUMNS: tuple[str, ...] = (
    "Preview",
    "Filename",
    "Source Description",
    "Date",
    "Date Precision",
    "Place",
    "Latitude",
    "Longitude",
    "People",
    "Tags",
    "Event",
    "Albums",
    "Description",
    "Status",
    "Notes",
)

HIDDEN_COLUMNS: tuple[str, ...] = (
    "source_id",
    "source_path",
    "source_hash",
    "description_hash",
    "metadata_hash",
    "processed_hash",
    "google_photos_media_id",
    "last_scan",
)

ALL_COLUMNS: tuple[str, ...] = VISIBLE_COLUMNS + HIDDEN_COLUMNS

#: Columns a reviewer owns. A rescan must never overwrite a non-empty value
#: in one of these.
HUMAN_OWNED_COLUMNS: frozenset[str] = frozenset(
    {
        "Date",
        "Date Precision",
        "Place",
        "Latitude",
        "Longitude",
        "People",
        "Tags",
        "Event",
        "Albums",
        "Description",
        "Status",
        "Notes",
    }
)

SHEET_NAME = "Review"

#: Semicolons separate list values (people, tags, albums) in cells.
LIST_SEPARATOR = "; "


def column_index(name: str) -> int:
    """Return the 1-based worksheet column index for a schema column."""
    return ALL_COLUMNS.index(name) + 1


def format_list(values: Iterable[str]) -> str:
    """Render a list field as a semicolon-separated cell value."""
    return LIST_SEPARATOR.join(value.strip() for value in values if value.strip())


def parse_list(value: str | None) -> list[str]:
    """Parse a semicolon-separated cell value back into a list."""
    if not value:
        return []
    return [part.strip() for part in value.split(";") if part.strip()]


@dataclass(frozen=True, slots=True)
class MergeOutcome:
    """What a rescan did to one workbook."""

    added: tuple[str, ...] = ()
    changed: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()
    untouched: tuple[str, ...] = ()


class ReviewWorkbookService:
    """Reads, merges and writes one folder's ``review.xlsx``.

    The workbook is operated on locally (in the cache) and then uploaded by the
    caller; this class performs no cloud access itself.
    """

    def __init__(self, preview_width_px: int = 180) -> None:
        self.preview_width_px = preview_width_px

    def read(self, path: Path) -> list[PhotoReviewRecord]:
        """Load existing reviewed rows from a workbook.

        TODO: open with openpyxl, map ``ALL_COLUMNS`` back onto
        :class:`PhotoReviewRecord`, and tolerate a workbook a reviewer has
        re-ordered or added their own columns to.
        """
        raise NotImplementedError("ReviewWorkbookService.read is not implemented yet")

    def merge(
        self,
        existing: list[PhotoReviewRecord],
        scanned: list[PhotoReviewRecord],
    ) -> tuple[list[PhotoReviewRecord], MergeOutcome]:
        """Combine a fresh scan with reviewed rows, human edits winning.

        TODO: key rows by ``source_id`` (falling back to ``source_path``), add
        rows for new photos, flag ``SOURCE_CHANGED`` when ``source_hash`` or
        ``description_hash`` moved, flag ``SOURCE_MISSING`` for rows whose
        source disappeared, and leave every other reviewed value untouched.
        """
        raise NotImplementedError("ReviewWorkbookService.merge is not implemented yet")

    def write(self, path: Path, records: list[PhotoReviewRecord]) -> Path:
        """Write the workbook, hiding technical columns.

        TODO: create the ``Review`` sheet with ``ALL_COLUMNS`` as the header,
        hide the ``HIDDEN_COLUMNS`` range, freeze the header row, and size rows
        to fit the embedded previews.
        """
        raise NotImplementedError("ReviewWorkbookService.write is not implemented yet")

    def embed_preview(self, path: Path, row: int, image_path: Path) -> None:
        """Embed one thumbnail into the ``Preview`` column of a row.

        TODO: downscale with Pillow to ``preview_width_px`` and anchor the
        image to the row's Preview cell.
        """
        raise NotImplementedError("ReviewWorkbookService.embed_preview is not implemented yet")
