"""Resolving a place *value* against the confirmed Places dictionary.

``Place`` and ``LatLon`` are one linked concept: coordinates are an attribute
of a canonical Place, never an independent fact. So any row that names a place
the dictionary knows can be given that place's coordinates — whether the name
arrived from the source text or from a reviewer typing it by hand.

That second route matters: a folder with no DOCX has no source description to
match against, so the only thing identifying the location is the ``Place`` the
reviewer typed. Resolving it here is what lets coordinates propagate anyway.

Resolution is exact, not fuzzy: whole-value equality against a canonical name
or a **confirmed** alias, compared normalized. Candidate aliases never resolve
— a hint must not silently supply coordinates. If two distinct places match,
the result is ambiguous and yields nothing rather than guessing.
"""

from __future__ import annotations

from dataclasses import dataclass

from photoarchive.catalog.matching import normalize
from photoarchive.catalog.models import Dictionary, Place
from photoarchive.geo import LatLon


@dataclass(frozen=True, slots=True)
class PlaceResolution:
    """The outcome of looking a place value up in the dictionary."""

    place: Place | None = None
    ambiguous_matches: tuple[str, ...] = ()

    @property
    def resolved(self) -> bool:
        return self.place is not None and not self.ambiguous_matches

    @property
    def is_ambiguous(self) -> bool:
        return bool(self.ambiguous_matches)

    @property
    def canonical(self) -> str:
        return self.place.canonical_place if self.place else ""

    @property
    def latlon(self) -> LatLon | None:
        """Confirmed coordinates only; candidates are never returned."""
        return self.place.latlon if self.place else None


def resolve_place(dictionary: Dictionary, value: str | None) -> PlaceResolution:
    """Resolve a place value to exactly one confirmed dictionary entry.

    Matching order: exact canonical, then confirmed alias, then the same two
    compared normalized (case and whitespace folded). Anything matching two
    different places is reported as ambiguous.
    """
    text = (value or "").strip()
    if not text:
        return PlaceResolution()

    target = normalize(text)
    matches: list[Place] = []

    for place in dictionary.places:
        if normalize(place.canonical_place) == target:
            matches.append(place)
            continue
        if any(normalize(alias) == target for alias in place.confirmed_aliases):
            matches.append(place)

    distinct = {place.place_id: place for place in matches}
    if len(distinct) == 1:
        return PlaceResolution(place=next(iter(distinct.values())))
    if len(distinct) > 1:
        return PlaceResolution(
            ambiguous_matches=tuple(
                sorted(place.canonical_place for place in distinct.values())
            )
        )
    return PlaceResolution()


def coordinates_for_place(dictionary: Dictionary, value: str | None) -> str:
    """Return a place's confirmed coordinates in canonical text form, or ``""``."""
    resolution = resolve_place(dictionary, value)
    point = resolution.latlon
    return point.format() if point is not None else ""
