"""Deciding whether a processed photo needs rebuilding.

A build fingerprint answers one question: *would building this again produce
the same file?* It therefore depends on exactly three things —

* the source photo's content hash;
* the final, human-owned metadata the build writes;
* the build mapping version;

— and on nothing else. Timestamps, machine names and run ids are deliberately
excluded: they change every run, and including them would make every photo look
stale on every machine. Those belong in provenance, which records *what
happened*, next to the fingerprint, which records *what the content is*.

Because the fingerprint is content-derived, a clean machine that has just
bootstrapped from portable state can tell which photos are already built
without rebuilding anything to find out.
"""

from __future__ import annotations

import hashlib
import json

from photoarchive.portable.models import BUILD_VERSION
from photoarchive.review.model import ReviewRow, split_list_field

#: Final fields whose values reach the built file's metadata. Changing this
#: tuple changes what a build means — bump ``BUILD_VERSION`` when it happens.
BUILD_FIELDS: tuple[str, ...] = (
    "date",
    "place",
    "latlon",
    "people",
    "tags",
    "description",
    "event",
    "albums",
)

#: Fields whose order a reviewer should not have to care about.
_LIST_FIELDS = frozenset({"people", "tags", "albums"})


def normalize_field(name: str, value: str | None) -> str | list[str]:
    """Normalise one final value so cosmetic edits do not force a rebuild.

    List fields are split and sorted, so ``"Аня, Тоня"`` and ``"Тоня; Аня"``
    describe the same photo and yield the same fingerprint.
    """
    text = (value or "").strip()
    if name in _LIST_FIELDS:
        return sorted(item.casefold() for item in split_list_field(text))
    return " ".join(text.split())


def build_metadata(row: ReviewRow) -> dict[str, str | list[str]]:
    """The normalised metadata a build would write for one row."""
    return {name: normalize_field(name, getattr(row, name, "")) for name in BUILD_FIELDS}


def build_fingerprint(
    row: ReviewRow, source_hash: str | None, build_version: int = BUILD_VERSION
) -> str:
    """Return the deterministic fingerprint of a processed photo.

    Same source plus same final metadata plus same build version yields the
    same fingerprint, on any machine, in any order.
    """
    payload = {
        "source_hash": source_hash or "",
        "metadata": build_metadata(row),
        "build_version": build_version,
    }
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def needs_rebuild(
    row: ReviewRow,
    source_hash: str | None,
    recorded_fingerprint: str | None,
    build_version: int = BUILD_VERSION,
) -> bool:
    """True when the built artifact would differ from what was recorded."""
    if not recorded_fingerprint:
        return True
    return build_fingerprint(row, source_hash, build_version) != recorded_fingerprint
