"""Three-way synchronisation for the files people edit.

``review.xlsx`` and ``catalog.xlsx`` are different from everything else the
pipeline touches: a person may edit them on more than one machine. Comparing
two sides is not enough — "local differs from remote" cannot distinguish *I
changed this* from *they changed this* from *we both did*.

So each editable artifact records the content hash both sides last agreed on,
and every decision is made against that baseline:

===============  ================  ==========================================
local vs last    remote vs last    action
===============  ================  ==========================================
same             same              nothing to do
changed          same              upload local
same             changed           download remote
changed          changed           **conflict — overwrite neither side**
===============  ================  ==========================================

There is deliberately no automatic merge. A binary ``.xlsx`` cannot be merged
safely, and losing an afternoon of somebody's review typing is far worse than
asking them which copy to keep.

Generated artifacts — ``review-all.html``, processed photos — do not use this.
They have no human edits to protect: they are regenerated when their inputs
change and uploaded over whatever was there.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from photoarchive.portable.models import ArtifactSyncState
from photoarchive.portable.provenance import OperationProvenance


class SyncAction(str, Enum):
    """What a three-way comparison concluded."""

    NOOP = "noop"
    UPLOAD = "upload"
    DOWNLOAD = "download"
    CONFLICT = "conflict"
    #: Never synchronised before and present on exactly one side.
    ADOPT_LOCAL = "adopt_local"
    ADOPT_REMOTE = "adopt_remote"


@dataclass(frozen=True, slots=True)
class SyncDecision:
    """The outcome for one artifact, with enough context to explain itself."""

    path: str
    action: SyncAction
    local_hash: str | None = None
    remote_hash: str | None = None
    last_common_hash: str | None = None
    last_sync: OperationProvenance | None = None

    @property
    def is_conflict(self) -> bool:
        return self.action is SyncAction.CONFLICT

    @property
    def changes_anything(self) -> bool:
        return self.action not in (SyncAction.NOOP, SyncAction.CONFLICT)


def content_hash(data: bytes) -> str:
    """Content hash of an artifact, in the form stored in portable state."""
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def file_hash(path: Path | str) -> str | None:
    """Hash a local file, or ``None`` when it does not exist."""
    path = Path(path)
    if not path.exists():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def decide(
    path: str,
    local_hash: str | None,
    remote_hash: str | None,
    state: ArtifactSyncState | None = None,
) -> SyncDecision:
    """Compare both sides against the last agreed baseline."""
    last = state.last_common_hash if state else None
    provenance = state.last_sync if state else None

    def outcome(action: SyncAction) -> SyncDecision:
        return SyncDecision(
            path=path,
            action=action,
            local_hash=local_hash,
            remote_hash=remote_hash,
            last_common_hash=last,
            last_sync=provenance,
        )

    if local_hash is None and remote_hash is None:
        return outcome(SyncAction.NOOP)

    # Never synchronised: adopt whichever side exists. If both exist and differ,
    # there is no baseline to judge by, so it is a conflict rather than a guess.
    if last is None:
        if local_hash is not None and remote_hash is None:
            return outcome(SyncAction.ADOPT_LOCAL)
        if local_hash is None and remote_hash is not None:
            return outcome(SyncAction.ADOPT_REMOTE)
        if local_hash == remote_hash:
            return outcome(SyncAction.NOOP)
        return outcome(SyncAction.CONFLICT)

    local_changed = local_hash != last
    remote_changed = remote_hash != last

    if not local_changed and not remote_changed:
        return outcome(SyncAction.NOOP)
    if local_changed and not remote_changed:
        return outcome(SyncAction.UPLOAD)
    if remote_changed and not local_changed:
        return outcome(SyncAction.DOWNLOAD)
    # Both moved. Identical edits on both sides are not a conflict.
    if local_hash == remote_hash:
        return outcome(SyncAction.NOOP)
    return outcome(SyncAction.CONFLICT)


def describe_conflict(decision: SyncDecision) -> str:
    """A conflict report a person can act on without reading the code."""
    lines = [f"CONFLICT: {decision.path}", ""]

    if decision.last_sync is not None:
        lines.append("Last common sync:")
        lines.append(f"  {decision.last_sync.describe()}")
    elif decision.last_common_hash:
        lines.append("Last common sync: recorded, but without provenance")
    else:
        lines.append("Last common sync: never — both copies appeared independently")

    lines.extend(
        [
            "",
            "Local:",
            "  changed since last sync"
            if decision.local_hash != decision.last_common_hash
            else "  unchanged",
            "",
            "Google Drive:",
            "  changed since last sync"
            if decision.remote_hash != decision.last_common_hash
            else "  unchanged",
            "",
            "Nothing was overwritten. Keep one copy, or merge by hand, then re-run.",
        ]
    )
    return "\n".join(lines)


def record_sync(
    state: ArtifactSyncState,
    agreed_hash: str | None,
    provenance: OperationProvenance,
    drive_file_id: str | None = None,
) -> ArtifactSyncState:
    """Update a baseline after a successful transfer."""
    state.last_common_hash = agreed_hash
    state.last_sync = provenance
    if drive_file_id:
        state.drive_file_id = drive_file_id
    return state
