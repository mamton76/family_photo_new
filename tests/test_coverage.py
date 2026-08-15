"""Source-description coverage policy.

The invariant behind all of it: coverage describes the source material and
never decides anything about a photo's place in the archive.
"""

from __future__ import annotations

import pytest

from photoarchive.coverage import (
    FolderDescriptionStatus,
    PhotoDescriptionCoverage,
    classify,
    has_descriptive_content,
    is_stale_source_text,
)
from photoarchive.parsing.descriptions import (
    SOURCE_NOTE_PATTERNS,
    SourceNoteKind,
    source_note_kind,
)

FOUND = FolderDescriptionStatus.FOUND


def _classify(**kwargs):
    kwargs.setdefault("source_entry_exists", True)
    return classify(FOUND, **kwargs)


# -- What counts as described ---------------------------------------------


def test_entry_text_is_descriptive() -> None:
    assert _classify(text="Тоня и Аня у школы") is PhotoDescriptionCoverage.DESCRIBED


def test_inherited_section_context_alone_is_not_a_description() -> None:
    """A divider hands the same context to every entry beneath it."""
    assert (
        _classify(text="", section_context="Далее фото 1990 года")
        is PhotoDescriptionCoverage.CONTEXT_ONLY
    )


def test_a_bare_reference_is_an_empty_entry() -> None:
    assert _classify(text="") is PhotoDescriptionCoverage.ENTRY_EMPTY


def test_a_photo_the_document_never_mentions_has_no_entry() -> None:
    assert (
        _classify(source_entry_exists=False, text="")
        is PhotoDescriptionCoverage.NO_ENTRY
    )


def test_a_source_state_note_does_not_describe_the_photograph() -> None:
    """`нет фото` reports the source's condition, not the photo's content."""
    assert source_note_kind("нет фото") is SourceNoteKind.SOURCE_STATE
    assert _classify(text="", source_notes="нет фото") is (
        PhotoDescriptionCoverage.ENTRY_EMPTY
    )


def test_an_unclassified_note_is_never_assumed_descriptive() -> None:
    assert source_note_kind("что-то новое") is SourceNoteKind.SOURCE_STATE
    assert not has_descriptive_content("", "что-то новое")


def test_every_recognised_note_pattern_carries_a_meaning() -> None:
    """A pattern cannot be added without classifying it."""
    assert SOURCE_NOTE_PATTERNS
    for pattern, kind in SOURCE_NOTE_PATTERNS.items():
        assert isinstance(kind, SourceNoteKind), pattern


# -- Coverage exists only under a FOUND folder ----------------------------


@pytest.mark.parametrize(
    "status",
    [
        FolderDescriptionStatus.ABSENT,
        FolderDescriptionStatus.AMBIGUOUS,
        FolderDescriptionStatus.UNKNOWN,
    ],
)
def test_no_photo_level_value_outside_a_found_folder(status) -> None:
    """No document was parsed, so no per-photo answer is invented."""
    assert classify(status, source_entry_exists=False, text="") is None
    assert classify(status, source_entry_exists=True, text="описание") is None


def test_no_entry_never_stands_in_for_a_missing_document() -> None:
    """`NO_ENTRY` means a parsed document that lacks this photo. Nothing else."""
    assert classify(
        FolderDescriptionStatus.ABSENT, source_entry_exists=False
    ) is not PhotoDescriptionCoverage.NO_ENTRY


# -- Stale source text ----------------------------------------------------


def test_text_left_behind_by_a_deleted_entry_is_stale() -> None:
    assert is_stale_source_text(
        source_entry_exists=False,
        text="вчерашнее описание",
        section_context="",
        source_notes="",
    )


def test_text_backed_by_an_entry_is_not_stale() -> None:
    assert not is_stale_source_text(
        source_entry_exists=True,
        text="описание",
        section_context="",
        source_notes="",
    )


def test_a_row_with_no_text_at_all_is_not_stale() -> None:
    assert not is_stale_source_text(
        source_entry_exists=False, text="", section_context="", source_notes=""
    )


# -- Folder reporting -----------------------------------------------------


def test_folder_summary_never_claims_anything_about_an_unobserved_folder() -> None:
    """A gap in our records must not read as a fact about the source."""
    from photoarchive.dashboard.aggregate import FolderGroup
    from photoarchive.dashboard.html import coverage_summary
    from photoarchive.review.model import ReviewRow

    group = FolderGroup(
        source_root="Ф-Тест",
        folder="",
        workbook_path=None,
        rows=[ReviewRow(reference="001", filename="001.jpg")],
        description_status=FolderDescriptionStatus.UNKNOWN,
    )

    summary = coverage_summary(group)

    assert "not yet observed" in summary
    assert "need description" not in summary
    assert group.described == 0
    assert group.needs_description == 0


def test_ambiguous_folder_is_unresolved_rather_than_counted() -> None:
    from photoarchive.dashboard.aggregate import FolderGroup
    from photoarchive.dashboard.html import coverage_summary
    from photoarchive.review.model import ReviewRow

    group = FolderGroup(
        source_root="Ф-Тест",
        folder="",
        workbook_path=None,
        rows=[ReviewRow(reference="001", filename="001.jpg")],
        description_status=FolderDescriptionStatus.AMBIGUOUS,
    )

    summary = coverage_summary(group)

    assert "ambiguous" in summary
    assert "coverage unresolved" in summary
    assert group.needs_description == 0


def test_found_folder_counts_described_and_breaks_down_the_rest() -> None:
    from photoarchive.dashboard.aggregate import FolderGroup
    from photoarchive.dashboard.html import coverage_summary
    from photoarchive.review.model import ReviewRow

    described = ReviewRow(reference="001", filename="001.jpg")
    described.source_description = "Тоня у школы"
    context_only = ReviewRow(reference="002", filename="002.jpg")
    context_only.section_context = "Далее 1990 год"
    empty = ReviewRow(reference="003", filename="003.jpg")
    unmentioned = ReviewRow(reference="004", filename="004.jpg")

    group = FolderGroup(
        source_root="Ф-Тест",
        folder="",
        workbook_path=None,
        rows=[described, context_only, empty, unmentioned],
        description_status=FolderDescriptionStatus.FOUND,
        source_entries={"001": True, "002": True, "003": True, "004": False},
    )

    assert group.described == 1
    assert group.needs_description == 3
    summary = coverage_summary(group)
    assert "4 photos · 1 described · 3 need description" in summary
    assert "1 context only" in summary
    assert "1 empty entries" in summary
    assert "1 no entry" in summary


def test_described_but_absent_rows_are_not_counted_as_photos() -> None:
    """An entry with no photo is not a photograph waiting to be described."""
    from photoarchive.dashboard.aggregate import FolderGroup
    from photoarchive.models import WorkflowStatus
    from photoarchive.review.model import ReviewRow

    absent = ReviewRow(reference="005")
    absent.status = WorkflowStatus.DESCRIBED_ABSENT

    group = FolderGroup(
        source_root="Ф-Тест",
        folder="",
        workbook_path=None,
        rows=[absent],
        description_status=FolderDescriptionStatus.FOUND,
        source_entries={"005": True},
    )

    assert group.coverage_counts == {}
