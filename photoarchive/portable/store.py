"""Reading and writing the portable state set, safely.

Two hazards this guards against:

**Interruption.** Every file is written to a temporary sibling, flushed, and
renamed into place. A power cut leaves either the old file or the new one,
never half of either. The manifest is written *last*, so a manifest claiming
generation N implies every other file of generation N is already there.

**Concurrency.** Two machines can run at once. Before publishing, the store
re-reads the remote manifest: if the generation moved, someone else wrote state
during this run and publishing would silently discard their work. The run
aborts instead, keeping its local results intact.

The backend is a directory. Today that is a local folder; when Drive sync
exists it becomes the mirror of ``_archive_state/`` under the archive root, and
nothing above this module changes.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from photoarchive.portable.models import (
    CATALOG_FILENAME,
    MANIFEST_FILENAME,
    SCHEMA_VERSION,
    SOURCES_DIRECTORY,
    Manifest,
    SourceState,
    check_schema_version,
)
from photoarchive.portable.provenance import MachineIdentity, OperationProvenance

LOG = logging.getLogger(__name__)


class StateConflictError(RuntimeError):
    """Raised when portable state changed remotely during this run."""


@dataclass(frozen=True, slots=True)
class PortableArchiveState:
    """One coherent generation of portable state."""

    manifest: Manifest
    sources: dict[str, SourceState]
    catalog: dict[str, Any]

    @property
    def generation(self) -> int:
        return self.manifest.state_generation


class PortableStateStore:
    """Loads and publishes the portable state directory."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)

    # -- Paths ------------------------------------------------------------

    @property
    def manifest_path(self) -> Path:
        return self.root / MANIFEST_FILENAME

    @property
    def catalog_path(self) -> Path:
        return self.root / CATALOG_FILENAME

    def source_path(self, source_id: str) -> Path:
        return self.root / SOURCES_DIRECTORY / f"{source_id}.json"

    @property
    def exists(self) -> bool:
        return self.manifest_path.exists()

    # -- Reading ----------------------------------------------------------

    def read_generation(self) -> int:
        """The remote generation right now, or 0 when no state exists."""
        if not self.manifest_path.exists():
            return 0
        try:
            data = _read_json(self.manifest_path)
        except (ValueError, OSError):
            return 0
        return int(data.get("state_generation", 0))

    def load(self) -> PortableArchiveState:
        """Load the whole state set. An absent state is an empty one."""
        if not self.exists:
            return PortableArchiveState(
                manifest=Manifest(), sources={}, catalog=_empty_catalog()
            )

        manifest = Manifest.from_dict(_read_json(self.manifest_path))

        sources: dict[str, SourceState] = {}
        sources_dir = self.root / SOURCES_DIRECTORY
        if sources_dir.exists():
            for path in sorted(sources_dir.glob("*.json")):
                state = SourceState.from_dict(_read_json(path))
                sources[state.source_id] = state

        catalog = _empty_catalog()
        if self.catalog_path.exists():
            catalog = _read_json(self.catalog_path)
            check_schema_version(catalog, "catalog state")

        return PortableArchiveState(manifest=manifest, sources=sources, catalog=catalog)

    # -- Writing ----------------------------------------------------------

    def publish(
        self,
        state: PortableArchiveState,
        machine: MachineIdentity,
        run_id: str,
        expected_generation: int | None = None,
        commit: str | None = None,
    ) -> int:
        """Write a new generation of portable state, and return its number.

        ``expected_generation`` is the generation this run started from. If the
        remote has moved past it, another machine published in the meantime and
        this write would silently discard that work — so it raises
        :class:`StateConflictError` and writes nothing.
        """
        current = self.read_generation()
        if expected_generation is not None and current != expected_generation:
            raise StateConflictError(
                f"Portable state changed during this run: started from generation "
                f"{expected_generation}, remote is now {current}. Nothing was written; "
                "re-run to pick up the newer state."
            )

        generation = current + 1
        manifest = state.manifest
        manifest.state_generation = generation
        manifest.record_machine(machine.machine_id, machine.label)
        manifest.updated = OperationProvenance.create(machine, run_id, commit)
        manifest.sources = {
            source_id: source.display_name for source_id, source in state.sources.items()
        }

        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / SOURCES_DIRECTORY).mkdir(parents=True, exist_ok=True)

        # Everything except the manifest first: the manifest is the marker that
        # says "this generation is complete".
        for source_id, source in state.sources.items():
            _write_json(self.source_path(source_id), source.as_dict())

        catalog = dict(state.catalog)
        catalog["schema_version"] = SCHEMA_VERSION
        _write_json(self.catalog_path, catalog)

        _write_json(self.manifest_path, manifest.as_dict())
        LOG.info("Published portable state generation %s", generation)
        return generation


def _empty_catalog() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "people": [],
        "places": [],
        "tags": [],
        "evidence": [],
    }


def _read_json(path: Path) -> dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} does not contain a JSON object")
    return data


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    """Write JSON atomically: temp file, validate, fsync, rename."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=False) + "\n"

    # Parse what we are about to write; never publish state we cannot read back.
    json.loads(text)

    temporary = path.with_name(f"{path.name}.tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
