"""Collecting every per-folder review workbook into one archive-wide view.

Strictly read-only: this reads the workbooks and never writes to them, to
SQLite or to the catalog. The per-folder ``review.xlsx`` files remain the only
place review metadata is edited.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from photoarchive.catalog.discovery import discover_review_workbooks
from photoarchive.models import SourceRoot, WorkflowStatus
from photoarchive.review.excel import REVIEW_FILENAME, ReviewWorkbookService
from photoarchive.review.model import ReviewRow

#: Final fields whose emptiness a reviewer might want to filter on.
TRACKED_FIELDS: tuple[str, ...] = ("date", "place", "latlon", "people", "tags")


@dataclass(slots=True)
class FolderGroup:
    """One review workbook: its rows plus the counts shown in the summary."""

    source_root: str
    folder: str
    workbook_path: Path
    root_identity: str = ""
    source_url: str | None = None
    rows: list[ReviewRow] = field(default_factory=list)

    @property
    def label(self) -> str:
        return f"{self.source_root}/{self.folder}" if self.folder else self.source_root

    @property
    def present_photos(self) -> int:
        return sum(
            1 for row in self.rows if row.status is not WorkflowStatus.DESCRIBED_ABSENT
        )

    @property
    def absent_photos(self) -> int:
        return sum(
            1 for row in self.rows if row.status is WorkflowStatus.DESCRIBED_ABSENT
        )

    @property
    def status_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for row in self.rows:
            counts[row.status.value] = counts.get(row.status.value, 0) + 1
        return dict(sorted(counts.items()))

    def filled(self, field_name: str) -> int:
        return sum(1 for row in self.rows if (getattr(row, field_name) or "").strip())

    @property
    def needs_review(self) -> int:
        """Rows a person still has to look at: flagged, or missing a final value."""
        return sum(1 for row in self.rows if needs_review(row))


@dataclass(slots=True)
class Aggregate:
    """Every folder in the archive, ready to render."""

    groups: list[FolderGroup] = field(default_factory=list)

    @property
    def rows(self) -> int:
        return sum(len(group.rows) for group in self.groups)

    @property
    def present_photos(self) -> int:
        return sum(group.present_photos for group in self.groups)

    @property
    def absent_photos(self) -> int:
        return sum(group.absent_photos for group in self.groups)

    @property
    def needs_review(self) -> int:
        return sum(group.needs_review for group in self.groups)

    @property
    def status_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for group in self.groups:
            for status, count in group.status_counts.items():
                counts[status] = counts.get(status, 0) + count
        return dict(sorted(counts.items()))

    def filled(self, field_name: str) -> int:
        return sum(group.filled(field_name) for group in self.groups)


def needs_review(row: ReviewRow) -> bool:
    """True when a row still wants human attention.

    Either the pipeline flagged it, or a final field a reviewer would normally
    fill is still empty.
    """
    if row.review_reason.strip():
        return True
    if row.status in (WorkflowStatus.REVIEW, WorkflowStatus.SOURCE_CHANGED):
        return True
    return any(not (getattr(row, name) or "").strip() for name in TRACKED_FIELDS)


def collect(
    source_dir: Path | str,
    source_roots: list[SourceRoot] | None = None,
) -> Aggregate:
    """Read every ``review.xlsx`` below ``source_dir`` into one aggregate.

    Ordering is deterministic — source root, then folder, then the workbook's
    own row order — so regenerating an unchanged archive yields an identical
    page.
    """
    source_dir = Path(source_dir)
    by_name = {root.name: root for root in (source_roots or [])}
    service = ReviewWorkbookService()
    groups: list[FolderGroup] = []

    for path in discover_review_workbooks(source_dir):
        relative = path.parent.relative_to(source_dir)
        parts = relative.parts
        root_name = parts[0] if parts else ""
        folder = "/".join(parts[1:])

        root = by_name.get(root_name)
        groups.append(
            FolderGroup(
                source_root=root_name,
                folder=folder,
                workbook_path=path,
                root_identity=root.identity if root else "",
                source_url=root.url if root else None,
                rows=list(service.read(path).values()),
            )
        )

    groups.sort(key=lambda group: (group.source_root, group.folder))
    return Aggregate(groups=groups)


__all__ = ["Aggregate", "FolderGroup", "REVIEW_FILENAME", "collect", "needs_review"]
