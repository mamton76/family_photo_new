"""Domain models shared across the pipeline.

These types are storage-provider agnostic on purpose: nothing here may depend
on Yandex Disk, Google Drive or Google Photos specifics.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class WorkflowStatus(str, Enum):
    """Lifecycle of a single photo inside the archive."""

    NEW = "NEW"
    REVIEW = "REVIEW"
    APPROVED = "APPROVED"
    BUILT = "BUILT"
    PUBLISHED = "PUBLISHED"
    SOURCE_CHANGED = "SOURCE_CHANGED"
    SOURCE_MISSING = "SOURCE_MISSING"
    ERROR = "ERROR"
    SKIP = "SKIP"


class DatePrecision(str, Enum):
    """How precisely the capture date of a photo is known.

    Precision is stored separately from the date itself, so that an
    approximate date is never mistaken for an exact one.
    """

    EXACT = "exact"
    MONTH = "month"
    SEASON = "season"
    YEAR = "year"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class SourceRoot:
    """One supplied Yandex Disk source root.

    Every source root gets its own dedicated folder under the configured
    Google Drive root, named after the source folder, and the nested source
    hierarchy is mirrored *below* that folder. Two source roots therefore
    never write into each other's subtree.

    Identity is derived from ``url``, not from ``name``: a person may rename
    the source folder on Yandex Disk, and that must not be read as "a brand
    new archive". Renames are surfaced instead (see
    :attr:`~photoarchive.state.StateRepository`).
    """

    url: str
    name: str
    remote_id: str | None = None

    @property
    def identity(self) -> str:
        """Stable short id for this source root, derived from its URL."""
        normalized = self.url.strip().rstrip("/")
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True, slots=True)
class RemoteSourceItem:
    """One file or folder as reported by a remote storage provider.

    ``relative_path`` is always relative to the scanned source root and uses
    forward slashes, so the same hierarchy can be mirrored on the destination.

    Identity must not rely on ``name`` alone: files can be renamed, and equal
    names appear in different folders.
    """

    name: str
    relative_path: str
    is_directory: bool
    remote_id: str | None = None
    size: int | None = None
    modified_at: datetime | None = None
    content_hash: str | None = None

    @property
    def parent_path(self) -> str:
        """Relative path of the containing folder ("" for the source root)."""
        head, _, _ = self.relative_path.rpartition("/")
        return head


@dataclass(slots=True)
class PhotoReviewRecord:
    """One row of a ``review.xlsx`` workbook.

    Visible fields are human-owned: once a reviewer edits them, a repeated scan
    must not overwrite them. Technical fields are pipeline-owned and used to
    detect new, changed and missing sources.
    """

    # Visible, human-reviewable fields.
    filename: str
    source_path: str
    source_description: str | None = None
    date: str | None = None
    date_precision: DatePrecision = DatePrecision.UNKNOWN
    place: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    people: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    event: str | None = None
    albums: list[str] = field(default_factory=list)
    description: str | None = None
    status: WorkflowStatus = WorkflowStatus.NEW
    notes: str | None = None

    # Technical, pipeline-owned fields (hidden columns in the workbook).
    source_id: str | None = None
    source_hash: str | None = None
    description_hash: str | None = None
    metadata_hash: str | None = None
    processed_hash: str | None = None
    google_photos_media_id: str | None = None
    last_scan: datetime | None = None


@dataclass(frozen=True, slots=True)
class MetadataProposal:
    """Automatically proposed metadata for one photo.

    A proposal is never authoritative: it seeds a review row and always
    requires human confirmation. ``inferred_fields`` lists the field names that
    were guessed from context (path, folder names) rather than read from an
    explicit source.
    """

    date: str | None = None
    date_precision: DatePrecision = DatePrecision.UNKNOWN
    place: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    people: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    event: str | None = None
    description: str | None = None
    inferred_fields: frozenset[str] = frozenset()
