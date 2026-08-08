"""The global ``catalog.xlsx`` knowledge base.

The catalog lives at the Google Drive archive root (one per archive, not one
per folder) and holds three sheets:

* **People** — stable id, canonical display name, aliases, relationship notes,
  default Google Photos album. Ambiguous aliases such as ``mom`` must be
  confirmed by a human before they resolve to a person.
* **Places** — stable id, canonical name, historical/display names, aliases,
  latitude, longitude, geographic precision, confirmation status.
* **Tags** — a *controlled* vocabulary (``school``, ``dacha``, ``birthday``,
  ``travel``, ``New Year``), not every noun found in a description.

It is populated from **approved** review rows, after human correction — the
``learn`` command — and then feeds better proposals for the next folder. The
first scan works fine with an empty or absent catalog.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from photoarchive.models import PhotoReviewRecord

CATALOG_SHEETS: tuple[str, ...] = ("People", "Places", "Tags")

PEOPLE_COLUMNS: tuple[str, ...] = (
    "person_id",
    "display_name",
    "aliases",
    "relationship",
    "default_album",
    "notes",
)

PLACES_COLUMNS: tuple[str, ...] = (
    "place_id",
    "canonical_name",
    "historical_names",
    "aliases",
    "latitude",
    "longitude",
    "precision",
    "confirmed",
)

TAGS_COLUMNS: tuple[str, ...] = ("tag", "aliases", "notes")


@dataclass(frozen=True, slots=True)
class Person:
    person_id: str
    display_name: str
    aliases: tuple[str, ...] = ()
    relationship: str | None = None
    default_album: str | None = None
    notes: str | None = None


@dataclass(frozen=True, slots=True)
class Place:
    place_id: str
    canonical_name: str
    historical_names: tuple[str, ...] = ()
    aliases: tuple[str, ...] = ()
    latitude: float | None = None
    longitude: float | None = None
    precision: str | None = None
    confirmed: bool = False


@dataclass(frozen=True, slots=True)
class Tag:
    tag: str
    aliases: tuple[str, ...] = ()
    notes: str | None = None


@dataclass(slots=True)
class Catalog:
    """In-memory view of the catalog, used as proposal context."""

    people: list[Person] = field(default_factory=list)
    places: list[Place] = field(default_factory=list)
    tags: list[Tag] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not (self.people or self.places or self.tags)


class CatalogService:
    """Loads and grows the archive-wide catalog.

    Like the review service, this works on a local copy; upload and download
    are the caller's job.
    """

    def __init__(self, filename: str = "catalog.xlsx") -> None:
        self.filename = filename

    def load(self, path: Path) -> Catalog:
        """Read the catalog workbook.

        Returns an empty catalog when the file does not exist yet: the first
        scan must not require a populated catalog.

        TODO: implement with openpyxl over ``CATALOG_SHEETS``.
        """
        raise NotImplementedError("CatalogService.load is not implemented yet")

    def learn(self, catalog: Catalog, approved: list[PhotoReviewRecord]) -> Catalog:
        """Fold approved review rows into the catalog.

        TODO: only ``APPROVED`` rows contribute; new people/places/tags are
        added with stable ids, and ambiguous aliases are flagged for
        confirmation instead of being merged automatically.
        """
        raise NotImplementedError("CatalogService.learn is not implemented yet")

    def save(self, catalog: Catalog, path: Path) -> Path:
        """Write the catalog workbook back out.

        TODO: implement with openpyxl, preserving sheets and columns this code
        does not know about.
        """
        raise NotImplementedError("CatalogService.save is not implemented yet")
