"""The portable state that lives with the archive, not with the machine.

The ownership split this implements:

``review.xlsx`` / ``catalog.xlsx``
    human truth — edited by people, synchronised conservatively

``_archive_state/*.json``
    **portable machine truth** — identities, hashes, Drive ids, fingerprints,
    sync baselines, evidence. Machine-oriented facts that would clutter a
    spreadsheet but must survive a dead laptop.

processed photos
    built artifacts

``review-all.html``
    generated view

``archive.sqlite`` / ``cache/``
    disposable local index; rebuildable from the above

Everything here is JSON: readable, diffable, and not tied to a SQLite file
format. The SQLite database is an accelerator, never the synchronisation
primitive.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from photoarchive.portable.provenance import OperationProvenance, format_timestamp

#: Bumped only for incompatible changes; unknown future versions are refused
#: rather than half-understood.
SCHEMA_VERSION = 1

#: Separate from the schema: it changes when the *meaning* of a build changes
#: (new metadata mapping), which must invalidate every fingerprint.
BUILD_VERSION = 1

STATE_DIRECTORY = "_archive_state"
MANIFEST_FILENAME = "manifest.json"
CATALOG_FILENAME = "catalog.json"
SOURCES_DIRECTORY = "sources"


class StateVersionError(RuntimeError):
    """Raised when portable state was written by an incompatible version."""


def check_schema_version(data: dict[str, Any], origin: str) -> None:
    """Refuse state this build cannot faithfully interpret."""
    version = data.get("schema_version")
    if version is None:
        raise StateVersionError(f"{origin} has no schema_version")
    if not isinstance(version, int):
        raise StateVersionError(f"{origin} has a non-integer schema_version: {version!r}")
    if version > SCHEMA_VERSION:
        raise StateVersionError(
            f"{origin} was written by a newer version (schema {version}; "
            f"this build understands {SCHEMA_VERSION}). Upgrade before continuing."
        )


@dataclass(slots=True)
class ArtifactSyncState:
    """The sync baseline for one human-editable file.

    ``last_common_hash`` is the content both sides agreed on at the last
    successful sync. Three-way comparison against it is what distinguishes
    "the other machine edited this" from "we both did".
    """

    path: str
    drive_file_id: str | None = None
    last_common_hash: str | None = None
    last_sync: OperationProvenance | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "drive_file_id": self.drive_file_id,
            "last_common_hash": self.last_common_hash,
            "last_sync": self.last_sync.as_dict() if self.last_sync else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ArtifactSyncState:
        return cls(
            path=str(data.get("path", "")),
            drive_file_id=data.get("drive_file_id"),
            last_common_hash=data.get("last_common_hash"),
            last_sync=OperationProvenance.from_dict(data.get("last_sync")),
        )


@dataclass(slots=True)
class ItemState:
    """Portable machine state for one logical photo.

    The Drive file id is the point: after a clean-machine bootstrap, an upload
    must *update* the existing file rather than creating ``001 (1).jpg``.
    """

    key: str
    relative_path: str = ""
    remote_id: str | None = None
    source_hash: str | None = None
    size: int | None = None
    modified_at: str | None = None

    drive_file_id: str | None = None
    drive_path: str | None = None

    build_fingerprint: str | None = None
    built_hash: str | None = None
    last_build: OperationProvenance | None = None

    google_photos_media_id: str | None = None
    google_photos_product_url: str | None = None
    published_at: str | None = None

    status: str | None = None

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "key": self.key,
            "relative_path": self.relative_path,
            "remote_id": self.remote_id,
            "source_hash": self.source_hash,
            "size": self.size,
            "modified_at": self.modified_at,
            "drive_file_id": self.drive_file_id,
            "drive_path": self.drive_path,
            "status": self.status,
        }
        if self.build_fingerprint or self.last_build:
            payload["last_build"] = {
                "fingerprint": self.build_fingerprint,
                "built_hash": self.built_hash,
                "build_version": BUILD_VERSION,
                **(self.last_build.as_dict() if self.last_build else {}),
            }
        if self.google_photos_media_id:
            payload["google_photos"] = {
                "media_id": self.google_photos_media_id,
                "product_url": self.google_photos_product_url,
                "published_at": self.published_at,
            }
        return payload

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ItemState:
        build = data.get("last_build") or {}
        photos = data.get("google_photos") or {}
        return cls(
            key=str(data.get("key", "")),
            relative_path=str(data.get("relative_path", "")),
            remote_id=data.get("remote_id"),
            source_hash=data.get("source_hash"),
            size=data.get("size"),
            modified_at=data.get("modified_at"),
            drive_file_id=data.get("drive_file_id"),
            drive_path=data.get("drive_path"),
            build_fingerprint=build.get("fingerprint"),
            built_hash=build.get("built_hash"),
            last_build=OperationProvenance.from_dict(build) if build else None,
            google_photos_media_id=photos.get("media_id"),
            google_photos_product_url=photos.get("product_url"),
            published_at=photos.get("published_at"),
            status=data.get("status"),
        )


@dataclass(slots=True)
class SourceState:
    """Everything portable about one Yandex source root."""

    source_id: str
    source_url: str
    display_name: str
    drive_folder_id: str | None = None
    last_scan: OperationProvenance | None = None
    items: dict[str, ItemState] = field(default_factory=dict)
    #: Sync baselines for the per-folder workbooks under this root.
    artifacts: dict[str, ArtifactSyncState] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "source_id": self.source_id,
            "source_url": self.source_url,
            "display_name": self.display_name,
            "drive_folder_id": self.drive_folder_id,
            "last_scan": self.last_scan.as_dict() if self.last_scan else None,
            # Sorted so an unchanged archive serialises byte-identically.
            "items": {key: self.items[key].as_dict() for key in sorted(self.items)},
            "artifacts": {
                key: self.artifacts[key].as_dict() for key in sorted(self.artifacts)
            },
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SourceState:
        check_schema_version(data, "source state")
        return cls(
            source_id=str(data.get("source_id", "")),
            source_url=str(data.get("source_url", "")),
            display_name=str(data.get("display_name", "")),
            drive_folder_id=data.get("drive_folder_id"),
            last_scan=OperationProvenance.from_dict(data.get("last_scan")),
            items={
                key: ItemState.from_dict(value)
                for key, value in (data.get("items") or {}).items()
            },
            artifacts={
                key: ArtifactSyncState.from_dict(value)
                for key, value in (data.get("artifacts") or {}).items()
            },
        )


@dataclass(slots=True)
class MachineRecord:
    """A machine that has touched this archive. Informational only."""

    label: str
    first_seen: str = field(default_factory=format_timestamp)
    last_seen: str = field(default_factory=format_timestamp)

    def as_dict(self) -> dict[str, str]:
        return {
            "label": self.label,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MachineRecord:
        return cls(
            label=str(data.get("label", "")),
            first_seen=str(data.get("first_seen", "")),
            last_seen=str(data.get("last_seen", "")),
        )


@dataclass(slots=True)
class Manifest:
    """The top of the portable state, and its concurrency guard.

    ``state_generation`` is written **last**, so a manifest at generation N
    means every file of generation N landed. A run records the generation it
    started from and refuses to publish over a newer one.
    """

    state_generation: int = 0
    machines: dict[str, MachineRecord] = field(default_factory=dict)
    sources: dict[str, str] = field(default_factory=dict)
    #: Archive-wide editable artifacts, e.g. ``catalog.xlsx``.
    artifacts: dict[str, ArtifactSyncState] = field(default_factory=dict)
    updated: OperationProvenance | None = None

    def record_machine(self, machine_id: str, label: str) -> None:
        existing = self.machines.get(machine_id)
        now = format_timestamp()
        if existing is None:
            self.machines[machine_id] = MachineRecord(
                label=label, first_seen=now, last_seen=now
            )
        else:
            existing.label = label or existing.label
            existing.last_seen = now

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "build_version": BUILD_VERSION,
            "state_generation": self.state_generation,
            "updated": self.updated.as_dict() if self.updated else None,
            "machines": {
                key: self.machines[key].as_dict() for key in sorted(self.machines)
            },
            "sources": {key: self.sources[key] for key in sorted(self.sources)},
            "artifacts": {
                key: self.artifacts[key].as_dict() for key in sorted(self.artifacts)
            },
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Manifest:
        check_schema_version(data, "manifest")
        return cls(
            state_generation=int(data.get("state_generation", 0)),
            machines={
                key: MachineRecord.from_dict(value)
                for key, value in (data.get("machines") or {}).items()
            },
            sources={
                str(key): str(value)
                for key, value in (data.get("sources") or {}).items()
            },
            artifacts={
                key: ArtifactSyncState.from_dict(value)
                for key, value in (data.get("artifacts") or {}).items()
            },
            updated=OperationProvenance.from_dict(data.get("updated")),
        )
