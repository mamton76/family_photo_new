"""Collecting every per-folder review workbook into one archive-wide view.

Strictly read-only: this reads the workbooks and never writes to them, to
SQLite or to the catalog. The per-folder ``review.xlsx`` files remain the only
place review metadata is edited.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from photoarchive.catalog.discovery import discover_review_workbooks
from photoarchive.coverage import (
    NEEDS_DESCRIPTION,
    FolderDescriptionStatus,
    PhotoDescriptionCoverage,
    classify,
)
from photoarchive.models import SourceRoot, WorkflowStatus
from photoarchive.portable.models import STATE_DIRECTORY
from photoarchive.portable.store import PortableStateStore
from photoarchive.review.excel import REVIEW_FILENAME, ReviewWorkbookService, identity_key
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
    #: What the source folder has by way of a description document. Read from
    #: portable state, because a folder with no document and one with several
    #: competing documents leave identical workbooks behind.
    description_status: FolderDescriptionStatus = FolderDescriptionStatus.UNKNOWN
    #: Filename of the document actually used, when exactly one was.
    description_document: str | None = None
    #: ``identity -> did the document have an entry for this row``. The one
    #: durable fact the workbook cannot supply: an entry with empty text and a
    #: photo the document never mentions produce identical rows.
    source_entries: dict[str, bool] = field(default_factory=dict)

    def coverage(self, row: ReviewRow) -> PhotoDescriptionCoverage | None:
        """This row's coverage, or ``None`` outside a ``FOUND`` folder."""
        return classify(
            self.description_status,
            source_entry_exists=self.source_entries.get(
                identity_key(row.reference), False
            ),
            text=row.source_description,
            section_context=row.section_context,
            source_notes=row.source_notes,
        )

    @property
    def coverage_counts(self) -> dict[PhotoDescriptionCoverage, int]:
        """How many present photos fall into each coverage value."""
        counts: dict[PhotoDescriptionCoverage, int] = {}
        for row in self.rows:
            if row.status is WorkflowStatus.DESCRIBED_ABSENT:
                # A description entry with no photo is not a photo to describe.
                continue
            value = self.coverage(row)
            if value is not None:
                counts[value] = counts.get(value, 0) + 1
        return counts

    @property
    def described(self) -> int:
        return self.coverage_counts.get(PhotoDescriptionCoverage.DESCRIBED, 0)

    @property
    def needs_description(self) -> int:
        counts = self.coverage_counts
        return sum(counts.get(value, 0) for value in NEEDS_DESCRIPTION)

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

    Source-description coverage is read from ``_archive_state/`` alongside the
    workbooks: which document a folder had, and which rows the document
    actually mentioned, are facts no `review.xlsx` can carry. State written
    before those facts existed simply leaves coverage unobserved, which is
    reported as such and never as "this folder has no description".
    """
    source_dir = Path(source_dir)
    by_name = {root.name: root for root in (source_roots or [])}
    service = ReviewWorkbookService()
    state = _load_source_states(source_dir)
    groups: list[FolderGroup] = []

    for path in discover_review_workbooks(source_dir):
        relative = path.parent.relative_to(source_dir)
        parts = relative.parts
        root_name = parts[0] if parts else ""
        folder = "/".join(parts[1:])

        root = by_name.get(root_name)
        identity = root.identity if root else ""
        record, entries = _coverage_facts(state.get(identity), folder)
        groups.append(
            FolderGroup(
                source_root=root_name,
                folder=folder,
                workbook_path=path,
                root_identity=identity,
                source_url=root.url if root else None,
                rows=list(service.read(path).values()),
                description_status=record[0],
                description_document=record[1],
                source_entries=entries,
            )
        )

    groups.sort(key=lambda group: (group.source_root, group.folder))
    return Aggregate(groups=groups)


def _load_source_states(source_dir: Path) -> dict:
    """Portable state beside the workbooks, or nothing if it is not there.

    Read-only and best-effort: the dashboard is a view. Unreadable or absent
    state leaves every folder unobserved rather than failing the render.
    """
    store = PortableStateStore(source_dir / STATE_DIRECTORY)
    if not store.exists:
        return {}
    try:
        return store.load().sources
    except Exception:  # noqa: BLE001 - a broken snapshot must not break the view
        return {}


def _coverage_facts(
    source_state, folder: str
) -> tuple[tuple[FolderDescriptionStatus, str | None], dict[str, bool]]:
    """The folder's description record and its per-row entry observations."""
    if source_state is None:
        return (FolderDescriptionStatus.UNKNOWN, None), {}

    record = source_state.folders.get(folder)
    if record is None:
        # Never observed — which is a statement about our records, not about
        # the source, so it must not read as "this folder has no document".
        status, document = FolderDescriptionStatus.UNKNOWN, None
    else:
        try:
            status = FolderDescriptionStatus(record.status)
        except ValueError:
            status = FolderDescriptionStatus.UNKNOWN
        document = record.document

    prefix = f"{folder}|"
    entries = {
        key[len(prefix):]: item.source_entry_exists
        for key, item in source_state.items.items()
        if key.startswith(prefix)
    }
    return (status, document), entries


__all__ = [
    "Aggregate",
    "FolderGroup",
    "REVIEW_FILENAME",
    "collect",
    "needs_review",
]
