"""Rebuilding a working machine from portable state.

The promise this module keeps: delete ``archive.sqlite`` and ``cache/``, move
to another computer, and carry on — without duplicating Drive files, losing
dictionary knowledge, or rebuilding photos that were already built.

Everything reconstructed here is derived. The inputs are the portable state and
the workbooks; the output is a local index that makes the next run fast. If the
index is wrong, deleting it and bootstrapping again is always safe.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from photoarchive.catalog.store import DictionaryStore
from photoarchive.models import SourceRoot
from photoarchive.portable.catalog_state import import_catalog
from photoarchive.portable.store import PortableArchiveState, PortableStateStore
from photoarchive.review.builder import RowState
from photoarchive.state import StateRepository

LOG = logging.getLogger(__name__)


@dataclass(slots=True)
class BootstrapResult:
    """What a bootstrap restored, for the run summary."""

    generation: int = 0
    source_roots: int = 0
    items: int = 0
    drive_ids: int = 0
    built_items: int = 0
    catalog_counts: dict[str, int] = field(default_factory=dict)
    machines: list[str] = field(default_factory=list)

    @property
    def restored_anything(self) -> bool:
        return bool(self.source_roots or self.items or self.catalog_counts)


def bootstrap(
    portable: PortableStateStore,
    state: StateRepository,
    dictionary: DictionaryStore,
) -> BootstrapResult:
    """Rebuild the local index from portable state. Safe to run repeatedly.

    Nothing is deleted: entities and rows are restored under their original
    ids, so running this over a healthy database is a no-op rather than a
    duplication.
    """
    loaded = portable.load()
    state.initialize()
    dictionary.initialize()

    result = BootstrapResult(generation=loaded.generation)
    result.machines = [
        record.label for record in loaded.manifest.machines.values() if record.label
    ]

    for source in loaded.sources.values():
        state.register_source_root(
            SourceRoot(url=source.source_url, name=source.display_name)
        )
        result.source_roots += 1
        result.items += len(source.items)
        result.drive_ids += sum(1 for item in source.items.values() if item.drive_file_id)
        result.built_items += sum(
            1 for item in source.items.values() if item.build_fingerprint
        )
        _restore_row_states(state, source)

    result.catalog_counts = import_catalog(dictionary, loaded.catalog)
    LOG.info(
        "Bootstrapped from generation %s: %s source roots, %s items, %s Drive ids",
        result.generation,
        result.source_roots,
        result.items,
        result.drive_ids,
    )
    return result


def _restore_row_states(state: StateRepository, source) -> None:
    """Re-seed the per-row bookkeeping a rescan compares against.

    Without this a clean machine would treat every row as new and flag the
    whole archive for review; with it, an unchanged source is recognised as
    unchanged.
    """
    identity = SourceRoot(url=source.source_url, name=source.display_name).identity
    by_folder: dict[str, dict[str, RowState]] = {}

    for key, item in source.items.items():
        folder, _, row_key = key.rpartition("|")
        by_folder.setdefault(folder, {})[row_key or key] = RowState(
            identity=row_key or key,
            photo_hash=item.source_hash or "",
            status=item.status or "",
        )

    for folder, rows in by_folder.items():
        state.save_row_states(identity, folder, rows)


def portable_root(archive_root: Path | str) -> Path:
    """The portable state directory under an archive root."""
    from photoarchive.portable.models import STATE_DIRECTORY

    return Path(archive_root) / STATE_DIRECTORY
