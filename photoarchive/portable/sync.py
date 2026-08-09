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
changed          changed           field-level semantic merge (:mod:`photoarchive.merge`)
===============  ================  ==========================================

"Both changed" is not automatically a person's problem: :mod:`photoarchive.merge`
compares the two sides field by field and only a genuine disagreement about one
value becomes a conflict workbook. This module decides *whether* a merge is
needed; it never merges anything itself.

**First sync** — ``last_common_hash`` is ``None`` — has no proven common
ancestor, so it is handled as four explicit cases rather than folded into the
table above:

==============================  ======================================
situation                        action
==============================  ======================================
local only                       case A — adopt local (initial upload)
remote only                      case B — adopt remote (initial download)
both exist, byte-identical       case C1 — adopt as the first baseline, no transfer
both exist, semantically equal,
byte-different                   case C2 — no conflict, but a canonicalising
                                  upload is still required (below)
both exist, genuinely differ     case D — first-sync merge workbook, no BASE offered
==============================  ======================================

Case C splits in two, because "no human conflict" is not the same claim as
"nothing needs to move". ``last_common_hash`` is one hash both sides
genuinely hold — it cannot be recorded while ``local_hash != remote_hash``,
no matter how confidently the *semantic* content agrees:

* **C1 — byte-identical** (``local_hash == remote_hash``): ``ADOPT_BASELINE``.
  Nothing to transfer; the shared hash can be recorded once adoption is
  confirmed.
* **C2 — semantically equal, byte-different**: ``ADOPT_BASELINE_WITH_UPLOAD``.
  Not a conflict — but local's canonical bytes must still be uploaded over
  the Drive copy before any hash is genuinely common. The direction is fixed:
  local is produced by the current pipeline and carries current
  machine-owned fields, so local overwrites Drive, never the reverse. Only
  after that upload succeeds do local and remote bytes match, and only then
  is ``local_hash`` recorded as ``last_common_hash``.

Case D never guesses which side is the ancestor — there isn't one — and never
picks a side by timestamp, machine or filename.

``last_common_hash``, the semantic baseline and ``last_sync`` are written only
by :func:`record_sync`, after a real transfer or adoption actually succeeds —
never by :func:`decide` itself. In particular, for case C2, they are written
only after the canonicalising upload lands, exactly as for an ordinary
``UPLOAD``.

Generated artifacts — ``review-all.html``, processed photos — do not use this.
They have no human edits to protect: they are regenerated when their inputs
change and uploaded over whatever was there.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from photoarchive.merge.baseline import SemanticBaseline
from photoarchive.merge.semantic import semantic_baselines_equal
from photoarchive.portable.models import ArtifactSyncState
from photoarchive.portable.provenance import OperationProvenance


