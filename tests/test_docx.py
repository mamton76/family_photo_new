"""DOCX extraction tests. Documents are built in-memory; no internet."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from photoarchive.parsing.docx import DOCUMENT_PART, DocxError, extract_paragraphs

NAMESPACE = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def _document(body: str) -> bytes:
    return f'<?xml version="1.0"?><w:document {NAMESPACE}><w:body>{body}</w:body></w:document>'.encode()


def _paragraph(*runs: str) -> str:
    return "<w:p>" + "".join(f"<w:r><w:t>{run}</w:t></w:r>" for run in runs) + "</w:p>"


def _docx(path: Path, body: str, *, part: str = DOCUMENT_PART) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(part, _document(body))
    return path


def test_extracts_paragraphs_in_document_order(tmp_path: Path) -> None:
    path = _docx(
        tmp_path / "d.docx",
        _paragraph("first") + _paragraph("second") + _paragraph("third"),
    )

    assert extract_paragraphs(path) == ("first", "second", "third")


def test_joins_multiple_runs_within_one_paragraph(tmp_path: Path) -> None:
    # Word splits a sentence into runs whenever formatting changes.
    path = _docx(tmp_path / "d.docx", _paragraph("20200512_150442", "   1979 ", "февраль"))

    assert extract_paragraphs(path) == ("20200512_150442   1979 февраль",)


def test_preserves_cyrillic(tmp_path: Path) -> None:
    path = _docx(tmp_path / "d.docx", _paragraph("Тоня Мамаева (2.5г)-справа"))

    assert extract_paragraphs(path) == ("Тоня Мамаева (2.5г)-справа",)


def test_drops_empty_and_whitespace_only_paragraphs(tmp_path: Path) -> None:
    path = _docx(
        tmp_path / "d.docx",
        _paragraph("kept") + "<w:p/>" + _paragraph("   ") + _paragraph("also kept"),
    )

    assert extract_paragraphs(path) == ("kept", "also kept")


def test_tabs_and_breaks_become_whitespace(tmp_path: Path) -> None:
    body = "<w:p><w:r><w:t>ref</w:t><w:tab/><w:t>text</w:t></w:r></w:p>"
    path = _docx(tmp_path / "d.docx", body)

    assert extract_paragraphs(path) == ("ref text",)


def test_reads_paragraphs_nested_in_tables(tmp_path: Path) -> None:
    body = f"<w:tbl><w:tr><w:tc>{_paragraph('in a table')}</w:tc></w:tr></w:tbl>"
    path = _docx(tmp_path / "d.docx", body)

    assert extract_paragraphs(path) == ("in a table",)


def test_accepts_raw_bytes(tmp_path: Path) -> None:
    path = _docx(tmp_path / "d.docx", _paragraph("from bytes"))

    assert extract_paragraphs(path.read_bytes()) == ("from bytes",)


def test_non_zip_file_raises_a_clear_error(tmp_path: Path) -> None:
    path = tmp_path / "not.docx"
    path.write_bytes(b"this is plain text, not a zip")

    with pytest.raises(DocxError, match="not a ZIP archive"):
        extract_paragraphs(path)


def test_zip_without_document_part_raises(tmp_path: Path) -> None:
    path = tmp_path / "d.docx"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/other.xml", b"<x/>")

    with pytest.raises(DocxError, match="no word/document.xml"):
        extract_paragraphs(path)


def test_malformed_xml_raises(tmp_path: Path) -> None:
    path = tmp_path / "d.docx"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(DOCUMENT_PART, b"<w:document><unclosed>")

    with pytest.raises(DocxError, match="not valid XML"):
        extract_paragraphs(path)


def test_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(DocxError, match="Could not open"):
        extract_paragraphs(tmp_path / "absent.docx")


def test_empty_document_returns_no_paragraphs(tmp_path: Path) -> None:
    assert extract_paragraphs(_docx(tmp_path / "d.docx", "")) == ()
