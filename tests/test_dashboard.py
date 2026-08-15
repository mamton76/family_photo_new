"""The generated read-only dashboard: aggregation, previews, links, rendering."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image

from photoarchive.dashboard.aggregate import collect, needs_review
from photoarchive.dashboard.html import render_dashboard, write_dashboard
from photoarchive.dashboard.links import PhotoDestinations, links_for, primary_link
from photoarchive.dashboard.preview import PreviewProvider
from photoarchive.models import SourceRoot, WorkflowStatus
from photoarchive.review.excel import ReviewWorkbookService
from photoarchive.review.model import ReviewRow

STAMP = datetime(2026, 1, 1, tzinfo=timezone.utc)
ROOT = SourceRoot(url="https://disk.yandex.ru/d/abc123", name="Архив A")


def _row(reference: str, **kwargs) -> ReviewRow:
    status = kwargs.pop("status", WorkflowStatus.NEW)
    row = ReviewRow(reference=reference, **kwargs)
    row.status = status
    return row


def _workbook(base: Path, root: str, folder: str, rows: list[ReviewRow]) -> Path:
    path = base / root / folder / "review.xlsx" if folder else base / root / "review.xlsx"
    ReviewWorkbookService().write(path, rows)
    return path


def _photo(path: Path, size=(1600, 1200)) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, (120, 90, 60)).save(path, format="JPEG")
    return path


def _sample(base: Path) -> Path:
    _workbook(
        base,
        "Архив A",
        "",
        [
            _row("020", filename="020.jpg", place="Школа 565", people="Тоня Мамаева"),
            _row("021", status=WorkflowStatus.DESCRIBED_ABSENT,
                 source_description="описание отсутствующего"),
        ],
    )
    _workbook(base, "Архив B", "1988", [_row("030", filename="030.jpg")])
    return base


# -- Aggregation -----------------------------------------------------------


def test_multiple_workbooks_are_collected(tmp_path: Path) -> None:
    aggregate = collect(_sample(tmp_path))

    assert len(aggregate.groups) == 2
    assert aggregate.rows == 3
    assert aggregate.present_photos == 2
    assert aggregate.absent_photos == 1


def test_groups_are_ordered_deterministically(tmp_path: Path) -> None:
    labels = [group.label for group in collect(_sample(tmp_path)).groups]

    assert labels == ["Архив A", "Архив B/1988"]


def test_summary_totals_equal_the_detail_rows(tmp_path: Path) -> None:
    aggregate = collect(_sample(tmp_path))

    assert aggregate.rows == sum(len(group.rows) for group in aggregate.groups)
    assert aggregate.filled("place") == sum(
        group.filled("place") for group in aggregate.groups
    )
    assert sum(aggregate.status_counts.values()) == aggregate.rows


def test_source_url_is_attached_from_the_state(tmp_path: Path) -> None:
    aggregate = collect(_sample(tmp_path), [ROOT])

    assert aggregate.groups[0].source_url == ROOT.url
    assert aggregate.groups[1].source_url is None


def test_needs_review_flags_incomplete_rows() -> None:
    complete = _row("020", date="1979", place="X", latlon="1.0, 2.0",
                    people="A", tags="t", status=WorkflowStatus.APPROVED)
    assert not needs_review(complete)

    complete.review_reason = "Description changed"
    assert needs_review(complete)
    assert needs_review(_row("021"))


def test_generation_does_not_mutate_the_workbooks(tmp_path: Path) -> None:
    base = _sample(tmp_path)
    before = {p: p.read_bytes() for p in base.rglob("review.xlsx")}

    write_dashboard(collect(base), tmp_path / "review-all.html")

    assert {p: p.read_bytes() for p in base.rglob("review.xlsx")} == before


def test_rendering_is_deterministic(tmp_path: Path) -> None:
    aggregate = collect(_sample(tmp_path))

    first = render_dashboard(aggregate, generated_at=STAMP)
    second = render_dashboard(aggregate, generated_at=STAMP)

    assert first == second


# -- Previews --------------------------------------------------------------


def test_preview_is_rendered_from_a_cached_original(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    _photo(cache / "photos" / "root-1" / "020.jpg")

    preview = PreviewProvider(cache).render("root-1", "020.jpg")

    assert preview is not None
    assert preview.thumbnail.startswith("data:image/jpeg;base64,")
    assert preview.has_medium


def test_preview_is_none_without_a_cached_original(tmp_path: Path) -> None:
    assert PreviewProvider(tmp_path / "cache").render("root-1", "020.jpg") is None


def test_preview_matches_a_reference_without_its_extension(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    _photo(cache / "photos" / "root-1" / "020.jpg")

    assert PreviewProvider(cache).render("root-1", "020") is not None


def test_previews_are_much_smaller_than_the_original(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    original = _photo(cache / "photos" / "root-1" / "020.jpg", size=(4000, 3000))

    preview = PreviewProvider(cache).render("root-1", "020.jpg")

    # The full-size original must never be what gets embedded.
    assert len(preview.medium) < original.stat().st_size
    assert len(preview.thumbnail) < len(preview.medium)


def test_present_photo_gets_an_image_and_absent_gets_a_placeholder(tmp_path: Path) -> None:
    base = _sample(tmp_path / "out")
    cache = tmp_path / "cache"
    _photo(cache / "photos" / ROOT.identity / "020.jpg")

    page = render_dashboard(collect(base, [ROOT]), PreviewProvider(cache), STAMP)

    assert page.count('<img src="data:image/jpeg') == 1
    assert 'class="placeholder"' in page


def test_every_image_source_is_self_contained(tmp_path: Path) -> None:
    base = _sample(tmp_path / "out")
    cache = tmp_path / "cache"
    _photo(cache / "photos" / ROOT.identity / "020.jpg")

    page = render_dashboard(collect(base, [ROOT]), PreviewProvider(cache), STAMP)

    sources = re.findall(r'<img src="([^"]{0,40})', page)
    assert sources and all(source.startswith("data:image/") for source in sources)
    assert "<script src" not in page and "<link " not in page


# -- Destination links -----------------------------------------------------


def test_yandex_only_is_the_primary_destination() -> None:
    links = links_for(PhotoDestinations(yandex_url="https://disk.yandex.ru/d/x"))

    assert [link.kind for link in links] == ["yandex"]
    assert links[0].primary
    assert "Source folder" in links[0].label


def test_photo_level_yandex_link_is_labelled_differently() -> None:
    links = links_for(
        PhotoDestinations(yandex_url="https://disk.yandex.ru/i/y", yandex_is_folder=False)
    )

    assert "Source photo" in links[0].label


def test_drive_outranks_yandex_but_yandex_remains() -> None:
    links = links_for(
        PhotoDestinations(
            yandex_url="https://disk.yandex.ru/d/x",
            google_drive_view_url="https://drive.google.com/file/d/1",
        )
    )

    assert [link.kind for link in links] == ["drive", "yandex"]
    assert links[0].primary and not links[1].primary


def test_photos_becomes_primary_and_older_links_survive() -> None:
    destinations = PhotoDestinations(
        yandex_url="https://disk.yandex.ru/d/x",
        google_drive_view_url="https://drive.google.com/file/d/1",
        google_photos_product_url="https://photos.google.com/lr/photo/2",
    )

    links = links_for(destinations)

    assert [link.kind for link in links] == ["photos", "drive", "yandex"]
    assert primary_link(destinations).kind == "photos"


def test_no_destinations_means_no_buttons() -> None:
    assert links_for(PhotoDestinations()) == []
    assert primary_link(PhotoDestinations()) is None


def test_dashboard_renders_available_links_only(tmp_path: Path) -> None:
    base = _sample(tmp_path)

    with_root = render_dashboard(collect(base, [ROOT]), generated_at=STAMP)
    without = render_dashboard(collect(base), generated_at=STAMP)

    assert ROOT.url in with_root
    assert "disk.yandex.ru" not in without
    assert 'class="links"' not in without


# -- Metadata display ------------------------------------------------------


def test_final_and_suggested_values_both_render(tmp_path: Path) -> None:
    _workbook(
        tmp_path, "Архив A", "",
        [_row("020", filename="020.jpg", place="Дом на Днепропетровской",
              suggested_place="Дома на Днепропетровской")],
    )

    page = render_dashboard(collect(tmp_path), generated_at=STAMP)

    # The canonical interpretation is shown beside the reviewer's own wording.
    assert "Дом на Днепропетровской" in page
    assert "Дома на Днепропетровской" in page
    assert 'class="final"' in page and 'class="sugg"' in page


def test_identical_suggestion_is_not_repeated(tmp_path: Path) -> None:
    _workbook(tmp_path, "Архив A", "",
              [_row("020", place="Школа 565", suggested_place="Школа 565")])

    page = render_dashboard(collect(tmp_path), generated_at=STAMP)

    assert page.count("Школа 565") == 1


def test_coordinates_link_to_google_maps(tmp_path: Path) -> None:
    _workbook(tmp_path, "Архив A", "",
              [_row("020", latlon="55.618485, 37.600828")])

    page = render_dashboard(collect(tmp_path), generated_at=STAMP)

    assert "google.com/maps/search" in page
    assert "55.618485,37.600828" in page


def test_unparsable_coordinates_render_as_plain_text(tmp_path: Path) -> None:
    _workbook(tmp_path, "Архив A", "", [_row("020", latlon="где-то рядом")])

    page = render_dashboard(collect(tmp_path), generated_at=STAMP)

    assert "где-то рядом" in page
    assert "google.com/maps" not in page


def test_source_columns_render(tmp_path: Path) -> None:
    _workbook(
        tmp_path, "Архив A", "",
        [_row("020", source_description="1979. Тоня.", section_context="Далее Аня",
              source_notes="нет фото", review_reason="Description changed")],
    )

    page = render_dashboard(collect(tmp_path), generated_at=STAMP)

    for text in ("1979. Тоня.", "Далее Аня", "нет фото", "Description changed"):
        assert text in page


# -- Filters and read-only guarantees --------------------------------------


def test_filter_metadata_is_present_in_the_markup(tmp_path: Path) -> None:
    page = render_dashboard(collect(_sample(tmp_path)), generated_at=STAMP)

    for attribute in ("data-status", "data-group", "data-text", "data-needsreview",
                      "data-nopeople", "data-noplace", "data-nolatlon",
                      "data-notags", "data-absent"):
        assert attribute in page
    assert 'id="search"' in page and 'id="status"' in page and 'id="folder"' in page


def test_page_contains_no_editing_controls(tmp_path: Path) -> None:
    page = render_dashboard(collect(_sample(tmp_path)), generated_at=STAMP)

    # A read-only viewer must not imply that anything can be saved.
    assert "<form" not in page
    assert "<textarea" not in page
    assert "contenteditable" not in page
    assert '<input id="search" type="search"' in page


def test_cyrillic_and_escaping_survive(tmp_path: Path) -> None:
    _workbook(tmp_path, "Архив A", "",
              [_row("020", notes='Тоня <b>&</b> "Аня"')])

    page = render_dashboard(collect(tmp_path), generated_at=STAMP)

    assert "Тоня" in page
    assert "&lt;b&gt;" in page


def test_write_dashboard_creates_the_file(tmp_path: Path) -> None:
    base = _sample(tmp_path / "out")
    path = write_dashboard(collect(base), tmp_path / "out" / "review-all.html")

    assert path.exists()
    assert path.read_text(encoding="utf-8").startswith("<!doctype html>")


# -- The dictionary panel -------------------------------------------------


def test_dashboard_warns_about_entities_the_last_run_invented(tmp_path: Path) -> None:
    """A typo becomes a canonical entity, so its arrival must be visible."""
    from openpyxl import Workbook

    from photoarchive.dashboard.aggregate import Aggregate
    from photoarchive.dashboard.dictionary import read_summary
    from photoarchive.dashboard.html import render_dashboard

    workbook = Workbook()
    people = workbook.active
    people.title = "People"
    people.append(["person_id", "canonical_name", "confirmed_aliases",
                   "candidate_aliases", "rejected_aliases", "evidence_count", "notes"])
    people.append(["p-1", "Тоня Мамаева", "Тоня", "мамочка", "", 3, ""])
    people.append(["p-2", "Тоня Мамаевa", "", "", "", 1, ""])  # latin 'a': a typo
    evidence = workbook.create_sheet("Evidence")
    evidence.append(["entity_type", "entity_value", "candidate_text", "reason",
                     "status", "source_folder", "reference", "run_id", "created_at"])
    evidence.append(["person", "Тоня Мамаева", "Тоня", "manual correction",
                     "CONFIRMED", "catalog.xlsx", "", "run-001", ""])
    evidence.append(["person", "Тоня Мамаевa", "", "approved metadata vs source text",
                     "CANDIDATE", "review-output", "020", "run-002", ""])
    workbook.save(tmp_path / "catalog.xlsx")
    workbook.close()

    summary = read_summary(tmp_path)

    assert summary.people == 2
    assert summary.candidates == 1
    # Only the entity whose provenance starts in the newest run.
    assert summary.created_recently == ["Тоня Мамаевa"]

    html = render_dashboard(Aggregate(groups=[]), dictionary=summary)
    assert "new in the last run" in html
    assert "Тоня Мамаевa" in html
    assert "1 candidate aliases awaiting a decision" in html


def test_dashboard_renders_without_a_catalog(tmp_path: Path) -> None:
    from photoarchive.dashboard.aggregate import Aggregate
    from photoarchive.dashboard.dictionary import read_summary
    from photoarchive.dashboard.html import render_dashboard

    summary = read_summary(tmp_path)

    assert summary.missing
    assert "Dictionary:" not in render_dashboard(Aggregate(groups=[]), dictionary=summary)
