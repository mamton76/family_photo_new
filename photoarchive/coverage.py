"""Source-description coverage: how much the source says about each photo.

The archive's working model is that **Yandex decides what exists and the DOCX
only describes it**. Every physical photo gets a normal, editable review row
whatever the description says or fails to say, so nothing here may ever gate a
photo, change a :class:`~photoarchive.models.WorkflowStatus`, or decide who
gets a row. Coverage reports on the *source material*; it is a source-quality
flag, not a workflow state.

Two levels, because they answer different questions:

``FolderDescriptionStatus``
    What the folder has. Persisted in portable state, since an ``ABSENT``
    folder and an ``AMBIGUOUS`` one leave identical workbooks behind and
    neither can be recovered from `review.xlsx`.

``PhotoDescriptionCoverage``
    What one photo's entry contains — **derived, never stored**, and only
    meaningful when the folder is ``FOUND``. Because the classification is
    computed rather than persisted, changing the policy below needs no
    migration and no policy version: the next render simply says something
    different about the same durable facts.

The classification is deliberately *semantic*. "Some field is non-empty" is
not the rule and must never become it: `_build_entry` moves content out of an
entry's text into its source notes, and a note that reads ``нет фото`` states
the source's condition rather than describing the photograph. What counts is
whether a content element carries **photo-specific description**.
"""

from __future__ import annotations

from enum import Enum

from photoarchive.parsing.descriptions import SourceNoteKind, source_note_kind
from photoarchive.review.model import split_values


class FolderDescriptionStatus(str, Enum):
    """What a photo-containing folder has by way of a description document."""

    #: Exactly one usable ``.docx``, parsed.
    FOUND = "FOUND"
    #: Observed, and there is no description document. Deliberately not
    #: "missing": nothing was lost, and ``SOURCE_MISSING`` keeps that meaning.
    ABSENT = "ABSENT"
    #: Several candidates and none chosen — a source problem a person can fix.
    AMBIGUOUS = "AMBIGUOUS"
    #: Not yet observed. This describes *our records*, never the source: a
    #: completed scan must never write it, and a rescan must always resolve it.
    UNKNOWN = "UNKNOWN"


class PhotoDescriptionCoverage(str, Enum):
    """What the selected document says about one physical photo."""

    #: The entry carries photo-specific descriptive content.
    DESCRIBED = "DESCRIBED"
    #: An entry exists but only inherits the surrounding section context, which
    #: every entry under that divider shares and so says nothing about *this*
    #: photograph.
    CONTEXT_ONLY = "CONTEXT_ONLY"
    #: An entry exists with no content and no inherited context.
    ENTRY_EMPTY = "ENTRY_EMPTY"
    #: A document was selected and parsed, and has no matching entry. It never
    #: means "there was no document" — that is folder state.
    NO_ENTRY = "NO_ENTRY"


#: Coverage values that still want a description written by hand.
NEEDS_DESCRIPTION: frozenset[PhotoDescriptionCoverage] = frozenset(
    {
        PhotoDescriptionCoverage.CONTEXT_ONLY,
        PhotoDescriptionCoverage.ENTRY_EMPTY,
        PhotoDescriptionCoverage.NO_ENTRY,
    }
)


def has_descriptive_content(text: str | None, source_notes: str | None = None) -> bool:
    """Report whether an entry carries photo-specific description.

    Entry text qualifies. Source notes qualify only if their semantic class
    says they describe the photograph — today none do, and adding a pattern
    without classifying it is impossible by construction.
    """
    if (text or "").strip():
        return True
    return any(
        source_note_kind(note) is SourceNoteKind.DESCRIPTIVE
        for note in split_values(source_notes)
    )


def classify(
    folder_status: FolderDescriptionStatus,
    *,
    source_entry_exists: bool,
    text: str | None = None,
    section_context: str | None = None,
    source_notes: str | None = None,
) -> PhotoDescriptionCoverage | None:
    """Classify one photo, or ``None`` when the folder gives no basis for it.

    Outside a ``FOUND`` folder no document was parsed, so no per-photo answer
    was ever computed and none is invented. Returning ``None`` rather than a
    stand-in keeps that honest: a caller cannot read a coverage value without
    having supplied the folder context that makes it meaningful.
    """
    if folder_status is not FolderDescriptionStatus.FOUND:
        return None
    if not source_entry_exists:
        return PhotoDescriptionCoverage.NO_ENTRY
    if has_descriptive_content(text, source_notes):
        return PhotoDescriptionCoverage.DESCRIBED
    if (section_context or "").strip():
        return PhotoDescriptionCoverage.CONTEXT_ONLY
    return PhotoDescriptionCoverage.ENTRY_EMPTY


def is_stale_source_text(
    *, source_entry_exists: bool, text: str | None, section_context: str | None,
    source_notes: str | None,
) -> bool:
    """Report whether a row still carries text its entry no longer supplies.

    When a description document is deleted, the row keeps yesterday's source
    columns — that text is the last thing the source said about this photo and
    is worth keeping. It is marked stale rather than erased, and the mark is
    derived here rather than stored, so it clears itself when the entry returns.
    """
    if source_entry_exists:
        return False
    return bool(
        (text or "").strip()
        or (section_context or "").strip()
        or (source_notes or "").strip()
    )


__all__ = [
    "NEEDS_DESCRIPTION",
    "FolderDescriptionStatus",
    "PhotoDescriptionCoverage",
    "classify",
    "has_descriptive_content",
    "is_stale_source_text",
]
