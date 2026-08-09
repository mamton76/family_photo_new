"""Applying a resolved merge — carefully, and in the right order.

The dangerous moment in any merge tool is the gap between deciding and
writing. While somebody was choosing resolutions, the other machine may have
saved the Drive copy again. Applying the old decision would silently overwrite
work that never took part in the conflict.

So the order is fixed:

1. every conflict must carry an explicit, valid resolution;
2. the resolved content is reconstructed;
3. **the remote is re-checked** — if it moved, stop and recompute;
4. only then is the canonical local workbook written and Drive updated;
5. and only after Drive confirms are ``last_common_hash``, the semantic
   baseline and ``last_sync`` advanced.

Nothing before step 5 may claim the two sides agree, because until the transfer
succeeds they do not.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable

from photoarchive.merge.baseline import SemanticBaseline, SemanticRecord
from photoarchive.merge.threeway import FieldConflict, MergeResult
from photoarchive.merge.workbook import (
    ResolutionSheet,
    read_conflict_workbook,
)

LOG = logging.getLogger(__name__)


class ApplyStatus(str, Enum):
    APPLIED = "applied"
    INCOMPLETE = "incomplete"
    REMOTE_CHANGED = "remote_changed"


@dataclass(slots=True)
class ApplyOutcome:
    """What applying a resolved merge concluded."""

    status: ApplyStatus
    records: dict[str, SemanticRecord] = field(default_factory=dict)
    order: list[str] = field(default_factory=list)
    unresolved: list[FieldConflict] = field(default_factory=list)
    message: str = ""

    @property
    def ok(self) -> bool:
        return self.status is ApplyStatus.APPLIED

    def as_baseline(self, artifact: str, path: str = "") -> SemanticBaseline:
        """The new agreed content — recorded only after a successful transfer."""
        return SemanticBaseline(
            artifact=artifact, path=path, records=dict(self.records), order=list(self.order)
        )


def resolve(
    merge_result: MergeResult,
    sheet: ResolutionSheet,
    expected_remote_hash: str | None = None,
    current_remote_hash: Callable[[], str | None] | None = None,
) -> ApplyOutcome:
    """Apply recorded resolutions to a merge result.

    ``expected_remote_hash`` is the Drive content this conflict was computed
    against. ``current_remote_hash`` reads what Drive holds *now*; when the two
    disagree the resolution is stale and nothing is written.
    """
    unresolved = sheet.missing()
    if unresolved:
        return ApplyOutcome(
            status=ApplyStatus.INCOMPLETE,
            unresolved=unresolved,
            message=(
                f"{len(unresolved)} conflict(s) have no explicit resolution. "
                "Choose LOCAL, DRIVE, BASE or CUSTOM for every row "
                "(CUSTOM also needs a Custom Value)."
            ),
        )

    # Re-check the remote before, not after, deciding to write.
    if current_remote_hash is not None:
        now = current_remote_hash()
        if expected_remote_hash is not None and now != expected_remote_hash:
            return ApplyOutcome(
                status=ApplyStatus.REMOTE_CHANGED,
                message=(
                    "REMOTE CHANGED SINCE CONFLICT WAS CREATED\n"
                    f"  conflict was computed against: {expected_remote_hash}\n"
                    f"  Google Drive now holds:        {now}\n"
                    "  Nothing was written. Re-run the sync to recompute the "
                    "conflict against the newer remote copy."
                ),
            )

    records = {key: _copy(record) for key, record in merge_result.records.items()}
    by_key = {conflict.key: conflict for conflict in merge_result.conflicts}

    for key, resolution in sheet.resolutions.items():
        conflict = by_key.get(key)
        if conflict is None:
            LOG.debug("Ignoring resolution for unknown conflict %s", key)
            continue
        record = records.get(conflict.record_id)
        if record is None:
            continue
        record.fields[conflict.field_name] = resolution.value(conflict)

    return ApplyOutcome(
        status=ApplyStatus.APPLIED,
        records=records,
        order=list(merge_result.order),
        message=f"Resolved {len(merge_result.conflicts)} conflict(s).",
    )


def resolve_from_workbook(
    merge_result: MergeResult,
    workbook_path: Path | str,
    expected_remote_hash: str | None = None,
    current_remote_hash: Callable[[], str | None] | None = None,
) -> ApplyOutcome:
    """Read a merge workbook and apply whatever a person decided in it."""
    sheet = read_conflict_workbook(workbook_path)
    return resolve(merge_result, sheet, expected_remote_hash, current_remote_hash)


def archive_merge_workbook(path: Path | str) -> Path:
    """Mark a merge workbook resolved without discarding it.

    The reasoning behind a decision is worth keeping; renaming beats deleting.
    """
    path = Path(path)
    resolved = path.with_name(path.name.replace(".merge.xlsx", ".resolved.xlsx"))
    if resolved.exists():
        resolved.unlink()
    path.rename(resolved)
    return resolved


def _copy(record: SemanticRecord) -> SemanticRecord:
    return SemanticRecord(
        record_id=record.record_id,
        label=record.label,
        sheet=record.sheet,
        fields=dict(record.fields),
    )
