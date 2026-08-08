"""Dictionary entities: People, Places, Tags, and the evidence behind them.

Two ideas run through this module:

* **Confirmed knowledge and candidates are different things.** A confirmed
  alias is a fact the pipeline may act on; a candidate is a hint that must be
  reviewed first. They are never merged silently.
* **Nothing is believed without provenance.** Every alias and every coordinate
  carries :class:`Evidence` recording where it came from, so "why does the
  system think this?" always has an answer. Evidence is kept when a candidate
  is promoted, not discarded.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from photoarchive.geo import LatLon


class EntityType(str, Enum):
    """Which dictionary an entry belongs to."""

    PERSON = "person"
    PLACE = "place"
    TAG = "tag"


class ConfidenceStatus(str, Enum):
    """How much the pipeline trusts a piece of dictionary knowledge."""

    CONFIRMED = "CONFIRMED"
    CANDIDATE = "CANDIDATE"
    REJECTED = "REJECTED"


class EvidenceReason(str, Enum):
    """Why a piece of knowledge was proposed."""

    MANUAL_CORRECTION = "manual correction"
    APPROVED_VS_SOURCE = "approved metadata vs source text"
    EXACT_CANONICAL_MATCH = "exact canonical match"
    EXACT_ALIAS_MATCH = "exact confirmed alias match"
    FUZZY_CANDIDATE = "fuzzy candidate"
    APPROVED_COORDINATES = "approved place coordinates"
    COORDINATE_CONFLICT = "coordinate conflict"


@dataclass(frozen=True, slots=True)
class Alias:
    """One spelling of an entity, with its confidence."""

    text: str
    status: ConfidenceStatus = ConfidenceStatus.CANDIDATE

    @property
    def is_confirmed(self) -> bool:
        return self.status is ConfidenceStatus.CONFIRMED


@dataclass(slots=True)
class Person:
    person_id: str
    canonical_name: str
    aliases: list[Alias] = field(default_factory=list)
    notes: str | None = None

    @property
    def confirmed_aliases(self) -> tuple[str, ...]:
        return tuple(alias.text for alias in self.aliases if alias.is_confirmed)

    @property
    def candidate_aliases(self) -> tuple[str, ...]:
        return tuple(
            alias.text
            for alias in self.aliases
            if alias.status is ConfidenceStatus.CANDIDATE
        )


@dataclass(slots=True)
class Place:
    place_id: str
    canonical_place: str
    aliases: list[Alias] = field(default_factory=list)
    #: Confirmed coordinates; only these are ever suggested to a reviewer.
    latlon: LatLon | None = None
    #: Proposed coordinates awaiting review, including genuine conflicts.
    candidate_latlon: list[LatLon] = field(default_factory=list)
    map_link: str | None = None
    notes: str | None = None

    @property
    def confirmed_aliases(self) -> tuple[str, ...]:
        return tuple(alias.text for alias in self.aliases if alias.is_confirmed)

    @property
    def candidate_aliases(self) -> tuple[str, ...]:
        return tuple(
            alias.text
            for alias in self.aliases
            if alias.status is ConfidenceStatus.CANDIDATE
        )


@dataclass(slots=True)
class Tag:
    tag_id: str
    canonical_tag: str
    aliases: list[Alias] = field(default_factory=list)
    notes: str | None = None

    @property
    def confirmed_aliases(self) -> tuple[str, ...]:
        return tuple(alias.text for alias in self.aliases if alias.is_confirmed)

    @property
    def candidate_aliases(self) -> tuple[str, ...]:
        return tuple(
            alias.text
            for alias in self.aliases
            if alias.status is ConfidenceStatus.CANDIDATE
        )


@dataclass(frozen=True, slots=True)
class Evidence:
    """Why the pipeline believes an alias or coordinate belongs to an entity."""

    entity_type: EntityType
    entity_value: str
    reason: EvidenceReason
    candidate_text: str | None = None
    proposed_latlon: LatLon | None = None
    source_root: str | None = None
    source_folder: str | None = None
    reference: str | None = None
    source_description: str | None = None
    section_context: str | None = None
    run_id: str | None = None
    status: ConfidenceStatus = ConfidenceStatus.CANDIDATE
    created_at: datetime | None = None


@dataclass(slots=True)
class Dictionary:
    """An in-memory snapshot used for matching during a scan."""

    people: list[Person] = field(default_factory=list)
    places: list[Place] = field(default_factory=list)
    tags: list[Tag] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not (self.people or self.places or self.tags)

    def place_by_canonical(self, canonical: str) -> Place | None:
        folded = canonical.casefold()
        for place in self.places:
            if place.canonical_place.casefold() == folded:
                return place
        return None
