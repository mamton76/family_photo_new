"""The suggestion layer: conservative, explainable, never a guess.

Suggestions seed a review row; a human decides what is true. So the bar here is
deliberately high — an empty ``Suggested Place`` costs a reviewer one typed
word, while a wrong one costs them the trust to skim the column at all.

Sources allowed:

* the entry's own description text;
* its inherited section context, for people only;
* **confirmed** dictionary entries — canonical names and confirmed aliases;
* confirmed coordinates of a matched place.

Explicitly not done here: birth years from ages, fuzzy name resolution,
free-text place detection, and anything derived from candidate aliases.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from photoarchive.catalog.matching import (
    PhraseMatch,
    candidate_matches,
    confirmed_canonicals,
    find_matches,
)
from photoarchive.catalog.models import Dictionary, EntityType
from photoarchive.geo import LatLon

#: Russian month names, nominative and genitive, mapped to month numbers.
_MONTHS: dict[str, int] = {
    "январь": 1, "января": 1, "февраль": 2, "февраля": 2,
    "март": 3, "марта": 3, "апрель": 4, "апреля": 4,
    "май": 5, "мая": 5, "июнь": 6, "июня": 6,
    "июль": 7, "июля": 7, "август": 8, "августа": 8,
    "сентябрь": 9, "сентября": 9, "октябрь": 10, "октября": 10,
    "ноябрь": 11, "ноября": 11, "декабрь": 12, "декабря": 12,
}

#: A four-digit year in a plausible photographic range.
_YEAR_RE = re.compile(r"\b(18\d\d|19\d\d|20\d\d)\b")

_MONTH_RE = re.compile(r"\b([А-Яа-яЁё]+)\b")
_DAY_RE = re.compile(r"\b([0-3]?\d)\b")


@dataclass(frozen=True, slots=True)
class Suggestion:
    """Machine-proposed values for one review row."""

    date: str = ""
    place: str = ""
    latlon: str = ""
    people: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    #: Weak hints found while matching; reported, never suggested.
    candidates: tuple[PhraseMatch, ...] = field(default=())
    #: Set when several confirmed places matched and none could safely win.
    ambiguous_places: tuple[str, ...] = ()
    #: How many confirmed matches each dictionary contributed.
    people_matched: int = 0
    places_matched: int = 0
    tags_matched: int = 0
    coordinates_reused: int = 0


def suggest_date(text: str) -> str:
    """Extract a date from description text, as much of it as is stated.

    The archive writes dates as ``1979.``, ``1980 июнь.`` or
    ``1979 февраль 19.``, so the year anchors the search and the month and day
    are only read from the words immediately following it. Output is partial
    ISO: ``1979``, ``1979-06``, ``1979-02-19``.

    Nothing is inferred: no year means no suggestion.
    """
    if not text:
        return ""

    year_match = _YEAR_RE.search(text)
    if not year_match:
        return ""

    year = year_match.group(1)
    # Look only just past the year; a month far away belongs to another clause.
    tail = text[year_match.end() : year_match.end() + 24]

    month_match = _MONTH_RE.search(tail)
    if not month_match:
        return year

    month = _MONTHS.get(month_match.group(1).casefold())
    if month is None:
        return year

    day_tail = tail[month_match.end() : month_match.end() + 6]
    day_match = _DAY_RE.search(day_tail)
    if not day_match:
        return f"{year}-{month:02d}"

    day = int(day_match.group(1))
    if not 1 <= day <= 31:
        return f"{year}-{month:02d}"
    return f"{year}-{month:02d}-{day:02d}"


def suggest(
    text: str,
    dictionary: Dictionary,
    section_context: str | None = None,
) -> Suggestion:
    """Build a conservative suggestion for one description entry."""
    people_matches = find_matches(text, dictionary, EntityType.PERSON)
    if section_context:
        # Section headings name people ("Далее Аня или Тоня"), not places.
        people_matches += find_matches(section_context, dictionary, EntityType.PERSON)

    place_matches = find_matches(text, dictionary, EntityType.PLACE)
    tag_matches = find_matches(text, dictionary, EntityType.TAG)

    people = confirmed_canonicals(people_matches)
    places = confirmed_canonicals(place_matches)
    tags = confirmed_canonicals(tag_matches)

    # Two distinct confirmed places in one description is a genuine ambiguity.
    # Picking the first would silently attach the wrong location — and its
    # coordinates — so the suggestion is left empty for a human to resolve.
    ambiguous = len(places) > 1
    place = places[0] if len(places) == 1 else ""
    latlon, reused = ("", 0) if ambiguous else _coordinates_for(places, dictionary)

    return Suggestion(
        date=suggest_date(text),
        place=place,
        latlon=latlon,
        ambiguous_places=tuple(places) if ambiguous else (),
        people=tuple(people),
        tags=tuple(tags),
        candidates=tuple(
            candidate_matches(people_matches)
            + candidate_matches(place_matches)
            + candidate_matches(tag_matches)
        ),
        people_matched=len(people),
        places_matched=len(places),
        tags_matched=len(tags),
        coordinates_reused=reused,
    )


def _coordinates_for(places: list[str], dictionary: Dictionary) -> tuple[str, int]:
    """Reuse a matched place's confirmed coordinates, if it has any.

    Candidate coordinates are deliberately ignored: an unreviewed guess must
    not reach a reviewer's map link as though it were established.
    """
    for canonical in places:
        place = dictionary.place_by_canonical(canonical)
        if place is not None and isinstance(place.latlon, LatLon):
            return place.latlon.format(), 1
    return "", 0
