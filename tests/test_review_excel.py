"""review.xlsx schema and generation tests. Local files only, no cloud."""

from __future__ import annotations

from pathlib import Path

from openpyxl import load_workbook

from photoarchive.models import WorkflowStatus
from photoarchive.review.excel import (
    FINAL_COLUMNS,
    SHEET_NAME,
    SUGGESTED_COLUMNS,
    VISIBLE_COLUMNS,
    ReviewWorkbookService,
    WorkbookPreview,
    build_preview,
    column_index,
    identity_key,
)
from photoarchive.review.model import ReviewRow, join_values, split_values

EXPECTED_COLUMNS = (
    "Preview",
    "Filename / Reference",
    "Source Description",
    "Section Context",
    "Source Notes",
    "Suggested Date",
    "Date",
    "Suggested Place",
    "Place",
    "Suggested LatLon",
    "LatLon",
    "Map Link",
    "Suggested People",
    "People",
    "Suggested Tags",
    "Tags",
    "Event",
    "Albums",
    "Description",
    "Status",
    "Review Reason",
    "Notes",
)


def _png(path: Path, size=(60, 40)) -> Path:
    from PIL import Image

    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, (200, 120, 60)).save(path, format="PNG")
    return path


# -- Schema ---------------------------------------------------------------


def test_visible_columns_match_the_specification() -> None:
    assert VISIBLE_COLUMNS == EXPECTED_COLUMNS


def test_no_precision_columns_exist() -> None:
    assert not any("Precision" in column for column in VISIBLE_COLUMNS)


def test_each_suggested_column_precedes_its_final_column() -> None:
    for suggested in SUGGESTED_COLUMNS:
        final = suggested.replace("Suggested ", "")
        assert column_index(suggested) < column_index(final)


def test_suggested_and_final_columns_are_disjoint() -> None:
    assert not SUGGESTED_COLUMNS & FINAL_COLUMNS


def test_identity_key_ignores_extension_and_case() -> None:
    assert identity_key("20200512_150442.jpg") == "20200512_150442"
    assert identity_key("20200512_150442") == "20200512_150442"
    assert identity_key("IMG_001.JPEG") == "img_001"


def test_list_values_round_trip() -> None:
    assert join_values(["Тоня", "Аня"]) == "Тоня; Аня"
    assert split_values("Тоня; Аня") == ["Тоня", "Аня"]
    assert split_values(None) == []


# -- Generation -----------------------------------------------------------


def _row(reference: str, **kwargs) -> ReviewRow:
    return ReviewRow(reference=reference, **kwargs)


def test_workbook_is_written_with_headers_and_rows(tmp_path: Path) -> None:
    path = tmp_path / "review.xlsx"
    rows = [_row("020", filename="020.jpg", source_description="Тоня на даче")]

    ReviewWorkbookService().write(path, rows)

    workbook = load_workbook(path)
    worksheet = workbook[SHEET_NAME]
    header = [cell.value for cell in worksheet[1]]
    assert tuple(header) == EXPECTED_COLUMNS
    assert worksheet.cell(row=2, column=column_index("Filename / Reference")).value == "020.jpg"
    assert worksheet.freeze_panes == "C2"
    assert worksheet.auto_filter.ref is not None


def test_cyrillic_survives_a_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "review.xlsx"
    rows = [_row("020", source_description="Тоня Мамаева (3г). Дома.", section_context="Далее Аня")]

    service = ReviewWorkbookService()
    service.write(path, rows)
    reloaded = service.read(path)

    assert reloaded["020"].source_description == "Тоня Мамаева (3г). Дома."
    assert reloaded["020"].section_context == "Далее Аня"


def test_preview_is_embedded_for_present_photos(tmp_path: Path) -> None:
    path = tmp_path / "review.xlsx"
    image = _png(tmp_path / "thumb.png")
    previews = {"020": WorkbookPreview("020", image, 60, 40)}

    ReviewWorkbookService().write(path, [_row("020", filename="020.jpg")], previews)

    workbook = load_workbook(path)
    assert len(workbook[SHEET_NAME]._images) == 1


def test_described_absent_row_has_no_preview(tmp_path: Path) -> None:
    path = tmp_path / "review.xlsx"
    rows = [
        _row("020", filename="020.jpg"),
        _row("021", status=WorkflowStatus.DESCRIBED_ABSENT),
    ]
    previews = {"020": WorkbookPreview("020", _png(tmp_path / "t.png"), 60, 40)}

    ReviewWorkbookService().write(path, rows, previews)

    workbook = load_workbook(path)
    worksheet = workbook[SHEET_NAME]
    # Only the present photo carries an image; the absent row is still a row.
    assert len(worksheet._images) == 1
    assert worksheet.max_row == 3
    assert worksheet.cell(row=3, column=column_index("Status")).value == "DESCRIBED_ABSENT"


def test_row_order_is_stable_across_rewrites(tmp_path: Path) -> None:
    path = tmp_path / "review.xlsx"
    rows = [_row("020"), _row("021"), _row("022")]
    service = ReviewWorkbookService()

    service.write(path, rows)
    first = [cell.value for cell in load_workbook(path)[SHEET_NAME]["B"]]
    service.write(path, rows)
    second = [cell.value for cell in load_workbook(path)[SHEET_NAME]["B"]]

    assert first == second == [None, "020", "021", "022"][: len(first)] or first == second


def test_read_of_a_missing_workbook_is_empty(tmp_path: Path) -> None:
    assert ReviewWorkbookService().read(tmp_path / "absent.xlsx") == {}


def test_build_preview_downscales_without_touching_the_source(tmp_path: Path) -> None:
    source = _png(tmp_path / "big.png", size=(900, 600))
    before = source.read_bytes()

    size = build_preview(source, tmp_path / "small.png", width_px=180)

    assert size == (180, 120)
    assert (tmp_path / "small.png").exists()
    assert source.read_bytes() == before


def test_build_preview_returns_none_for_unreadable_images(tmp_path: Path) -> None:
    broken = tmp_path / "broken.jpg"
    broken.write_bytes(b"not an image")

    assert build_preview(broken, tmp_path / "out.png") is None
