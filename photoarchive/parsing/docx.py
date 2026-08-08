"""DOCX text extraction, using only the standard library.

A ``.docx`` file is a ZIP archive whose main content lives in
``word/document.xml``. This module opens it with :mod:`zipfile`, parses it with
:mod:`xml.etree.ElementTree` and returns the document's paragraphs in order.

Word splits a single visible paragraph into several ``<w:t>`` *runs* whenever
formatting changes mid-sentence, so the runs of one ``<w:p>`` are joined back
together here. The XML is parsed as XML — never with regular expressions — so
that entities and attribute quirks cannot corrupt the text.

This module knows nothing about photo archives: it turns bytes into
paragraphs. Interpreting those paragraphs is
:mod:`photoarchive.parsing.descriptions`' job.
"""

from __future__ import annotations

import zipfile
from pathlib import Path
from xml.etree import ElementTree

#: WordprocessingML namespace used by every element we care about.
WORD_NAMESPACE = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"

#: The part of the ZIP archive holding the document body.
DOCUMENT_PART = "word/document.xml"

_PARAGRAPH_TAG = f"{{{WORD_NAMESPACE}}}p"
_TEXT_TAG = f"{{{WORD_NAMESPACE}}}t"
_TAB_TAG = f"{{{WORD_NAMESPACE}}}tab"
_BREAK_TAG = f"{{{WORD_NAMESPACE}}}br"


class DocxError(RuntimeError):
    """Raised when a file is not a readable DOCX document."""


def extract_paragraphs(source: Path | str | bytes) -> tuple[str, ...]:
    """Return the non-empty paragraphs of a DOCX file, in document order.

    ``source`` may be a path or the raw bytes of the file. Unicode is
    preserved verbatim, so Cyrillic text survives unchanged. Paragraphs that
    are empty or whitespace-only are dropped, since Word uses them purely for
    vertical spacing.

    Raises :class:`DocxError` for anything that is not a readable DOCX.
    """
    document_xml = _read_document_part(source)

    try:
        root = ElementTree.fromstring(document_xml)
    except ElementTree.ParseError as error:
        raise DocxError(f"DOCX document.xml is not valid XML: {error}") from error

    paragraphs: list[str] = []
    # .iter() walks in document order, including paragraphs nested in tables.
    for paragraph in root.iter(_PARAGRAPH_TAG):
        text = _paragraph_text(paragraph)
        if text:
            paragraphs.append(text)
    return tuple(paragraphs)


def _read_document_part(source: Path | str | bytes) -> bytes:
    """Pull ``word/document.xml`` out of the DOCX container."""
    try:
        if isinstance(source, bytes):
            import io

            archive = zipfile.ZipFile(io.BytesIO(source))
        else:
            archive = zipfile.ZipFile(Path(source))
    except zipfile.BadZipFile as error:
        raise DocxError("File is not a DOCX document (not a ZIP archive)") from error
    except OSError as error:
        raise DocxError(f"Could not open DOCX file: {error}") from error

    with archive:
        try:
            return archive.read(DOCUMENT_PART)
        except KeyError as error:
            raise DocxError(
                f"File is not a DOCX document (no {DOCUMENT_PART} inside)"
            ) from error


def _paragraph_text(paragraph: ElementTree.Element) -> str:
    """Join every text run of one paragraph into a single string."""
    pieces: list[str] = []
    for node in paragraph.iter():
        if node.tag == _TEXT_TAG:
            if node.text:
                pieces.append(node.text)
        elif node.tag in (_TAB_TAG, _BREAK_TAG):
            # Tabs and soft breaks separate words; keep them as whitespace so
            # a reference and its description do not run together.
            pieces.append(" ")
    return "".join(pieces).strip()
