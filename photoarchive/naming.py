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


#: Characters a destination folder name must not contain.
_UNSAFE_NAME_CHARACTERS = str.maketrans(
    {"/": "-", "\\": "-", "\n": " ", "\r": " ", "\t": " "}
)

FALLBACK_ROOT_NAME = "source"


def sanitize_folder_name(name: str) -> str:
    """Make a source folder name safe to use as a single destination folder.

    Path separators would otherwise split one source root across several
    destination folders, so they are replaced rather than removed. An empty or
    whitespace-only name falls back to :data:`FALLBACK_ROOT_NAME`, since a
    source root must always land in a folder of its own.
    """
    cleaned = name.translate(_UNSAFE_NAME_CHARACTERS).strip().strip(".")
    cleaned = " ".join(cleaned.split())
    return cleaned or FALLBACK_ROOT_NAME
