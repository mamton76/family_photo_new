"""SQLite storage for the People / Places / Tags dictionaries and evidence.

Aliases and candidate coordinates live in their own normalised tables rather
than as list columns, so a candidate can be promoted, rejected or counted
without rewriting a blob. Evidence rows are append-only: promoting a candidate
adds a confirmation, it never deletes the reasoning that led there.
"""

from __future__ import annotations

import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from photoarchive.catalog.models import (
    Alias,
    ConfidenceStatus,
    Dictionary,
    EntityType,
    Evidence,
    EvidenceReason,
    Person,
    Place,
    Tag,
)
from photoarchive.geo import LatLon, is_materially_different, parse_latlon

DEFAULT_DICTIONARY_PATH = Path("archive.sqlite")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS people (
    person_id      TEXT PRIMARY KEY,
    canonical_name TEXT NOT NULL UNIQUE,
    notes          TEXT
);

CREATE TABLE IF NOT EXISTS places (
    place_id        TEXT PRIMARY KEY,
    canonical_place TEXT NOT NULL UNIQUE,
    latlon          TEXT,
    map_link        TEXT,
    notes           TEXT
);

CREATE TABLE IF NOT EXISTS tags (
    tag_id        TEXT PRIMARY KEY,
    canonical_tag TEXT NOT NULL UNIQUE,
    notes         TEXT
);

-- One alias row per (entity, spelling). Status separates fact from hint.
CREATE TABLE IF NOT EXISTS aliases (
    id          INTEGER PRIMARY KEY,
    entity_type TEXT NOT NULL,
    entity_id   TEXT NOT NULL,
    alias       TEXT NOT NULL,
    status      TEXT NOT NULL,
    UNIQUE (entity_type, entity_id, alias)
);

-- Proposed coordinates for a place, including conflicts with a confirmed one.
CREATE TABLE IF NOT EXISTS place_latlon_candidates (
    id       INTEGER PRIMARY KEY,
    place_id TEXT NOT NULL REFERENCES places(place_id) ON DELETE CASCADE,
    latlon   TEXT NOT NULL,
    status   TEXT NOT NULL,
    UNIQUE (place_id, latlon)
);

