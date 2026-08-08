"""Associating fragments of a shared folder description with photo filenames.

Everything in this module is pure: it takes filenames and text fragments and
returns an association. No cloud APIs, no filesystem, so it is fully testable
offline.

The baseline strategy is explicit filename references, which people write in
several shapes — the reference is *not* required to start the line::

    020.jpg — Tonya and Anya
    Photo 021.jpg: the boat pier
    Tonya and Anya (022.jpg)

A fragment that names no file belongs to the folder as a whole and is returned
as shared context rather than being guessed onto an arbitrary photo.

Case-insensitive matching uses :meth:`str.lower`, not :meth:`str.casefold`,
because the match offsets are used to slice the *original* fragment and
casefold can change a string's length (``ß`` → ``ss``), which would misalign
those offsets.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Protocol

from photoarchive.naming import filename_stem, split_fragments

__all__ = [
    "DescriptionMatch",
    "DescriptionMatcher",
    "FilenameReference",
    "FilenameReferenceMatcher",
    "MatchResult",
    "filename_stem",
    "find_referenced_filename",
    "locate_reference",
    "match_fragments_to_photos",
    "split_fragments",
    "strip_reference",
]

#: Characters trimmed from the edges of a fragment once the filename
#: reference is removed: whitespace, dashes, punctuation and brackets.
_EDGE_CHARACTERS = " \t -–—:;.,!?)]}([{«»\"'`"


@dataclass(frozen=True, slots=True)
class FilenameReference:
    """Where a fragment refers to a photo, and to which one."""

    filename: str
    start: int
    end: int


@dataclass(frozen=True, slots=True)
class DescriptionMatch:
    """Fragments attributed to one photo."""

    filename: str
    fragments: tuple[str, ...] = ()

    @property
    def text(self) -> str:
        return " ".join(self.fragments).strip()


@dataclass(frozen=True, slots=True)
class MatchResult:
    """Outcome of matching one folder description against its photos."""

    matches: tuple[DescriptionMatch, ...] = ()
    shared_fragments: tuple[str, ...] = field(default=())

    def text_for(self, filename: str) -> str:
        """Return the description text attributed to one photo."""
        for match in self.matches:
            if match.filename == filename:
                return match.text
        return ""


class DescriptionMatcher(Protocol):
    """Strategy for attributing description fragments to photos."""

    def match(self, filenames: Sequence[str], fragments: Sequence[str]) -> MatchResult:
        """Attribute each fragment to a photo, or to the folder as a whole."""
        ...




def locate_reference(
    fragment: str, filenames: Iterable[str]
) -> FilenameReference | None:
    """Find where a fragment refers to one of ``filenames``, if at all.

    The reference may sit anywhere in the fragment. A full filename match wins
    over a bare stem match, and longer candidates win over shorter ones so
    that ``021`` is not swallowed by ``02``. Stem matches require word
    boundaries, so ``020`` does not match inside ``1020``.
    """
    lowered = fragment.lower()
    candidates = list(filenames)

    for filename in sorted(candidates, key=len, reverse=True):
        index = lowered.find(filename.lower())
        if index != -1:
            return FilenameReference(filename, index, index + len(filename))

    by_stem_length = sorted(
        candidates, key=lambda name: len(filename_stem(name)), reverse=True
    )
    for filename in by_stem_length:
        stem = filename_stem(filename)
        if not stem:
            continue
        match = re.search(rf"(?<!\w){re.escape(stem.lower())}(?!\w)", lowered)
        if match:
            return FilenameReference(filename, match.start(), match.end())
    return None


def find_referenced_filename(fragment: str, filenames: Iterable[str]) -> str | None:
    """Return the photo a fragment explicitly refers to, if any."""
    reference = locate_reference(fragment, filenames)
    return reference.filename if reference else None


def strip_reference(fragment: str, reference: FilenameReference) -> str:
    """Return the description text of a fragment, minus the filename itself.

    The text *after* the reference wins, since that is where descriptions
    normally sit (``020.jpg — Tonya``). When nothing follows, the text before
    it is used instead, which handles trailing references
    (``Tonya and Anya (020.jpg)``). A fragment that is *only* a filename
    yields an empty string rather than the filename repeated back.
    """
    after = fragment[reference.end :].strip(_EDGE_CHARACTERS)
    if after:
        return after
    return fragment[: reference.start].strip(_EDGE_CHARACTERS)


def match_fragments_to_photos(
    filenames: Sequence[str],
    fragments: Sequence[str],
) -> MatchResult:
    """Attribute fragments to photos by explicit filename reference.

    Fragments that reference no filename are returned in ``shared_fragments``
    and describe the folder rather than a single photo. Every photo appears in
    the result, including those with no fragment of their own.
    """
    attributed: dict[str, list[str]] = {name: [] for name in filenames}
    shared: list[str] = []

    for fragment in fragments:
        reference = locate_reference(fragment, filenames)
        if reference is None:
            shared.append(fragment)
            continue
        remainder = strip_reference(fragment, reference)
        if remainder:
            attributed[reference.filename].append(remainder)

    matches = tuple(
        DescriptionMatch(filename=name, fragments=tuple(attributed[name]))
        for name in filenames
    )
    return MatchResult(matches=matches, shared_fragments=tuple(shared))


class FilenameReferenceMatcher:
    """Default :class:`DescriptionMatcher` built on explicit references."""

    def match(self, filenames: Sequence[str], fragments: Sequence[str]) -> MatchResult:
        return match_fragments_to_photos(filenames, fragments)
