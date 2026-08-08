"""Parsing unstructured folder description files.

A description file is free-form text written by a person, one per folder, and
by default it describes only the photos directly in that folder. Parsing turns
it into fragments that the matcher can attribute to individual photos.

No LLM integration here — the baseline is deterministic line splitting. A
future smarter parser only has to satisfy :class:`DescriptionParser`.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Protocol

from photoarchive.scanning.matcher import split_fragments


@dataclass(frozen=True, slots=True)
class ParsedDescription:
    """Result of parsing one folder description file."""

    raw_text: str
    fragments: tuple[str, ...] = ()

    @property
    def content_hash(self) -> str:
        """Stable hash of the source text, used to detect description changes."""
        return description_hash(self.raw_text)


class DescriptionParser(Protocol):
    """Strategy for turning description text into per-photo candidates."""

    def parse(self, text: str) -> ParsedDescription:
        """Split raw description text into attributable fragments."""
        ...


class PlainTextDescriptionParser:
    """Baseline parser: one fragment per non-empty line."""

    def parse(self, text: str) -> ParsedDescription:
        return ParsedDescription(raw_text=text, fragments=tuple(split_fragments(text)))


def description_hash(text: str) -> str:
    """Hash description text so a changed description can be surfaced.

    Line endings are normalised so that a pure CRLF/LF change is not reported
    as an edit.
    """
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()
