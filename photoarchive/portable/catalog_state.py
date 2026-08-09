"""Making the dictionaries and their evidence survive a lost laptop.

``catalog.xlsx`` is the human editing surface, but it deliberately shows only a
count of the evidence behind each entity. The reasoning itself — which photo,
which description, which run proposed an alias — lives in SQLite, and SQLite is
declared disposable. Without this module, deleting a local database would throw
away the archive's accumulated explanations.

So the dictionaries are exported whole to ``_archive_state/catalog.json``:
entities with their stable ids, confirmed and candidate aliases, confirmed and
candidate coordinates, and every evidence record. Importing rebuilds the
database exactly, ids included, so a clean machine resolves the same aliases to
the same entities as the machine that learned them.
"""

from __future__ import annotations

import logging
from typing import Any

from photoarchive.catalog.models import (
    ConfidenceStatus,
    EntityType,
    Evidence,
    EvidenceReason,
)
from photoarchive.catalog.store import DictionaryStore
from photoarchive.geo import parse_latlon
from photoarchive.portable.models import SCHEMA_VERSION, check_schema_version
from photoarchive.portable.provenance import format_timestamp, parse_timestamp

LOG = logging.getLogger(__name__)


def export_catalog(store: DictionaryStore) -> dict[str, Any]:
    """Serialise the whole dictionary, including evidence, to plain JSON.

    Output is sorted throughout so an unchanged dictionary produces an
    identical file and portable state diffs stay readable.
    """
    dictionary = store.load()

    people = [
        {
            "person_id": person.person_id,
            "canonical_name": person.canonical_name,
            "confirmed_aliases": sorted(person.confirmed_aliases),
            "candidate_aliases": sorted(person.candidate_aliases),
            "notes": person.notes,
        }
        for person in sorted(dictionary.people, key=lambda item: item.canonical_name)
    ]

    places = [
        {
            "place_id": place.place_id,
            "canonical_place": place.canonical_place,
            "confirmed_aliases": sorted(place.confirmed_aliases),
            "candidate_aliases": sorted(place.candidate_aliases),
            "latlon": place.latlon.format() if place.latlon else None,
            "candidate_latlon": sorted(
                point.format() for point in place.candidate_latlon
            ),
            "map_link": place.map_link,
            "notes": place.notes,
        }
        for place in sorted(dictionary.places, key=lambda item: item.canonical_place)
    ]

    tags = [
        {
            "tag_id": tag.tag_id,
            "canonical_tag": tag.canonical_tag,
            "confirmed_aliases": sorted(tag.confirmed_aliases),
            "candidate_aliases": sorted(tag.candidate_aliases),
            "notes": tag.notes,
        }
        for tag in sorted(dictionary.tags, key=lambda item: item.canonical_tag)
    ]

    evidence: list[dict[str, Any]] = []
    for entity_type, values in (
        (EntityType.PERSON, [item["canonical_name"] for item in people]),
        (EntityType.PLACE, [item["canonical_place"] for item in places]),
        (EntityType.TAG, [item["canonical_tag"] for item in tags]),
    ):
        for value in values:
            evidence.extend(
                _evidence_to_dict(record) for record in store.evidence_for(entity_type, value)
            )

    return {
        "schema_version": SCHEMA_VERSION,
        "exported_at": format_timestamp(),
        "people": people,
        "places": places,
        "tags": tags,
        "evidence": evidence,
    }


