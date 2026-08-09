"""Deciding whether two edited values *mean* the same thing.

The three-way merge must not manufacture a conflict out of superficial
formatting: ``"Тоня, Мама"`` and ``"Мама; Тоня"`` are the same two people.
But it must never paper over a genuine disagreement either — two different
coordinates, two different place spellings, two different statuses stay
different no matter how they are normalised.

This module is the one place that decides what "the same" means for each
field, reusing the domain's existing parsers (:mod:`photoarchive.geo`,
:func:`~photoarchive.review.model.split_list_field`) rather than inventing a
second copy of that logic. The three-way algorithm in
:mod:`photoarchive.merge.threeway` stays a simple ``semantic_equal(a, b)``
comparison; it never normalises a value itself, and it never stores a
normalised value — normalisation exists only to answer "equal?", not to
decide what gets displayed or written.

Field policies, briefly:

======================  ============================================
kind                    normalisation
======================  ============================================
TEXT                    whitespace collapsed; nothing else
PROSE                   line endings unified; whitespace preserved
LIST (People/Tags/
Albums)                 split on ``,``/``;``, casefolded, order-free
ALIAS_SET (catalog      split on ``;``, order-free, case preserved
confirmed/candidate                                  (matches the
aliases)                                              catalog importer)
LATLON                  canonical ``lat, lon`` text when parseable
LATLON_SET (catalog     split on ``;``, each item canonicalised
candidate_latlon)                                    independently, order-free
OPAQUE (Status)         exact value once blank is normalised
======================  ============================================

Date is deliberately TEXT: normalising *display* trivia (spacing) is fine,
but turning ``"1979"`` and ``"1979-05"`` into the same thing would erase a
real precision difference, so nothing beyond whitespace is touched.

Place is deliberately TEXT too: ``Place`` is the human's final word, and the
catalog's dictionary aliases must never silently decide two different
spellings mean the same place — that is exactly the ownership split between
``Place`` (human) and ``Suggested Place`` (machine) the review workbook keeps.
"""

from __future__ import annotations

from collections.abc import Hashable
from enum import Enum

from photoarchive.geo import parse_latlon
from photoarchive.review.model import split_list_field, split_values


class Kind(str, Enum):
    TEXT = "text"
    PROSE = "prose"
    LIST = "list"
    ALIAS_SET = "alias_set"
    LATLON = "latlon"
    LATLON_SET = "latlon_set"
    OPAQUE = "opaque"


#: Field name -> comparison policy. Deliberately keyed by field name rather
#: than by (artifact, field): no field name is used with two different
#: meanings across ``review.xlsx`` and ``catalog.xlsx``, so one registry
#: covers both. ``artifact`` stays part of the public signature below so a
#: future field-name collision with different semantics has somewhere to go
#: without changing callers.
_KIND_BY_FIELD: dict[str, Kind] = {
    "date": Kind.TEXT,
    "place": Kind.TEXT,
    "canonical_name": Kind.TEXT,
    "canonical_place": Kind.TEXT,
    "canonical_tag": Kind.TEXT,
    "event": Kind.TEXT,
    "map_link": Kind.TEXT,
    "latlon": Kind.LATLON,
    "candidate_latlon": Kind.LATLON_SET,
    "people": Kind.LIST,
    "tags": Kind.LIST,
    "albums": Kind.LIST,
    "confirmed_aliases": Kind.ALIAS_SET,
    "candidate_aliases": Kind.ALIAS_SET,
    "description": Kind.PROSE,
    "notes": Kind.PROSE,
    "status": Kind.OPAQUE,
}


def _normalize_text(value: str | None) -> str:
    """Whitespace-insensitive scalar text: blank, spacing, line breaks fold away."""
    text = "" if value is None else str(value)
    return " ".join(text.split())


def _normalize_prose(value: str | None) -> str:
    """Prose stays prose: only line-ending trivia and outer blank are folded."""
    text = "" if value is None else str(value)
    return text.replace("\r\n", "\n").replace("\r", "\n").strip()


def _normalize_list(value: str | None) -> tuple[str, ...]:
    """People / Tags / Albums: an unordered, case-folded set of members."""
    text = "" if value is None else str(value)
    return tuple(sorted({item.casefold() for item in split_list_field(text)}))


def _normalize_alias_set(value: str | None) -> tuple[str, ...]:
    """Catalog alias collections: semicolon-separated, order-free, case kept.

    Matches :func:`photoarchive.catalog.importer._sync_aliases`, which compares
    aliases as an exact-text set rather than case-folding them.
    """
    text = "" if value is None else str(value)
    return tuple(sorted(set(split_values(text))))


def _normalize_latlon(value: str | None) -> str:
    """Canonical coordinate text when parseable; whitespace-only text otherwise.

    An unparsable value (free text, a half-typed cell) is never silently
    equated with another unparsable value beyond ignoring formatting — two
    genuinely different coordinates always normalise to different text.
    """
    text = "" if value is None else str(value).strip()
    if not text:
        return ""
    point = parse_latlon(text)
    if point is not None:
        return point.format()
    return _normalize_text(text)


def _normalize_latlon_set(value: str | None) -> tuple[str, ...]:
    """Catalog candidate coordinates: an unordered set, each item canonicalised.

    Split with the same separator as any other catalog collection
    (:func:`~photoarchive.review.model.split_values`), then each item is
    normalised independently through :func:`_normalize_latlon`: a parseable
    coordinate becomes its canonical text regardless of spacing, and an
    unparsable item is kept — never dropped — with only whitespace touched,
    so two different unparsable values still compare different.
    """
    text = "" if value is None else str(value)
    return tuple(sorted({_normalize_latlon(item) for item in split_values(text)}))


def _normalize_opaque(value: str | None) -> str:
    """Exact value, once blank is normalised. No case-folding, no collapsing."""
    text = "" if value is None else str(value)
    return text.strip()


_NORMALIZERS = {
    Kind.TEXT: _normalize_text,
    Kind.PROSE: _normalize_prose,
    Kind.LIST: _normalize_list,
    Kind.ALIAS_SET: _normalize_alias_set,
    Kind.LATLON: _normalize_latlon,
    Kind.LATLON_SET: _normalize_latlon_set,
    Kind.OPAQUE: _normalize_opaque,
}


def normalize_for_comparison(artifact: str, field: str, value: str | None) -> Hashable:
    """The value a merge may compare, never the value it may display or write.

    ``artifact`` (``"review"`` / ``"catalog"``) is accepted for callers that
    have it and for a future field-name collision, but no field currently
    needs it: field names do not carry different meanings across artifacts.
    """
    del artifact  # reserved; see module and registry docstrings
    kind = _KIND_BY_FIELD.get(field, Kind.TEXT)
    return _NORMALIZERS[kind](value)


def semantic_equal(artifact: str, field: str, first: str | None, second: str | None) -> bool:
    """Whether two raw values mean the same thing for this field."""
    return normalize_for_comparison(artifact, field, first) == normalize_for_comparison(
        artifact, field, second
    )


def semantic_baselines_equal(first, second) -> bool:
    """Whether two whole baselines agree on every record and human field.

    Used to recognise a first sync where both copies were produced
    independently but agree — e.g. the same content packaged into two
    byte-different ``.xlsx`` files — so it need not become a conflict.
    """
    if set(first.records) != set(second.records):
        return False
    artifact = first.artifact or second.artifact
    for record_id, a in first.records.items():
        b = second.records[record_id]
        for name in set(a.fields) | set(b.fields):
            if not semantic_equal(artifact, name, a.value(name), b.value(name)):
                return False
    return True