-- Append-only provenance.
CREATE TABLE IF NOT EXISTS evidence (
    id                 INTEGER PRIMARY KEY,
    entity_type        TEXT NOT NULL,
    entity_value       TEXT NOT NULL,
    reason             TEXT NOT NULL,
    candidate_text     TEXT,
    proposed_latlon    TEXT,
    source_root        TEXT,
    source_folder      TEXT,
    reference          TEXT,
    source_description TEXT,
    section_context    TEXT,
    run_id             TEXT,
    status             TEXT NOT NULL,
    created_at         TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_aliases_entity ON aliases(entity_type, entity_id);
CREATE INDEX IF NOT EXISTS idx_evidence_entity ON evidence(entity_type, entity_value);

-- Evidence is append-only but not duplicate-prone: re-learning from an
-- unchanged row must record the same fact once, not once per run. The run id
-- and timestamp are deliberately outside the key.
CREATE UNIQUE INDEX IF NOT EXISTS idx_evidence_unique ON evidence(
    entity_type, entity_value, reason,
    IFNULL(candidate_text, ''), IFNULL(proposed_latlon, ''),
    IFNULL(source_root, ''), IFNULL(source_folder, ''), IFNULL(reference, '')
);
"""


#: ``entity type -> (table, id column, canonical-name column)``.
_TABLE_FOR: dict[EntityType, tuple[str, str, str]] = {
    EntityType.PERSON: ("people", "person_id", "canonical_name"),
    EntityType.PLACE: ("places", "place_id", "canonical_place"),
    EntityType.TAG: ("tags", "tag_id", "canonical_tag"),
}


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _utc_now() -> datetime:
    return datetime.now(tz=timezone.utc)


class DictionaryStore:
    """Reads and writes the dictionaries backed by SQLite."""

    def __init__(self, path: Path | str = DEFAULT_DICTIONARY_PATH) -> None:
        self.path = Path(path)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        """Create the dictionary schema. Safe to call repeatedly."""
        with self.connect() as connection:
            connection.executescript(_SCHEMA)

    # -- Writing ----------------------------------------------------------

    def add_person(self, canonical_name: str, notes: str | None = None) -> str:
        """Insert a person, or return the existing id for that canonical name."""
        return self._add_entity("people", "person_id", "canonical_name", canonical_name, notes, "person")

    def add_tag(self, canonical_tag: str, notes: str | None = None) -> str:
        return self._add_entity("tags", "tag_id", "canonical_tag", canonical_tag, notes, "tag")

    def add_place(
        self,
        canonical_place: str,
        latlon: LatLon | None = None,
        notes: str | None = None,
    ) -> str:
        place_id = self._add_entity(
            "places", "place_id", "canonical_place", canonical_place, notes, "place"
        )
        if latlon is not None:
            self.set_place_latlon(place_id, latlon)
        return place_id

    def _add_entity(
        self,
        table: str,
        id_column: str,
        name_column: str,
        value: str,
        notes: str | None,
        prefix: str,
    ) -> str:
        with self.connect() as connection:
            row = connection.execute(
                f"SELECT {id_column} FROM {table} WHERE {name_column} = ?", (value,)
            ).fetchone()
            if row is not None:
                return str(row[id_column])

            entity_id = _new_id(prefix)
            connection.execute(
                f"INSERT INTO {table} ({id_column}, {name_column}, notes) VALUES (?, ?, ?)",
                (entity_id, value, notes),
            )
            return entity_id

    def add_alias(
        self,
        entity_type: EntityType,
        entity_id: str,
        alias: str,
        status: ConfidenceStatus = ConfidenceStatus.CANDIDATE,
    ) -> None:
        """Add or upgrade an alias.

        A confirmed alias never degrades back to a candidate on a later pass.
        """
        with self.connect() as connection:
            existing = connection.execute(
                "SELECT status FROM aliases WHERE entity_type = ? AND entity_id = ? AND alias = ?",
                (entity_type.value, entity_id, alias),
            ).fetchone()

            if existing is None:
                connection.execute(
                    "INSERT INTO aliases (entity_type, entity_id, alias, status)"
                    " VALUES (?, ?, ?, ?)",
                    (entity_type.value, entity_id, alias, status.value),
                )
                return

            if (
                existing["status"] == ConfidenceStatus.CANDIDATE.value
                and status is ConfidenceStatus.CONFIRMED
            ):
                connection.execute(
                    "UPDATE aliases SET status = ?"
                    " WHERE entity_type = ? AND entity_id = ? AND alias = ?",
                    (status.value, entity_type.value, entity_id, alias),
                )

    def set_place_latlon(self, place_id: str, latlon: LatLon) -> None:
        """Set a place's confirmed coordinates."""
        with self.connect() as connection:
            connection.execute(
                "UPDATE places SET latlon = ?, map_link = ? WHERE place_id = ?",
                (latlon.format(), latlon.map_url, place_id),
            )

    def propose_place_latlon(self, place_id: str, latlon: LatLon) -> str:
        """Offer coordinates for a place and report what happened.

        Returns ``"added"`` when the place had none, ``"unchanged"`` when the
        proposal agrees with what is already confirmed, and ``"conflict"`` when
        it disagrees materially — in which case it is stored as a candidate for
        a human to resolve. Confirmed coordinates are never overwritten.

        The three outcomes are distinct so that repeated learning reports
        honestly: re-reading the same row is not a new coordinate.
        """
        with self.connect() as connection:
            row = connection.execute(
                "SELECT latlon FROM places WHERE place_id = ?", (place_id,)
            ).fetchone()
            confirmed = parse_latlon(row["latlon"]) if row else None

        if confirmed is None:
            self.set_place_latlon(place_id, latlon)
            return "added"

        if not is_materially_different(confirmed, latlon):
            return "unchanged"

        with self.connect() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO place_latlon_candidates (place_id, latlon, status)"
                " VALUES (?, ?, ?)",
                (place_id, latlon.format(), ConfidenceStatus.CANDIDATE.value),
            )
        return "conflict"

    def rename_entity(
        self, entity_type: EntityType, entity_id: str, canonical: str
    ) -> None:
        """Rename an entity in place, keeping its id, aliases and evidence.

        Renaming is an edit to one entity, not the birth of another.
        """
        table, id_column, name_column = _TABLE_FOR[entity_type]
        with self.connect() as connection:
            connection.execute(
                f"UPDATE {table} SET {name_column} = ? WHERE {id_column} = ?",
                (canonical, entity_id),
            )

    def merge_entities(
        self, entity_type: EntityType, keep_id: str, absorb_id: str
    ) -> str | None:
        """Fold one entity into another, keeping ``keep_id``.

        Used when two rows turn out to name the same thing — ``Дом на
        Днепропетровской`` and ``Дома на Днепропетровской``. The absorbed
        entity's canonical value becomes a confirmed alias of the survivor, its
        own aliases and evidence are re-pointed rather than dropped, and any
        coordinates it had are offered to the survivor (never overwriting a
        confirmed one). Returns the absorbed canonical value.
        """
        if keep_id == absorb_id:
            return None

        table, id_column, name_column = _TABLE_FOR[entity_type]
        with self.connect() as connection:
            row = connection.execute(
                f"SELECT {name_column} FROM {table} WHERE {id_column} = ?", (absorb_id,)
            ).fetchone()
            if row is None:
                return None
            absorbed_name = str(row[name_column])

            keep_row = connection.execute(
                f"SELECT {name_column} FROM {table} WHERE {id_column} = ?", (keep_id,)
            ).fetchone()
            if keep_row is None:
                return None
            survivor_name = str(keep_row[name_column])

            # Re-point the absorbed entity's aliases; drop only exact clashes.
            connection.execute(
                "UPDATE OR IGNORE aliases SET entity_id = ?"
                " WHERE entity_type = ? AND entity_id = ?",
                (keep_id, entity_type.value, absorb_id),
            )
            connection.execute(
                "DELETE FROM aliases WHERE entity_type = ? AND entity_id = ?",
                (entity_type.value, absorb_id),
            )
            # Evidence follows the surviving entity so its history is not lost.
            connection.execute(
                "UPDATE OR IGNORE evidence SET entity_value = ?"
                " WHERE entity_type = ? AND entity_value = ?",
                (survivor_name, entity_type.value, absorbed_name),
            )

        absorbed_point = None
        if entity_type is EntityType.PLACE:
            with self.connect() as connection:
                place = connection.execute(
                    "SELECT latlon FROM places WHERE place_id = ?", (absorb_id,)
                ).fetchone()
                absorbed_point = parse_latlon(place["latlon"]) if place else None

        with self.connect() as connection:
            connection.execute(f"DELETE FROM {table} WHERE {id_column} = ?", (absorb_id,))

        self.add_alias(entity_type, keep_id, absorbed_name, ConfidenceStatus.CONFIRMED)
        if absorbed_point is not None:
            self.propose_place_latlon(keep_id, absorbed_point)
        return absorbed_name

    def find_by_canonical(self, entity_type: EntityType, value: str) -> str | None:
        """Return the id of the entity with this canonical value, if any."""
        table, id_column, name_column = _TABLE_FOR[entity_type]
        with self.connect() as connection:
            row = connection.execute(
                f"SELECT {id_column} FROM {table} WHERE {name_column} = ?", (value,)
            ).fetchone()
        return str(row[id_column]) if row else None

    def clear_place_latlon_candidate(self, place_id: str, latlon: LatLon) -> None:
        """Drop a candidate coordinate that has just been confirmed."""
        with self.connect() as connection:
            connection.execute(
                "DELETE FROM place_latlon_candidates WHERE place_id = ? AND latlon = ?",
                (place_id, latlon.format()),
            )

    def record_evidence(self, evidence: Evidence) -> None:
        """Append one provenance record, unless the same fact is already stored.

        Evidence is never deleted, and re-learning from an unchanged row is not
        new evidence — so the insert is keyed and ignored on repeat.
        """
        with self.connect() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO evidence (entity_type, entity_value, reason, candidate_text,"
                " proposed_latlon, source_root, source_folder, reference, source_description,"
                " section_context, run_id, status, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    evidence.entity_type.value,
                    evidence.entity_value,
                    evidence.reason.value,
                    evidence.candidate_text,
                    evidence.proposed_latlon.format() if evidence.proposed_latlon else None,
                    evidence.source_root,
                    evidence.source_folder,
                    evidence.reference,
                    evidence.source_description,
                    evidence.section_context,
                    evidence.run_id,
                    evidence.status.value,
                    (evidence.created_at or _utc_now()).isoformat(),
                ),
            )

    # -- Reading ----------------------------------------------------------

    def load(self) -> Dictionary:
        """Load the whole dictionary for matching."""
        with self.connect() as connection:
            aliases: dict[tuple[str, str], list[Alias]] = {}
            for row in connection.execute("SELECT * FROM aliases"):
                key = (row["entity_type"], row["entity_id"])
                aliases.setdefault(key, []).append(
                    Alias(text=row["alias"], status=ConfidenceStatus(row["status"]))
                )

            people = [
                Person(
                    person_id=row["person_id"],
                    canonical_name=row["canonical_name"],
                    aliases=aliases.get((EntityType.PERSON.value, row["person_id"]), []),
                    notes=row["notes"],
                )
                for row in connection.execute("SELECT * FROM people ORDER BY canonical_name")
            ]

            candidates: dict[str, list[LatLon]] = {}
            for row in connection.execute("SELECT * FROM place_latlon_candidates"):
                point = parse_latlon(row["latlon"])
                if point is not None:
                    candidates.setdefault(row["place_id"], []).append(point)

            places = [
                Place(
                    place_id=row["place_id"],
                    canonical_place=row["canonical_place"],
                    aliases=aliases.get((EntityType.PLACE.value, row["place_id"]), []),
                    latlon=parse_latlon(row["latlon"]),
                    candidate_latlon=candidates.get(row["place_id"], []),
                    map_link=row["map_link"],
                    notes=row["notes"],
                )
                for row in connection.execute("SELECT * FROM places ORDER BY canonical_place")
            ]

            tags = [
                Tag(
                    tag_id=row["tag_id"],
                    canonical_tag=row["canonical_tag"],
                    aliases=aliases.get((EntityType.TAG.value, row["tag_id"]), []),
                    notes=row["notes"],
                )
                for row in connection.execute("SELECT * FROM tags ORDER BY canonical_tag")
            ]

        return Dictionary(people=people, places=places, tags=tags)

    def evidence_for(self, entity_type: EntityType, entity_value: str) -> list[Evidence]:
        """Return every recorded reason concerning one entity."""
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM evidence WHERE entity_type = ? AND entity_value = ?"
                " ORDER BY id",
                (entity_type.value, entity_value),
            ).fetchall()

        return [
            Evidence(
                entity_type=EntityType(row["entity_type"]),
                entity_value=row["entity_value"],
                reason=EvidenceReason(row["reason"]),
                candidate_text=row["candidate_text"],
                proposed_latlon=parse_latlon(row["proposed_latlon"]),
                source_root=row["source_root"],
                source_folder=row["source_folder"],
                reference=row["reference"],
                source_description=row["source_description"],
                section_context=row["section_context"],
                run_id=row["run_id"],
                status=ConfidenceStatus(row["status"]),
                created_at=datetime.fromisoformat(row["created_at"]),
            )
            for row in rows
        ]

    def evidence_count(self, entity_type: EntityType, entity_value: str) -> int:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS total FROM evidence"
                " WHERE entity_type = ? AND entity_value = ?",
                (entity_type.value, entity_value),
            ).fetchone()
        return int(row["total"])
