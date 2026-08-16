"""The review row: one workbook line, split into machine and human halves.

Every metadata field exists twice. ``Suggested X`` is machine-owned and may be
recomputed on any scan. ``X`` is user-owned: it is seeded from the suggestion
exactly once, when the row is first created, and after that the pipeline never
writes it again.

The one deliberate exception is ``Map Link``. Pasting a Google Maps URL into
that cell is an explicit human act, so it may update the final ``LatLon``.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from typing import ClassVar

from photoarchive.models import WorkflowStatus

#: Why a row needs another look. Plain strings: they are shown verbatim in the
#: workbook and read by a person, not matched programmatically.
REASON_DESCRIPTION_CHANGED = "Description changed"
REASON_DESCRIPTION_CHANGED_AFTER_APPROVAL = "Description changed after approval"
REASON_SOURCE_PHOTO_CHANGED = "Source photo changed"
REASON_PREVIOUSLY_ABSENT_FOUND = "Previously absent photo found"
REASON_SOURCE_MISSING = "Source photo no longer present"
REASON_PHOTO_RETURNED = "Photo returned"
#: The description document no longer has an entry for this row, but the source
#: columns still hold what it last said. The text is kept — it is the last thing
#: the source said about this photo — and flagged rather than quietly trusted.
REASON_SOURCE_TEXT_STALE = "Description entry removed; source text below is stale"
REASON_MAP_LINK_APPLIED = "LatLon updated from Map Link"
REASON_MAP_LINK_UNPARSED = "Map Link could not be parsed; LatLon left unchanged"


def new_photo_id() -> str:
    """A stable identity for one photograph, assigned once and never derived.

    Deriving it from source root, folder and filename would be tidy and wrong:
    it would change the moment a photo moves between folders, which is the one
    case an identity exists to survive. Same shape as the dictionary's own ids.
    """
    return f"photo-{uuid.uuid4().hex[:12]}"


@dataclass(slots=True)
class ReviewRow:
    """One row of ``review.xlsx``.

    ``reference`` is the stable identity: the DOCX reference for a described
    photo, or the filename stem for a photo with no description. It survives a
    photo appearing later, which is what lets a ``DESCRIBED_ABSENT`` row be
    reused rather than duplicated.
    """

    reference: str
    filename: str = ""
    #: Archive-wide identity of the photograph, assigned once. Shown in the
    #: workbook read-only, so one copy can be pointed at another by hand.
    photo_id: str = ""

    # Source text, refreshed from the DOCX on every scan.
    source_description: str = ""
    section_context: str = ""
    source_notes: str = ""

    # Machine-owned suggestions.
    suggested_date: str = ""
    suggested_place: str = ""
    suggested_latlon: str = ""
    suggested_people: str = ""
    suggested_tags: str = ""

    # User-owned final values.
    date: str = ""
    place: str = ""
    latlon: str = ""
    map_link: str = ""
    people: str = ""
    tags: str = ""
    event: str = ""
    albums: str = ""
    description: str = ""

    status: WorkflowStatus = WorkflowStatus.NEW
    review_reason: str = ""
    notes: str = ""

    # Not written to the sheet; used while building and previewing.
    is_present: bool = True
    source_path: str = ""

    #: The user-owned fields and the suggestion that may seed each one.
    AUTOFILL_PAIRS: ClassVar[tuple[tuple[str, str], ...]] = (
        ("date", "suggested_date"),
        ("place", "suggested_place"),
        ("latlon", "suggested_latlon"),
        ("people", "suggested_people"),
        ("tags", "suggested_tags"),
        # The archival caption starts as what the document said, so the common
        # case is editing a sentence rather than retyping it. `Source
        # Description` is the machine's copy and is refreshed every scan; this
        # one is the reviewer's to cut down, rewrite or replace.
        ("description", "source_description"),
    )

    def seed_final_from_suggestions(self) -> None:
        """Copy suggestions into the final fields when the row is created."""
        self.fill_empty_final_fields()

    def fill_empty_final_fields(self) -> list[str]:
        """Fill only *blank* final fields from their suggestions.

        This is how dictionary knowledge propagates: once ``learn`` teaches a
        place its coordinates, the next scan can drop them into rows whose
        ``LatLon`` a reviewer never filled in — without touching the ``Place``
        they typed by hand.

        A non-empty final value is never overwritten, on any scan. Returns the
        names of the fields that were filled, for reporting.
        """
        filled: list[str] = []
        for final_attribute, suggested_attribute in self.AUTOFILL_PAIRS:
            current = (getattr(self, final_attribute) or "").strip()
            suggested = (getattr(self, suggested_attribute) or "").strip()
            if current or not suggested:
                continue
            setattr(self, final_attribute, suggested)
            filled.append(final_attribute)
        return filled


def join_values(values: Iterable[str]) -> str:
    """Render a list field as a semicolon-separated cell value."""
    return "; ".join(value.strip() for value in values if value and value.strip())


def split_values(value: str | None) -> list[str]:
    """Parse a semicolon-separated cell value back into a list."""
    if not value:
        return []
    return [part.strip() for part in str(value).split(";") if part.strip()]


def split_list_field(value: str | None) -> list[str]:
    """Split a People or Tags cell, accepting commas as well as semicolons.

    Reviewers type ``Тоня Мамаева, Настя Платова`` far more often than they
    reach for a semicolon, and treating that as one person would poison the
    dictionary with a name that does not exist. Place is deliberately *not*
    split this way: a place legitimately contains commas
    (``Днепропетровская, Москва``).
    """
    if not value:
        return []
    text = str(value).replace(";", ",")
    return [part.strip() for part in text.split(",") if part.strip()]
