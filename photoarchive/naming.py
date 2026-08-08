"""Small filename and text helpers shared across layers.

These live outside both :mod:`photoarchive.parsing` and
:mod:`photoarchive.scanning` so that neither package has to import the other:
parsing turns documents into entries, scanning matches and reports them, and
both need the same notion of "a filename without its extension".
"""

from __future__ import annotations


def filename_stem(filename: str) -> str:
    """Return a filename without its extension."""
    stem, dot, _ = filename.rpartition(".")
    return stem if dot else filename


def split_fragments(text: str) -> list[str]:
    """Split raw text into fragments, one per non-empty line."""
    return [line.strip() for line in text.splitlines() if line.strip()]
