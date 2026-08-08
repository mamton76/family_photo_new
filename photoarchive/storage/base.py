"""Storage provider interfaces.

The pipeline talks to two very different clouds, so the contract is split:

* :class:`ReadableStorage` — what a *source* must offer (Yandex Disk). It is
  intentionally read-only: there is no method here that can mutate a source
  archive.
* :class:`WritableStorage` — what a *destination* must offer (Google Drive).

Domain and scanning code depends on these protocols only. Provider concepts
(public-folder keys, Drive file IDs, API pagination) must not leak upwards;
paths crossing this boundary are ``/``-separated and relative to the root the
provider was constructed with.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Protocol, runtime_checkable

from photoarchive.models import RemoteSourceItem


class StorageError(RuntimeError):
    """Provider-neutral storage failure."""


@runtime_checkable
class ReadableStorage(Protocol):
    """A remote location that can be enumerated and downloaded from."""

    def list_recursive(self, relative_path: str = "") -> Iterable[RemoteSourceItem]:
        """Yield every file and folder below ``relative_path``.

        Paths in the yielded items are relative to the storage root, so the
        hierarchy can be mirrored verbatim onto a destination.
        """
        ...

    def list_folder(self, relative_path: str = "") -> Iterable[RemoteSourceItem]:
        """Yield the direct children of one folder (no recursion)."""
        ...

    def exists(self, relative_path: str) -> bool:
        """Report whether an item exists at ``relative_path``."""
        ...

    def download(self, relative_path: str, destination: Path) -> Path:
        """Copy a remote file into the local cache and return its local path."""
        ...


@runtime_checkable
class WritableStorage(ReadableStorage, Protocol):
    """A remote location the pipeline may create folders and files in."""

    def ensure_folder(self, relative_path: str) -> str:
        """Create the folder path if needed and return its provider id.

        Must be idempotent: an existing folder is reused, never duplicated.
        """
        ...

    def upload(self, local_path: Path, relative_path: str, *, overwrite: bool = True) -> str:
        """Upload a local file and return the resulting provider file id."""
        ...
