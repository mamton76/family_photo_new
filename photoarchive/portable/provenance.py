"""Who did what, when, with which version of the code.

Hashes tell the *machine* whether two things are the same. They tell a *person*
nothing. Six months after the fact, ``"last_synced_hash": "8d3f17…"`` cannot
answer "which computer wrote this, and was that before or after I fixed the
place names?" — so every recorded hash is wrapped in a provenance record
carrying a timestamp, a machine, a run id and the code version.

Provenance is diagnostic only. It never decides correctness: sync decisions
compare content hashes, and rebuild decisions compare fingerprints. A machine
whose id changed after an OS reinstall must still synchronise correctly.

Nothing personal is recorded — no username, home directory, project path or IP.
A machine is an opaque id plus a label a person chose.
"""

from __future__ import annotations

import json
import logging
import os
import platform
import subprocess
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

LOG = logging.getLogger(__name__)

#: Where this installation's identity lives. Local, disposable, gitignored —
#: losing it costs a line of provenance, never correctness.
DEFAULT_MACHINE_FILE = Path.home() / ".family-photo-archive" / "machine.json"

UNKNOWN_COMMIT = "unknown"

#: Marks a commit whose working tree had uncommitted changes.
DIRTY_SUFFIX = "-dirty"


def utc_now() -> datetime:
    """Current time, always UTC. Persisted state never carries local time."""
    return datetime.now(tz=timezone.utc)


def format_timestamp(moment: datetime | None = None) -> str:
    """ISO-8601 in UTC with a trailing ``Z``."""
    moment = moment or utc_now()
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


@dataclass(frozen=True, slots=True)
class MachineIdentity:
    """One local installation: an opaque id plus a human label."""

    machine_id: str
    label: str

    def as_dict(self) -> dict[str, str]:
        return {"machine_id": self.machine_id, "label": self.label}


def load_machine_identity(
    path: Path | str = DEFAULT_MACHINE_FILE, label: str | None = None
) -> MachineIdentity:
    """Return this machine's identity, creating it once on first use.

    The id is a UUID generated locally and then reused forever. It deliberately
    does not derive from the hostname, which people change. ``label`` overrides
    the stored label — that is how a person renames "MacBook-Pro-3.local" into
    "Tonya MacBook".
    """
    path = Path(path)
    stored: dict[str, str] = {}

    if path.exists():
        try:
            stored = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError) as error:
            LOG.debug("Unreadable machine identity at %s: %s", path, error)
            stored = {}

    machine_id = str(stored.get("machine_id") or "").strip() or uuid.uuid4().hex
    resolved = (
        (label or "").strip()
        or str(stored.get("label") or "").strip()
        or default_machine_label()
    )

    if stored.get("machine_id") != machine_id or stored.get("label") != resolved:
        _persist_machine(path, machine_id, resolved)

    return MachineIdentity(machine_id=machine_id, label=resolved)


def default_machine_label() -> str:
    """A readable starting label. Overridable, and never used as a key."""
    name = platform.node().split(".")[0].strip()
    return name or "unknown machine"


def _persist_machine(path: Path, machine_id: str, label: str) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            {"machine_id": machine_id, "label": label}, indent=2, ensure_ascii=False
        )
        temporary = path.with_name(f"{path.name}.tmp")
        temporary.write_text(payload + "\n", encoding="utf-8")
        os.replace(temporary, path)
    except OSError as error:
        # A read-only home is inconvenient, not fatal: the run still works, it
        # just re-generates an id next time.
        LOG.debug("Could not persist machine identity to %s: %s", path, error)


def app_commit(repository: Path | str | None = None) -> str:
    """The code version behind an operation, honestly reported.

    Returns a short commit hash, suffixed ``-dirty`` when the working tree has
    uncommitted changes, or ``"unknown"`` when there is no usable Git metadata.
    A dirty tree is never presented as though it were a clean commit — the
    whole point is being able to ask later which code produced an artifact.
    """
    directory = Path(repository) if repository else Path(__file__).resolve().parent.parent.parent

    commit = _git(directory, "rev-parse", "--short", "HEAD")
    if not commit:
        return UNKNOWN_COMMIT

    status = _git(directory, "status", "--porcelain")
    if status is None:
        return commit
    return f"{commit}{DIRTY_SUFFIX}" if status.strip() else commit


def _git(directory: Path, *arguments: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", *arguments],
            cwd=directory,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        LOG.debug("git %s failed: %s", " ".join(arguments), error)
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip()


@dataclass(frozen=True, slots=True)
class OperationProvenance:
    """Who/when/what-version, attached to a recorded hash or fingerprint."""

    machine_id: str = ""
    machine_label: str = ""
    run_id: str = ""
    app_commit: str = UNKNOWN_COMMIT
    at: str = field(default_factory=format_timestamp)

    @classmethod
    def create(
        cls,
        machine: MachineIdentity,
        run_id: str,
        commit: str | None = None,
        moment: datetime | None = None,
    ) -> OperationProvenance:
        return cls(
            machine_id=machine.machine_id,
            machine_label=machine.label,
            run_id=run_id,
            app_commit=commit or app_commit(),
            at=format_timestamp(moment),
        )

    def as_dict(self) -> dict[str, str]:
        return {
            "at": self.at,
            "machine_id": self.machine_id,
            "machine_label": self.machine_label,
            "run_id": self.run_id,
            "app_commit": self.app_commit,
        }

    @classmethod
    def from_dict(cls, data: dict | None) -> OperationProvenance | None:
        if not data:
            return None
        return cls(
            machine_id=str(data.get("machine_id", "")),
            machine_label=str(data.get("machine_label", "")),
            run_id=str(data.get("run_id", "")),
            app_commit=str(data.get("app_commit", UNKNOWN_COMMIT)),
            at=str(data.get("at", "")),
        )

    def describe(self) -> str:
        """One human-readable line, for conflict and summary reports."""
        moment = parse_timestamp(self.at)
        when = moment.strftime("%d %b %Y %H:%M UTC") if moment else self.at or "unknown time"
        machine = self.machine_label or self.machine_id or "unknown machine"
        return f"{when} · machine: {machine} · run: {self.run_id} · app commit: {self.app_commit}"
