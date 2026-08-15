"""Building the current portable snapshot from real application state.

One path, used by both ``run`` and ``bootstrap --publish``, so a snapshot never
depends on which command produced it.

    real Yandex listing
    + source root identities
    + per-folder review RowState
    + dictionary and evidence
    + durable fields already in portable state
    -> current PortableArchiveState

The last input matters most. A snapshot **merges into** the previous state
rather than replacing it: Drive file ids, build fingerprints and Google Photos
ids cannot be recreated from a scan, so overwriting them with scan-only data
would silently destroy the very information that prevents duplicate uploads
and needless rebuilds.

Two different things are recorded per source, and both are needed:

* **source items** — the files the provider actually reported;
* **logical review rows** — every row in ``review.xlsx``, including
  ``DESCRIBED_ABSENT`` rows that have no file behind them at all.

A folder of 12 photos described by a DOCX naming 24 references yields 12 of the
first and 24 of the second.

This module knows nothing about Google Drive. It builds state; publishing it
through a store or backend is somebody else's job.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from photoarchive.catalog.store import DictionaryStore
from photoarchive.models import RemoteSourceItem, SourceRoot
from photoarchive.portable.catalog_state import export_catalog
from photoarchive.coverage import FolderDescriptionStatus
from photoarchive.portable.models import (
    ArtifactSyncState,
    ItemState,
    Manifest,
    FolderDescriptionRecord,
    SourceItemObservation,
    SourceState,
)
from photoarchive.portable.provenance import MachineIdentity, OperationProvenance
from photoarchive.portable.store import PortableArchiveState, PortableStateStore
from photoarchive.portable.sync import file_hash
from photoarchive.review.builder import RowState
from photoarchive.state import StateRepository

LOG = logging.getLogger(__name__)


@dataclass(slots=True)
class ScannedSource:
    """What one real scan observed, handed to the exporter unchanged.

    The listing is passed in rather than re-fetched: exporting portable state
    must never cost another round trip to Yandex.
    """

    source_root: SourceRoot
    items: list[RemoteSourceItem] = field(default_factory=list)
    #: ``folder path -> {identity -> RowState}``, exactly as the scan produced.
    row_states: dict[str, dict[str, RowState]] = field(default_factory=dict)
    #: ``folder path -> FolderDescriptionRecord`` for every folder the scan
    #: planned. Only a real scan can observe this, which is why it is passed in
    #: rather than derived: an absent record means "not observed".
    folders: dict[str, FolderDescriptionRecord] = field(default_factory=dict)
    #: Local paths of the workbooks written for this source.
    workbooks: list[Path] = field(default_factory=list)


def folder_description_record(plan, error: str | None = None) -> FolderDescriptionRecord:
    """Resolve one scanned folder's description state, once, at the source.

    The rule that picks a document — exactly one wins, several are a conflict,
    zero means none — lives in :class:`~photoarchive.scanning.scanner.FolderScanPlan`.
    Recording the *outcome* here is what stops the dashboard from having to
    reimplement it later, from data that cannot express the difference.

    A document that exists but could not be read is not a source condition; the
    folder simply stays unobserved rather than being reported as having none.
    """
    if error:
        status = FolderDescriptionStatus.UNKNOWN
    elif plan.has_ambiguous_description:
        status = FolderDescriptionStatus.AMBIGUOUS
    elif plan.has_description:
        status = FolderDescriptionStatus.FOUND
    else:
        status = FolderDescriptionStatus.ABSENT

    document = plan.description
    return FolderDescriptionRecord(
        folder_path=plan.folder_path,
        status=status.value,
        document=document.name if document else None,
        candidates=[item.name for item in plan.docx_candidates],
    )


def row_key(folder_path: str, identity: str) -> str:
    """Key a logical review row by folder and identity, never by name alone."""
    return f"{folder_path}|{identity}"


def build_portable_snapshot(
    scanned: list[ScannedSource],
    dictionary: DictionaryStore,
    previous: PortableArchiveState,
    machine: MachineIdentity,
    run_id: str,
    commit: str | None = None,
    catalog_workbook: Path | None = None,
) -> PortableArchiveState:
    """Merge fresh scan facts into the previous portable state."""
    provenance = OperationProvenance.create(machine, run_id, commit)

    manifest = previous.manifest
    sources = dict(previous.sources)

    for observation in scanned:
        source_id = observation.source_root.identity
        sources[source_id] = _merge_source(
            previous=sources.get(source_id),
            observation=observation,
            provenance=provenance,
        )

    if catalog_workbook is not None:
        manifest.artifacts["catalog.xlsx"] = _merge_artifact(
            manifest.artifacts.get("catalog.xlsx"), "catalog.xlsx", catalog_workbook
        )

    return PortableArchiveState(
        manifest=manifest,
        sources=sources,
        catalog=export_catalog(dictionary),
    )


def _merge_source(
    previous: SourceState | None,
    observation: ScannedSource,
    provenance: OperationProvenance,
) -> SourceState:
    root = observation.source_root
    state = previous or SourceState(
        source_id=root.identity, source_url=root.url, display_name=root.name
    )

    # A renamed Yandex folder edits the same source; identity comes from the URL.
    state.source_url = root.url
    state.display_name = root.name
    state.last_scan = provenance

    state.source_items = {
        item.relative_path: SourceItemObservation(
            relative_path=item.relative_path,
            remote_id=item.remote_id,
            is_directory=item.is_directory,
            size=item.size,
            modified_at=item.modified_at.isoformat() if item.modified_at else None,
            content_hash=item.content_hash,
        )
        for item in observation.items
        if item.relative_path
    }

    physical = {
        item.relative_path.rsplit("/", 1)[-1]: item
        for item in observation.items
        if not item.is_directory
    }

    refreshed: dict[str, ItemState] = {}
    for folder_path, rows in observation.row_states.items():
        for identity, row_state in rows.items():
            key = row_key(folder_path, identity)
            # Keep whatever the previous state knew that a scan cannot know.
            item = state.items.get(key) or ItemState(key=key)
            item.key = key
            _apply_row_state(item, row_state)
            _apply_physical(item, folder_path, identity, physical)
            refreshed[key] = item

    state.items = refreshed
    # Folders the scan actually visited. Others keep whatever was known, so a
    # scoped run never downgrades an untouched folder to "not observed".
    state.folders.update(observation.folders)
    _merge_workbook_artifacts(state, observation)
    return state


def _apply_row_state(item: ItemState, row_state: RowState) -> None:
    """Copy the whole ``RowState``, not just the photo hash.

    Restoring a partial row state on a clean machine would make the next scan
    report every description and suggestion as changed.
    """
    item.source_hash = row_state.photo_hash or item.source_hash
    item.description_hash = row_state.description_hash or None
    item.suggestion_hash = row_state.suggestion_hash or None
    item.status = row_state.status or item.status
    item.was_absent = row_state.was_absent
    item.source_entry_exists = row_state.source_entry_exists


def _apply_physical(
    item: ItemState, folder_path: str, identity: str, physical: dict[str, RemoteSourceItem]
) -> None:
    """Attach provider facts when a real file backs this row.

    ``DESCRIBED_ABSENT`` rows legitimately have none, and keep whatever the
    portable state already recorded.
    """
    from photoarchive.review.excel import identity_key

    for name, source_item in physical.items():
        if identity_key(name) != identity:
            continue
        item.relative_path = source_item.relative_path
        item.remote_id = source_item.remote_id
        item.size = source_item.size
        item.modified_at = (
            source_item.modified_at.isoformat() if source_item.modified_at else None
        )
        if source_item.content_hash:
            item.source_hash = item.source_hash or source_item.content_hash
        return


def _merge_workbook_artifacts(state: SourceState, observation: ScannedSource) -> None:
    for workbook in observation.workbooks:
        key = workbook.name
        state.artifacts[key] = _merge_artifact(
            state.artifacts.get(key), str(workbook), workbook
        )


def _merge_artifact(
    previous: ArtifactSyncState | None, path: str, local_path: Path
) -> ArtifactSyncState:
    """Record what a workbook contains locally, without claiming it is synced.

    ``last_common_hash`` and ``drive_file_id`` are left exactly as they were —
    they may only be set by a real transfer. Writing the local hash into them
    would make a future three-way sync believe Drive had already seen it.
    """
    state = previous or ArtifactSyncState(path=path)
    state.path = path
    state.local_content_hash = file_hash(local_path)
    return state


def publish_snapshot(
    store: PortableStateStore,
    snapshot: PortableArchiveState,
    machine: MachineIdentity,
    run_id: str,
    expected_generation: int,
    commit: str | None = None,
) -> tuple[int, bool]:
    """Publish a snapshot, skipping the write when nothing meaningful changed.

    Returns ``(generation, written)``. Comparison ignores provenance — a run id
    and a timestamp differ on every run, and letting them bump the generation
    would make an untouched archive look perpetually modified, and would make
    concurrent-run detection meaningless.
    """
    if _matches_published(store, snapshot):
        LOG.info("Portable state unchanged; leaving generation %s", expected_generation)
        return expected_generation, False

    generation = store.publish(
        snapshot, machine, run_id, expected_generation=expected_generation, commit=commit
    )
    return generation, True


def _matches_published(
    store: PortableStateStore, snapshot: PortableArchiveState
) -> bool:
    """Whether the deterministic content of the snapshot is already published."""
    if not store.exists:
        return False
    try:
        current = store.load()
    except Exception:  # noqa: BLE001 - unreadable state must simply be rewritten
        return False

    return _comparable(current) == _comparable(snapshot)


def _comparable(state: PortableArchiveState) -> dict:
    """The parts of a state that mean something, with provenance stripped."""
    sources = {}
    for source_id, source in state.sources.items():
        payload = source.as_dict()
        payload.pop("last_scan", None)
        sources[source_id] = payload

    catalog = dict(state.catalog)
    catalog.pop("exported_at", None)

    return {
        "sources": sources,
        "catalog": catalog,
        "artifacts": {
            key: value.as_dict() for key, value in state.manifest.artifacts.items()
        },
    }


def scanned_from_state(
    state: StateRepository, source_root: SourceRoot, items: list[RemoteSourceItem]
) -> ScannedSource:
    """Rebuild a :class:`ScannedSource` from what SQLite already recorded.

    Used by ``bootstrap --publish``, which repairs portable state from a
    healthy local database without re-running a full scan.
    """
    row_states: dict[str, dict[str, RowState]] = {}
    with state.connect() as connection:
        folders = connection.execute(
            "SELECT DISTINCT folder_path FROM review_rows WHERE root_identity = ?",
            (source_root.identity,),
        ).fetchall()

    for row in folders:
        folder = row["folder_path"]
        row_states[folder] = state.load_row_states(source_root.identity, folder)

    return ScannedSource(source_root=source_root, items=items, row_states=row_states)
