"""Portable archive state: what must survive a lost or replaced machine.

    GitHub          code
    Yandex Disk     source originals and descriptions
    Drive archive   workbooks, dashboard, processed photos, portable state
    local machine   disposable cache and index

``archive.sqlite`` and ``cache/`` are accelerators. Everything needed to carry
on elsewhere lives in Git, Yandex and the Drive archive.
"""

from photoarchive.portable.bootstrap import BootstrapResult, bootstrap, portable_root
from photoarchive.portable.catalog_state import export_catalog, import_catalog
from photoarchive.portable.fingerprint import (
    BUILD_FIELDS,
    build_fingerprint,
    needs_rebuild,
)
from photoarchive.portable.models import (
    BUILD_VERSION,
    SCHEMA_VERSION,
    STATE_DIRECTORY,
    ArtifactSyncState,
    ItemState,
    Manifest,
    SourceState,
    StateVersionError,
)
from photoarchive.portable.provenance import (
    MachineIdentity,
    OperationProvenance,
    app_commit,
    load_machine_identity,
)
from photoarchive.portable.store import (
    PortableArchiveState,
    PortableStateStore,
    StateConflictError,
)
from photoarchive.portable.sync import (
    SyncAction,
    SyncDecision,
    content_hash,
    decide,
    describe_conflict,
    file_hash,
    record_sync,
)

__all__ = [
    "ArtifactSyncState",
    "BUILD_FIELDS",
    "BUILD_VERSION",
    "BootstrapResult",
    "ItemState",
    "MachineIdentity",
    "Manifest",
    "OperationProvenance",
    "PortableArchiveState",
    "PortableStateStore",
    "SCHEMA_VERSION",
    "STATE_DIRECTORY",
    "SourceState",
    "StateConflictError",
    "StateVersionError",
    "SyncAction",
    "SyncDecision",
    "app_commit",
    "bootstrap",
    "build_fingerprint",
    "content_hash",
    "decide",
    "describe_conflict",
    "export_catalog",
    "file_hash",
    "import_catalog",
    "load_machine_identity",
    "needs_rebuild",
    "portable_root",
    "record_sync",
]
