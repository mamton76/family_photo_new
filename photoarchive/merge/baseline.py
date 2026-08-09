"""The semantic baseline: what both sides last agreed the *content* was.

``last_common_hash`` can tell you that a workbook changed. It cannot tell you
*what* changed, so it cannot merge. Answering "you edited Place, they edited
People, and those don't collide" needs the actual last-agreed values.

So alongside the hash, portable state keeps a normalised model of each synced
human-editable workbook: stable identity plus human-owned fields, and nothing
else. Previews, machine-generated columns and formatting are deliberately
absent — they are regenerated from source and dictionary state after a merge,
and including them would manufacture conflicts nobody needs to resolve.

The baseline is compact JSON rather than an archived ``.xlsx``: it diffs
readably, survives a schema change, and costs kilobytes instead of megabytes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: Bumped when the *meaning* of a baseline record changes.
BASELINE_SCHEMA_VERSION = 1

ARTIFACT_REVIEW = "review"
ARTIFACT_CATALOG = "catalog"

#: Human-owned columns of ``review.xlsx``. Everything else on that sheet is
#: machine-owned and regenerated, so it can never be a conflict.
#:
#: ``Status`` is included deliberately: the scan writes lifecycle transitions,
#: but a reviewer marking a row APPROVED is a decision that must survive a
#: merge. System transitions are re-applied by the next scan afterwards.
REVIEW_HUMAN_FIELDS: tuple[str, ...] = (
    "date",
    "place",
    "latlon",
    "people",
    "tags",
    "event",
    "albums",
    "description",
    "status",
    "notes",
)

#: Machine-owned review columns, listed so the exclusion is explicit and
#: testable rather than implied by omission.
REVIEW_MACHINE_FIELDS: tuple[str, ...] = (
    "source_description",
    "section_context",
    "source_notes",
    "suggested_date",
    "suggested_place",
    "suggested_latlon",
    "suggested_people",
    "suggested_tags",
    "review_reason",
)

#: Human-owned catalog fields, per sheet.
CATALOG_HUMAN_FIELDS: dict[str, tuple[str, ...]] = {
    "People": ("canonical_name", "confirmed_aliases", "candidate_aliases", "notes"),
    "Places": (
        "canonical_place",
        "confirmed_aliases",
        "candidate_aliases",
        "latlon",
        "candidate_latlon",
        "map_link",
        "notes",
    ),
    "Tags": ("canonical_tag", "confirmed_aliases", "candidate_aliases", "notes"),
}


@dataclass(slots=True)
class SemanticRecord:
    """One row or entity, reduced to its human-owned values."""

    record_id: str
    label: str = ""
    sheet: str = ""
    fields: dict[str, str] = field(default_factory=dict)

    def value(self, name: str) -> str:
        return (self.fields.get(name) or "").strip()

    def as_dict(self) -> dict[str, Any]:
        return {
            "record_id": self.record_id,
            "label": self.label,
            "sheet": self.sheet,
            "fields": {key: self.fields[key] for key in sorted(self.fields)},
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SemanticRecord:
        return cls(
            record_id=str(data.get("record_id", "")),
            label=str(data.get("label", "")),
            sheet=str(data.get("sheet", "")),
            fields={
                str(key): "" if value is None else str(value)
                for key, value in (data.get("fields") or {}).items()
            },
        )


@dataclass(slots=True)
class SemanticBaseline:
    """The last-agreed content of one editable workbook."""

    artifact: str
    path: str = ""
    schema_version: int = BASELINE_SCHEMA_VERSION
    records: dict[str, SemanticRecord] = field(default_factory=dict)
    #: Row order as it appeared, so a merge can rebuild a stable workbook.
    order: list[str] = field(default_factory=list)

    @property
    def human_fields(self) -> tuple[str, ...]:
        return (
            REVIEW_HUMAN_FIELDS
            if self.artifact == ARTIFACT_REVIEW
            else tuple(
                sorted({name for names in CATALOG_HUMAN_FIELDS.values() for name in names})
            )
        )

    def get(self, record_id: str) -> SemanticRecord | None:
        return self.records.get(record_id)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "artifact": self.artifact,
            "path": self.path,
            "order": list(self.order),
            "records": {
                key: self.records[key].as_dict() for key in sorted(self.records)
            },
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SemanticBaseline:
        version = int(data.get("schema_version", 0))
        if version > BASELINE_SCHEMA_VERSION:
            raise ValueError(
                f"semantic baseline schema {version} is newer than this build "
                f"understands ({BASELINE_SCHEMA_VERSION})"
            )
        records = {
            key: SemanticRecord.from_dict(value)
            for key, value in (data.get("records") or {}).items()
        }
        return cls(
            artifact=str(data.get("artifact", ARTIFACT_REVIEW)),
            path=str(data.get("path", "")),
            schema_version=version or BASELINE_SCHEMA_VERSION,
            records=records,
            order=[str(item) for item in (data.get("order") or sorted(records))],
        )


def baseline_from_review_rows(rows, path: str = "") -> SemanticBaseline:
    """Reduce review rows to their human-owned values.

    Identity is the row's stable reference, which is what survives a
    ``DESCRIBED_ABSENT`` row gaining a filename later.
    """
    from photoarchive.review.excel import identity_key

    baseline = SemanticBaseline(artifact=ARTIFACT_REVIEW, path=path)
    for row in rows:
        record_id = identity_key(row.reference or row.filename)
        baseline.records[record_id] = SemanticRecord(
            record_id=record_id,
            label=row.filename or row.reference,
            sheet="Review",
            fields={
                name: _read_field(row, name) for name in REVIEW_HUMAN_FIELDS
            },
        )
        baseline.order.append(record_id)
    return baseline


def _read_field(row, name: str) -> str:
    value = getattr(row, name, "")
    if hasattr(value, "value"):  # WorkflowStatus and similar enums
        value = value.value
    return "" if value is None else str(value).strip()


def baseline_from_catalog(dictionary, path: str = "") -> SemanticBaseline:
    """Reduce the dictionaries to their human-owned values.

    Entities are keyed by their stable ids, so renaming a canonical value is an
    edit to that entity rather than the birth of another.
    """
    baseline = SemanticBaseline(artifact=ARTIFACT_CATALOG, path=path)

    for person in dictionary.people:
        _add_catalog_record(
            baseline, "People", person.person_id, person.canonical_name,
            {
                "canonical_name": person.canonical_name,
                "confirmed_aliases": _join(person.confirmed_aliases),
                "candidate_aliases": _join(person.candidate_aliases),
                "notes": person.notes or "",
            },
        )

    for place in dictionary.places:
        _add_catalog_record(
            baseline, "Places", place.place_id, place.canonical_place,
            {
                "canonical_place": place.canonical_place,
                "confirmed_aliases": _join(place.confirmed_aliases),
                "candidate_aliases": _join(place.candidate_aliases),
                "latlon": place.latlon.format() if place.latlon else "",
                "candidate_latlon": _join(
                    point.format() for point in place.candidate_latlon
                ),
                "map_link": place.map_link or "",
                "notes": place.notes or "",
            },
        )

    for tag in dictionary.tags:
        _add_catalog_record(
            baseline, "Tags", tag.tag_id, tag.canonical_tag,
            {
                "canonical_tag": tag.canonical_tag,
                "confirmed_aliases": _join(tag.confirmed_aliases),
                "candidate_aliases": _join(tag.candidate_aliases),
                "notes": tag.notes or "",
            },
        )

    return baseline


def _add_catalog_record(
    baseline: SemanticBaseline, sheet: str, entity_id: str, label: str,
    fields: dict[str, str],
) -> None:
    record_id = f"{sheet}:{entity_id}"
    baseline.records[record_id] = SemanticRecord(
        record_id=record_id, label=label, sheet=sheet, fields=fields
    )
    baseline.order.append(record_id)


def _join(values) -> str:
    return "; ".join(str(value) for value in values if value)
