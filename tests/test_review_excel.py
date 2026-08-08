"""review.xlsx schema tests. No cloud access, no workbook generation yet."""

from __future__ import annotations

import pytest

from photoarchive.review.excel import (
    ALL_COLUMNS,
    HIDDEN_COLUMNS,
    HUMAN_OWNED_COLUMNS,
    VISIBLE_COLUMNS,
    ReviewWorkbookService,
    column_index,
    format_list,
    parse_list,
)

EXPECTED_VISIBLE_COLUMNS = (
    "Preview",
    "Filename",
    "Source Description",
    "Section Context",
    "Source Notes",
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

EXPECTED_HIDDEN_COLUMNS = (
    "source_root_identity",
    "description_document",
    "source_id",
    "source_path",
    "source_hash",
    "description_hash",
    "metadata_hash",
    "processed_hash",
    "google_photos_media_id",
    "last_scan",
)


def test_visible_columns_match_the_specification() -> None:
    assert VISIBLE_COLUMNS == EXPECTED_VISIBLE_COLUMNS


def test_hidden_columns_match_the_specification() -> None:
    assert HIDDEN_COLUMNS == EXPECTED_HIDDEN_COLUMNS


def test_all_columns_are_unique_and_visible_first() -> None:
    assert ALL_COLUMNS == VISIBLE_COLUMNS + HIDDEN_COLUMNS
    assert len(set(ALL_COLUMNS)) == len(ALL_COLUMNS)


def test_column_index_is_one_based() -> None:
    assert column_index("Preview") == 1
    assert column_index("source_root_identity") == len(VISIBLE_COLUMNS) + 1


def test_source_derived_columns_are_not_human_owned() -> None:
    # Section context and source notes come from the DOCX, not the reviewer.
    assert "Section Context" not in HUMAN_OWNED_COLUMNS
    assert "Source Notes" not in HUMAN_OWNED_COLUMNS


def test_human_owned_columns_are_visible_and_exclude_generated_ones() -> None:
    assert HUMAN_OWNED_COLUMNS <= set(VISIBLE_COLUMNS)
    assert "Preview" not in HUMAN_OWNED_COLUMNS
    assert "Filename" not in HUMAN_OWNED_COLUMNS
    assert "Source Description" not in HUMAN_OWNED_COLUMNS


def test_list_values_round_trip_through_cells() -> None:
    assert format_list(["Tonya", "Anya"]) == "Tonya; Anya"
    assert parse_list("Tonya; Anya") == ["Tonya", "Anya"]
    assert parse_list(None) == []
    assert parse_list("") == []


def test_workbook_service_operations_are_still_todo(tmp_path) -> None:
    service = ReviewWorkbookService(preview_width_px=180)

    with pytest.raises(NotImplementedError):
        service.read(tmp_path / "review.xlsx")
    with pytest.raises(NotImplementedError):
        service.write(tmp_path / "review.xlsx", [])