class SyncAction(str, Enum):
    """What a three-way comparison concluded."""

    NOOP = "noop"
    UPLOAD = "upload"
    DOWNLOAD = "download"
    #: Both sides moved since the last common baseline, and disagree.
    CONFLICT = "conflict"
    #: Never synchronised before and present on exactly one side (first
    #: sync, case A / case B — see :func:`decide`).
    ADOPT_LOCAL = "adopt_local"
    ADOPT_REMOTE = "adopt_remote"
    #: Never synchronised before, present on both sides, and byte-identical —
    #: first sync, case C1. No content needs to move; only the first baseline
    #: needs to be recorded, once a real adoption confirms it.
    ADOPT_BASELINE = "adopt_baseline"
    #: Never synchronised before, present on both sides, semantically equal
    #: but byte-different — first sync, case C2. Not a human conflict, but
    #: ``local_hash != remote_hash`` means no hash is genuinely shared yet:
    #: local's canonical bytes must be uploaded over Drive first. Only after
    #: that upload succeeds does ``local_hash`` become ``last_common_hash``.
    ADOPT_BASELINE_WITH_UPLOAD = "adopt_baseline_with_upload"
    #: Never synchronised before, present on both sides, and genuinely
    #: different — first sync, case D. There is no ancestor to merge against,
    #: so this needs a first-sync merge workbook, not an ordinary one.
    FIRST_SYNC_CONFLICT = "first_sync_conflict"


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
    def is_first_sync(self) -> bool:
        """True when no common baseline existed before this decision."""
        return self.last_common_hash is None

    @property
    def is_conflict(self) -> bool:
        return self.action in (SyncAction.CONFLICT, SyncAction.FIRST_SYNC_CONFLICT)

    @property
    def changes_anything(self) -> bool:
        """True when this decision moves artifact *content* somewhere.

        ``ADOPT_BASELINE`` (case C1, byte-identical) deliberately is not
        included: both sides already hold the same bytes, so nothing needs to
        be transferred — only a first baseline needs recording, which is
        bookkeeping, not a content change. ``ADOPT_BASELINE_WITH_UPLOAD``
        (case C2, semantically equal but byte-different) is *not* excluded:
        no hash is genuinely shared until local's canonical bytes are
        uploaded over Drive, so this is content movement even though it will
        never be a human conflict.
        """
        return self.action not in (
            SyncAction.NOOP,
            SyncAction.CONFLICT,
            SyncAction.FIRST_SYNC_CONFLICT,
            SyncAction.ADOPT_BASELINE,
        )


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
    *,
    local_baseline: SemanticBaseline | None = None,
    remote_baseline: SemanticBaseline | None = None,
) -> SyncDecision:
    """Compare both sides against the last agreed baseline.

    When ``state`` has never recorded a common baseline, this is a *first
    sync* and there is no ancestor to compare against — see the module
    docstring's cases. ``local_baseline``/``remote_baseline`` are the parsed
    semantic content of each side, used only to tell case C2 (content
    genuinely agrees, e.g. the same edits packaged into byte-different
    ``.xlsx`` files) from case D (content genuinely differs) when the raw
    hashes alone cannot: without them, two differing hashes with no baseline
    are conservatively treated as case D, never guessed into case C2.
    """
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

    if last is None:
        # Case A: local only -> initial upload is safe.
        if local_hash is not None and remote_hash is None:
            return outcome(SyncAction.ADOPT_LOCAL)
        # Case B: remote only -> initial download/adopt is safe.
        if local_hash is None and remote_hash is not None:
            return outcome(SyncAction.ADOPT_REMOTE)
        # Case C1: both exist and are byte-identical — one hash is already
        # genuinely shared, so there is nothing left to transfer.
        if local_hash == remote_hash:
            return outcome(SyncAction.ADOPT_BASELINE)
        # Case C2: both exist and a semantic comparison shows they mean the
        # same thing, but the bytes differ (e.g. the same edits packaged into
        # two different .xlsx files). Not a human conflict — but no hash is
        # genuinely common yet, so local's canonical bytes still need to be
        # uploaded over Drive before one can be recorded.
        if (
            local_baseline is not None
            and remote_baseline is not None
            and semantic_baselines_equal(local_baseline, remote_baseline)
        ):
            return outcome(SyncAction.ADOPT_BASELINE_WITH_UPLOAD)
        # Case D: both exist and genuinely differ. There is no BASE to merge
        # against, so this is not an ordinary CONFLICT — it needs a
        # first-sync merge workbook instead.
        return outcome(SyncAction.FIRST_SYNC_CONFLICT)

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
    if decision.action is SyncAction.FIRST_SYNC_CONFLICT:
        lines = [
            f"FIRST SYNC — NO COMMON BASELINE: {decision.path}",
            "",
            "This computer and Google Drive have never synchronised this file "
            "before, and their content genuinely differs. There is no shared "
            "ancestor to merge against, so nothing was overwritten. Resolve it "
            "with a first-sync merge workbook (LOCAL / DRIVE / CUSTOM — BASE is "
            "not available).",
        ]
        return "\n".join(lines)

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
