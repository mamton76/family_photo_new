"""Dry-run report tests, driven by fake storage and a fake loader.

No cloud access and no real DOCX files: the document loader is injected.
"""

from __future__ import annotations

from photoarchive.models import RemoteSourceItem, SourceRoot
from photoarchive.parsing.docx import DocxError
from photoarchive.scanning.report import build_dry_run_report, render_dry_run_report

ROOT_NAME = "Ф-ТоняМам-76-83-разное"
DOCX_NAME = "Ф-ТоняМам-76-83-разное.docx"


def _root() -> SourceRoot:
    return SourceRoot(url="https://disk.yandex.ru/d/cWAy_XDLrIfMsg", name=ROOT_NAME)


def _file(relative_path: str) -> RemoteSourceItem:
    return RemoteSourceItem(
        name=relative_path.rsplit("/", 1)[-1],
        relative_path=relative_path,
        is_directory=False,
    )


def _dir(relative_path: str) -> RemoteSourceItem:
    return RemoteSourceItem(
        name=relative_path.rsplit("/", 1)[-1],
        relative_path=relative_path,
        is_directory=True,
    )


PARAGRAPHS = (
    ROOT_NAME,
    "20200512_150241   1979. Тоня Мамаева (3г). Дома. - нет фото",
    "Далее Аня или Тоня ????????",
    "20200512_150442   1979 февраль 19. Тоня Мамаева (2.5г)-справа.",
    "Валерий Мамаев (39лет). - нет фото",
    "20200512_999999   описание отсутствующего фото",
)

LISTING = [
    _file("20200512_150241.jpg"),
    _file("20200512_150442.jpg"),
    _file(DOCX_NAME),
    _file("Ф-ТоняМам-76-83-разное.rtf"),
]


def _loader(paragraphs=PARAGRAPHS):
    def load(item: RemoteSourceItem) -> tuple[str, ...]:
        assert item.name.endswith(".docx")
        return paragraphs

    return load


def _report(items=None, loader=None):
    return build_dry_run_report(
        _root(), LISTING if items is None else items, loader or _loader()
    )


# -- Counts ---------------------------------------------------------------


def test_report_counts_entries_and_absences() -> None:
    report = _report()
    folder = report.folders[0]

    assert len(folder.plan.photos) == 2
    assert folder.entry_count == 3
    assert folder.present_and_described == 2
    assert folder.present_without_description == 0
    assert folder.described_but_absent == 1
    assert folder.section_divider_count == 1
    assert folder.source_note_count == 2


def test_summary_totals() -> None:
    report = _report()

    assert report.photos_found == 2
    assert report.documents_found == 1
    assert report.entries_found == 3
    assert report.described_but_absent == 1
    assert report.description_conflicts == 0


# -- Rendering ------------------------------------------------------------


def test_report_shows_the_description_file_and_counts() -> None:
    text = render_dry_run_report(_report())

    assert f"description file: {DOCX_NAME}" in text
    assert "photos present: 2" in text
    assert "description entries: 3" in text
    assert "present + described: 2" in text
    assert "present without description: 0" in text
    assert "described but absent: 1" in text
    assert "section dividers: 1" in text
    assert 'source notes "нет фото": 2' in text


def test_report_lists_other_non_photo_files() -> None:
    text = render_dry_run_report(_report())

    assert "other non-photo files:" in text
    assert "  - Ф-ТоняМам-76-83-разное.rtf" in text


def test_report_shows_destination_path() -> None:
    text = render_dry_run_report(_report())

    assert "[folder] /" in text
    assert f"destination: {ROOT_NAME}" in text


def test_no_docx_produces_a_warning_and_keeps_other_files() -> None:
    items = [_file("020.jpg"), _file("something.rtf"), _file("notes.txt")]

    report = _report(items)
    text = render_dry_run_report(report)

    assert "description file: none" in text
    assert "warning: no DOCX description found" in text
    assert "  - notes.txt" in text
    assert "  - something.rtf" in text
    # Nothing was parsed, so every photo counts as undescribed.
    assert report.folders[0].entry_count == 0
    assert report.folders[0].present_without_description == 1


def test_multiple_docx_surfaces_a_conflict_and_parses_nothing() -> None:
    parsed: list[str] = []

    def load(item: RemoteSourceItem) -> tuple[str, ...]:
        parsed.append(item.name)
        return PARAGRAPHS

    items = [_file("020.jpg"), _file("a.docx"), _file("b.docx")]
    report = build_dry_run_report(_root(), items, load)
    text = render_dry_run_report(report)

    assert parsed == []
    assert report.description_conflicts == 1
    assert "description conflict: yes" in text
    assert "DOCX candidates:" in text
    assert "  - a.docx" in text
    assert "  - b.docx" in text


def test_document_read_failure_is_reported_not_fatal() -> None:
    def load(item: RemoteSourceItem) -> tuple[str, ...]:
        raise DocxError("not a ZIP archive")

    report = build_dry_run_report(_root(), LISTING, load)
    text = render_dry_run_report(report)

    assert report.folders[0].error is not None
    assert "error: could not read the description document" in text


def test_report_without_a_loader_finds_but_does_not_parse() -> None:
    report = build_dry_run_report(_root(), LISTING, None)

    assert report.documents_found == 1
    assert report.entries_found == 0


# -- Verbose --------------------------------------------------------------


def test_verbose_shows_structured_entries() -> None:
    text = render_dry_run_report(_report(), verbose=True)

    assert "[entry] 20200512_150442" in text
    assert "matched photo: 20200512_150442.jpg" in text
    assert "status: present" in text
    assert "section context: Далее Аня или Тоня ????????" in text
    assert "source notes:" in text
    assert "  - нет фото" in text


def test_verbose_shows_absent_entries_with_status() -> None:
    text = render_dry_run_report(_report(), verbose=True)

    assert "[entry] 20200512_999999" in text
    assert "matched photo: none" in text
    assert "status: DESCRIBED_ABSENT" in text


def test_section_context_is_not_merged_into_the_description() -> None:
    text = render_dry_run_report(_report(), verbose=True)

    entry_block = text.split("[entry] 20200512_150442")[1].split("[entry]")[0]
    description = entry_block.split("description:")[1]
    assert "Далее" not in description
    assert "нет фото" not in description


def test_normal_mode_stays_concise() -> None:
    text = render_dry_run_report(_report())

    assert "[entry]" not in text


def test_report_is_deterministic() -> None:
    assert render_dry_run_report(_report(), verbose=True) == render_dry_run_report(
        _report(), verbose=True
    )


def test_folders_without_photos_are_skipped() -> None:
    items = [_dir("empty"), _file("empty/notes.docx")]

    report = _report(items)

    assert report.folders_with_photos == 0