def import_catalog(store: DictionaryStore, data: dict[str, Any]) -> dict[str, int]:
    """Rebuild a dictionary database from portable state.

    Stable ids are preserved, not regenerated: a rebuilt database must resolve
    the same aliases to the same entities as the one it replaced. Existing rows
    are left alone, so importing onto a populated database tops it up rather
    than duplicating it.
    """
    check_schema_version(data, "catalog state")
    store.initialize()
    counts = {"people": 0, "places": 0, "tags": 0, "aliases": 0, "evidence": 0}

    for person in data.get("people") or []:
        _restore_entity(
            store, EntityType.PERSON, person["person_id"], person["canonical_name"],
            person.get("notes"),
        )
        counts["people"] += 1
        counts["aliases"] += _restore_aliases(store, EntityType.PERSON, person)

    for place in data.get("places") or []:
        _restore_entity(
            store, EntityType.PLACE, place["place_id"], place["canonical_place"],
            place.get("notes"),
        )
        counts["places"] += 1
        counts["aliases"] += _restore_aliases(store, EntityType.PLACE, place)

        point = parse_latlon(place.get("latlon"))
        if point is not None:
            store.set_place_latlon(place["place_id"], point)
        for candidate in place.get("candidate_latlon") or []:
            proposed = parse_latlon(candidate)
            if proposed is not None:
                store.propose_place_latlon(place["place_id"], proposed)

    for tag in data.get("tags") or []:
        _restore_entity(
            store, EntityType.TAG, tag["tag_id"], tag["canonical_tag"], tag.get("notes")
        )
        counts["tags"] += 1
        counts["aliases"] += _restore_aliases(store, EntityType.TAG, tag)

    for record in data.get("evidence") or []:
        restored = _evidence_from_dict(record)
        if restored is not None:
            store.record_evidence(restored)
            counts["evidence"] += 1

    return counts


def _restore_entity(
    store: DictionaryStore, entity_type: EntityType, entity_id: str, canonical: str,
    notes: str | None,
) -> None:
    """Insert an entity under its original id, or leave the existing one."""
    table, id_column, name_column = {
        EntityType.PERSON: ("people", "person_id", "canonical_name"),
        EntityType.PLACE: ("places", "place_id", "canonical_place"),
        EntityType.TAG: ("tags", "tag_id", "canonical_tag"),
    }[entity_type]

    with store.connect() as connection:
        connection.execute(
            f"INSERT OR IGNORE INTO {table} ({id_column}, {name_column}, notes)"
            " VALUES (?, ?, ?)",
            (entity_id, canonical, notes),
        )


def _restore_aliases(
    store: DictionaryStore, entity_type: EntityType, payload: dict[str, Any]
) -> int:
    entity_id = payload.get(f"{entity_type.value}_id")
    added = 0
    for alias in payload.get("confirmed_aliases") or []:
        store.add_alias(entity_type, entity_id, alias, ConfidenceStatus.CONFIRMED)
        added += 1
    for alias in payload.get("candidate_aliases") or []:
        store.add_alias(entity_type, entity_id, alias, ConfidenceStatus.CANDIDATE)
        added += 1
    return added


def _evidence_to_dict(record: Evidence) -> dict[str, Any]:
    return {
        "entity_type": record.entity_type.value,
        "entity_value": record.entity_value,
        "reason": record.reason.value,
        "candidate_text": record.candidate_text,
        "proposed_latlon": record.proposed_latlon.format() if record.proposed_latlon else None,
        "source_root": record.source_root,
        "source_folder": record.source_folder,
        "reference": record.reference,
        "source_description": record.source_description,
        "section_context": record.section_context,
        "run_id": record.run_id,
        "status": record.status.value,
        "created_at": format_timestamp(record.created_at) if record.created_at else None,
    }


def _evidence_from_dict(data: dict[str, Any]) -> Evidence | None:
    try:
        return Evidence(
            entity_type=EntityType(data["entity_type"]),
            entity_value=str(data["entity_value"]),
            reason=EvidenceReason(data["reason"]),
            candidate_text=data.get("candidate_text"),
            proposed_latlon=parse_latlon(data.get("proposed_latlon")),
            source_root=data.get("source_root"),
            source_folder=data.get("source_folder"),
            reference=data.get("reference"),
            source_description=data.get("source_description"),
            section_context=data.get("section_context"),
            run_id=data.get("run_id"),
            status=ConfidenceStatus(data.get("status", ConfidenceStatus.CANDIDATE.value)),
            created_at=parse_timestamp(data.get("created_at")),
        )
    except (KeyError, ValueError) as error:
        LOG.debug("Skipping unreadable evidence record: %s", error)
        return None
