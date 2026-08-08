"""Yandex Disk source adapter (skeleton — no network calls yet).

The first real implementation must support a **public** Yandex Disk folder
URL of the form ``https://disk.yandex.ru/d/<public-folder-id>`` and traverse it
**recursively**, preserving nested relative paths. The public-resources REST
API (``/v1/disk/public/resources``) returns paginated children, so traversal
needs a limit/offset loop per folder.

The class shape leaves room for a future authenticated adapter: only the
constructor would change, since the interface stays :class:`ReadableStorage`.

This adapter is read-only by contract. It must never create, modify, move or
delete anything in the source archive.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

from photoarchive.models import RemoteSourceItem, SourceRoot
from photoarchive.storage.base import StorageError

PUBLIC_RESOURCES_ENDPOINT = "https://cloud-api.yandex.net/v1/disk/public/resources"

DEFAULT_PAGE_SIZE = 200


@dataclass(frozen=True, slots=True)
class YandexDiskConfig:
    """Connection parameters for one public source root.

    ``public_url`` is supplied by the CLI at runtime and is never stored in
    ``config.yaml``.
    """

    public_url: str
    page_size: int = DEFAULT_PAGE_SIZE
    timeout_seconds: float = 30.0


class YandexDiskStorage:
    """Read-only view over one public Yandex Disk folder.

    Implements :class:`~photoarchive.storage.base.ReadableStorage`.
    """

    def __init__(self, config: YandexDiskConfig, cache_dir: Path) -> None:
        self.config = config
        self.cache_dir = Path(cache_dir)

    # -- ReadableStorage ---------------------------------------------------

    def list_recursive(self, relative_path: str = "") -> Iterable[RemoteSourceItem]:
        """Depth-first walk of the public folder.

        TODO: implement via ``GET {PUBLIC_RESOURCES_ENDPOINT}?public_key=...
        &path=...&limit=...&offset=...`` with httpx, following ``_embedded.items``
        pagination and recursing into every ``type == "dir"`` entry. Yield files
        and folders with relative paths joined by ``/``.
        """
        raise NotImplementedError("YandexDiskStorage.list_recursive is not implemented yet")

    def list_folder(self, relative_path: str = "") -> Iterator[RemoteSourceItem]:
        """Direct children of one folder.

        TODO: single paginated request against the public resources endpoint.
        """
        raise NotImplementedError("YandexDiskStorage.list_folder is not implemented yet")

    def exists(self, relative_path: str) -> bool:
        """TODO: HEAD/GET the resource and treat 404 as absent."""
        raise NotImplementedError("YandexDiskStorage.exists is not implemented yet")

    def download(self, relative_path: str, destination: Path) -> Path:
        """Fetch one source file into the local cache.

        TODO: request ``/v1/disk/public/resources/download`` for a short-lived
        href, then stream the body to ``destination``. Downloads must never
        write back to the source.
        """
        raise NotImplementedError("YandexDiskStorage.download is not implemented yet")

    def describe_root(self) -> SourceRoot:
        """Resolve the source root to a stable identity plus a folder name.

        The name becomes this root's dedicated folder under the Google Drive
        root, so it must be resolved before any mirroring happens.

        TODO: read ``name`` from the public resource metadata (``GET
        {PUBLIC_RESOURCES_ENDPOINT}?public_key=...``) and fall back to
        :func:`fallback_root_name` when the API reports no usable name.
        """
        raise NotImplementedError("YandexDiskStorage.describe_root is not implemented yet")

    # -- Helpers -----------------------------------------------------------

    @staticmethod
    def public_key_from_url(url: str) -> str:
        """Return the public key used by the API for a shared folder URL.

        For the public-resources API the full share URL *is* the public key, so
        this only validates and normalises it.
        """
        cleaned = url.strip()
        if not cleaned:
            raise StorageError("Yandex Disk source URL must not be empty")
        if not cleaned.startswith(("https://disk.yandex.", "https://yadi.sk/")):
            raise StorageError(f"Not a recognised Yandex Disk share URL: {url!r}")
        return cleaned


def fallback_root_name(url: str) -> str:
    """Derive a destination folder name from a share URL.

    Used only when the API reports no usable folder name. The public id is
    stable per share, so repeated runs keep landing in the same destination
    folder instead of creating a new one each time.
    """
    cleaned = url.strip().rstrip("/")
    _, _, tail = cleaned.partition("?")[0].rpartition("/")
    return tail or "source"
