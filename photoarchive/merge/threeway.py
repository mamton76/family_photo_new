"""Field-level three-way merge of two edited copies of one workbook.

The file-level rule stays the safety gate — it decides whether a merge is even
needed. But "both sides changed" must not mean "the whole workbook is an opaque
conflict". These workbooks have a known schema and stable row identity, so the
comparison happens per field:

===================  ===================  ==========================
local vs base        remote vs base       result
===================  ===================  ==========================
changed              unchanged            take local
unchanged            changed              take remote
changed              changed, same value  take that value
changed              changed, differently **true conflict**
===================  ===================  ==========================

Two people editing different columns of the same row is not a conflict, and a
person should never be asked about it. Only a genuine disagreement about one
value reaches them — and there the merge refuses to guess.

"Changed" and "same value" are judged *semantically*
(:mod:`photoarchive.merge.semantic`), not by raw text equality: reordering
``People`` or reformatting ``LatLon`` is not a change worth asking about, but
the value carried forward — for display, for the conflict workbook, for the
merged output — is always the raw text somebody actually typed. Nothing
normalised is ever written out.

Machine-owned fields never take part: they are regenerated from source and
dictionary state after the merge, so a difference in them means nothing.

When no common baseline exists yet, :func:`merge_first_sync` reuses this same
algorithm against an empty ``base`` — see its docstring.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from photoarchive.merge.baseline import SemanticBaseline, SemanticRecord
from photoarchive.merge.semantic import semantic_equal

#: How a record differs structurally between the three sides.
class RecordChange(str, Enum):
    UNCHANGED = "unchanged"
    ADDED_LOCAL = "added_local"
    ADDED_REMOTE = "added_remote"
    ADDED_BOTH = "added_both"
    REMOVED_LOCAL = "removed_local"
    REMOVED_REMOTE = "removed_remote"
    MODIFIED = "modified"


class ConflictKind(str, Enum):
    FIELD = "field"
    #: One side edited a record the other side deleted.
    DELETE_VS_EDIT = "delete_vs_edit"
    #: The same stable id appeared independently on both sides, differently.
    ADDED_BOTH = "added_both"
    #: Like ``ADDED_BOTH``, but for a whole workbook with no common baseline
    #: yet — there is no BASE to offer, and the resolution UX says so.
    FIRST_SYNC = "first_sync"


@dataclass(frozen=True, slots=True)
class FieldConflict:
    """One genuine disagreement a person has to settle."""

    record_id: str
    label: str
    sheet: str
    field_name: str
    base: str
    local: str
    remote: str
    kind: ConflictKind = ConflictKind.FIELD

    @property
    def key(self) -> str:
        return f"{self.record_id}::{self.field_name}"


@dataclass(slots=True)
class MergeResult:
    """A merged model plus whatever could not be decided automatically."""

    artifact: str
    records: dict[str, SemanticRecord] = field(default_factory=dict)
    order: list[str] = field(default_factory=list)
    conflicts: list[FieldConflict] = field(default_factory=list)
    #: Records auto-merged without a person, for reporting.
    auto_merged: list[str] = field(default_factory=list)
    added_local: list[str] = field(default_factory=list)
    added_remote: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)

    @property
    def has_conflicts(self) -> bool:
        return bool(self.conflicts)

    def conflict_for(self, record_id: str, field_name: str) -> FieldConflict | None:
        for conflict in self.conflicts:
            if conflict.record_id == record_id and conflict.field_name == field_name:
                return conflict
        return None

    def as_baseline(self, path: str = "") -> SemanticBaseline:
        return SemanticBaseline(
            artifact=self.artifact,
            path=path,
            records=dict(self.records),
            order=list(self.order),
        )


def merge(
    base: SemanticBaseline,
    local: SemanticBaseline,
    remote: SemanticBaseline,
    deletions_are_meaningful: bool = False,
    first_sync: bool = False,
) -> MergeResult:
    """Three-way merge of one workbook's human-owned content.

    ``deletions_are_meaningful`` distinguishes the two artifacts. In
    ``review.xlsx`` the scan owns which rows exist, so a row missing from
    somebody's copy is not an instruction to delete an archive item — the row
    is kept. In ``catalog.xlsx`` deleting an entity *is* a supported edit, so a
    deletion is honoured when the other side left the entity alone, and raised
    as a conflict when the other side changed it.

    ``first_sync`` marks a merge against an empty, unproven ``base`` (see
    :func:`merge_first_sync`): the only structural conflict that can occur —
    the same id present on both sides with differing content — is reported as
    :attr:`ConflictKind.FIRST_SYNC` instead of :attr:`ConflictKind.ADDED_BOTH`,
    so the resolution workbook can say there is no common baseline rather than
    implying one merely wasn't found for this one record.
    """
    result = MergeResult(artifact=base.artifact or local.artifact or remote.artifact)
    fields = _fields_for(base, local, remote)
    artifact = result.artifact

    for record_id in _ordered_ids(base, local, remote):
        base_record = base.get(record_id)
        local_record = local.get(record_id)
        remote_record = remote.get(record_id)

        merged = _merge_record(
            record_id, base_record, local_record, remote_record, fields,
            deletions_are_meaningful, result, artifact, first_sync,
        )
        if merged is not None:
            result.records[record_id] = merged
            result.order.append(record_id)

    return result


def merge_first_sync(
    local: SemanticBaseline,
    remote: SemanticBaseline,
    deletions_are_meaningful: bool = False,
) -> MergeResult:
    """Merge two copies that have never shared a common baseline.

    There is no proven ancestor, so nothing here is "a deletion": a record
    present on exactly one side is simply a safe addition (see
    :func:`_merge_record`'s ``base_record is None`` branch, which this reuses
    unchanged). A record present on both sides is compared field by field —
    semantically equal fields merge silently, and a genuine disagreement
    becomes a :attr:`ConflictKind.FIRST_SYNC` conflict with no base value to
    fall back to.
    """
    empty_base = SemanticBaseline(artifact=local.artifact or remote.artifact)
    return merge(empty_base, local, remote, deletions_are_meaningful, first_sync=True)


def _merge_record(
    record_id: str,
    base_record: SemanticRecord | None,
    local_record: SemanticRecord | None,
    remote_record: SemanticRecord | None,
    fields: tuple[str, ...],
    deletions_are_meaningful: bool,
    result: MergeResult,
    artifact: str,
    first_sync: bool = False,
) -> SemanticRecord | None:
    # --- structural cases -------------------------------------------------
    if base_record is None:
        if local_record is not None and remote_record is None:
            result.added_local.append(record_id)
            return local_record
        if remote_record is not None and local_record is None:
            result.added_remote.append(record_id)
            return remote_record
        if local_record is not None and remote_record is not None:
            # Independently created under the same id: safe only if identical.
            if _same(local_record, remote_record, fields, artifact):
                result.added_local.append(record_id)
                return local_record
            kind = ConflictKind.FIRST_SYNC if first_sync else ConflictKind.ADDED_BOTH
            return _conflicted_record(
                record_id, None, local_record, remote_record, fields,
                kind, result, artifact,
            )
        return None

    if local_record is None or remote_record is None:
        surviving = local_record or remote_record
        other_changed = surviving is not None and not _same(
            base_record, surviving, fields, artifact
        )

        if not deletions_are_meaningful:
            # review.xlsx: rows exist because the source says so. A row missing
            # from one copy is not permission to drop an archive item.
            result.removed.append(record_id)
            return surviving or base_record

        if surviving is None:
            result.removed.append(record_id)
            return None
        if not other_changed:
            result.removed.append(record_id)
            return None
        # Deleted on one side, edited on the other: only a person can decide.
        return _conflicted_record(
            record_id, base_record, local_record, remote_record, fields,
            ConflictKind.DELETE_VS_EDIT, result, artifact,
        )

    # --- the ordinary case: field by field --------------------------------
    merged_fields: dict[str, str] = {}
    conflicted = False

    for name in fields:
        base_value = base_record.value(name)
        local_value = local_record.value(name)
        remote_value = remote_record.value(name)

        # Comparison is semantic; the value carried forward is always the
        # raw text somebody actually typed, never a normalised stand-in.
        local_changed = not semantic_equal(artifact, name, local_value, base_value)
        remote_changed = not semantic_equal(artifact, name, remote_value, base_value)

        if local_changed and remote_changed:
            if semantic_equal(artifact, name, local_value, remote_value):
                # Both sides moved to the same meaning, worded differently.
                # Keep local's actual wording rather than inventing a third
                # spelling nobody typed.
                merged_fields[name] = local_value
                continue
            conflicted = True
            result.conflicts.append(
                FieldConflict(
                    record_id=record_id,
                    label=local_record.label or remote_record.label,
                    sheet=local_record.sheet or base_record.sheet,
                    field_name=name,
                    base=base_value,
                    local=local_value,
                    remote=remote_value,
                )
            )
            # Hold the base value until a person decides; nothing is guessed.
            merged_fields[name] = base_value
            continue

        if local_changed and not remote_changed:
            merged_fields[name] = local_value
        elif remote_changed and not local_changed:
            merged_fields[name] = remote_value
        else:
            # Neither side changed the meaning; keep local's text (identical
            # to base's meaning, and to remote's when that one moved too).
            merged_fields[name] = local_value

    if not conflicted and (
        merged_fields != {name: base_record.value(name) for name in fields}
    ):
        result.auto_merged.append(record_id)

    return SemanticRecord(
        record_id=record_id,
        label=local_record.label or remote_record.label or base_record.label,
        sheet=local_record.sheet or base_record.sheet,
        fields=merged_fields,
    )


def _conflicted_record(
    record_id: str,
    base_record: SemanticRecord | None,
    local_record: SemanticRecord | None,
    remote_record: SemanticRecord | None,
    fields: tuple[str, ...],
    kind: ConflictKind,
    result: MergeResult,
    artifact: str,
) -> SemanticRecord:
    """Record a structural conflict, holding the safest value meanwhile."""
    reference = local_record or remote_record or base_record
    assert reference is not None

    for name in fields:
        local_value = local_record.value(name) if local_record else ""
        remote_value = remote_record.value(name) if remote_record else ""
        base_value = base_record.value(name) if base_record else ""
        if semantic_equal(artifact, name, local_value, remote_value):
            continue
        result.conflicts.append(
            FieldConflict(
                record_id=record_id,
                label=reference.label,
                sheet=reference.sheet,
                field_name=name,
                base=base_value,
                local=local_value,
                remote=remote_value,
                kind=kind,
            )
        )

    return SemanticRecord(
        record_id=record_id,
        label=reference.label,
        sheet=reference.sheet,
        fields={
            name: (base_record.value(name) if base_record else reference.value(name))
            for name in fields
        },
    )


def _same(
    first: SemanticRecord, second: SemanticRecord, fields: tuple[str, ...], artifact: str
) -> bool:
    return all(
        semantic_equal(artifact, name, first.value(name), second.value(name))
        for name in fields
    )


def _fields_for(*baselines: SemanticBaseline) -> tuple[str, ...]:
    """Every human-owned field seen on any side, in a stable order."""
    names: list[str] = []
    for baseline in baselines:
        for record in baseline.records.values():
            for name in record.fields:
                if name not in names:
                    names.append(name)
    if names:
        return tuple(names)
    return baselines[0].human_fields if baselines else ()


def _ordered_ids(*baselines: SemanticBaseline) -> list[str]:
    """Record ids in a deterministic order: base order first, then additions."""
    ordered: list[str] = []
    for baseline in baselines:
        for record_id in baseline.order or sorted(baseline.records):
            if record_id not in ordered:
                ordered.append(record_id)
    for baseline in baselines:
        for record_id in sorted(baseline.records):
            if record_id not in ordered:
                ordered.append(record_id)
    return ordered
