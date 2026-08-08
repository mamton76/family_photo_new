"""Yandex Disk source adapter over the official public-resources API.

This adapter reads **public** Yandex Disk folders — a share URL of the form
``https://disk.yandex.ru/d/<public-folder-id>`` — with no authentication, no
OAuth and no access token. It uses the documented JSON endpoints under
``https://cloud-api.yandex.net/v1/disk/public/resources`` and never scrapes the
web UI.

**The adapter is strictly read-only.** Every request it issues is a ``GET``;
there is deliberately no upload, delete, move, rename or overwrite method
anywhere in this module, so no code path can mutate the source archive.

A future authenticated adapter can be added alongside this one: all
public-API specifics are confined to this file, and the rest of the
application depends only on the provider-neutral protocols in
:mod:`photoarchive.storage.base`.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from photoarchive.models import RemoteSourceItem, SourceRoot
from photoarchive.storage.base import StorageError

LOG = logging.getLogger(__name__)

PUBLIC_RESOURCES_ENDPOINT = "https://cloud-api.yandex.net/v1/disk/public/resources"
PUBLIC_DOWNLOAD_ENDPOINT = f"{PUBLIC_RESOURCES_ENDPOINT}/download"

USER_AGENT = "family-photo-archive/0.1"

DEFAULT_PAGE_SIZE = 200

#: Depth guard, so a pathological or looping hierarchy cannot recurse forever.
MAX_TRAVERSAL_DEPTH = 64


@dataclass(frozen=True, slots=True)
class YandexDiskConfig:
    """Connection parameters for one public source root.

    ``public_url`` is supplied by the CLI at runtime and is never stored in
    ``config.yaml``. There is no credential field here by design: this phase is
    public-access only.
    """

    public_url: str
    page_size: int = DEFAULT_PAGE_SIZE
    timeout_seconds: float = 30.0


class YandexDiskStorage:
    """Read-only view over one public Yandex Disk folder.

    Implements :class:`~photoarchive.storage.base.ReadableStorage`.

    The HTTP client is reused across requests. Pass ``client`` to inject one
    (the unit tests use :class:`httpx.MockTransport`); otherwise a client is
    created lazily on first use and closed by :meth:`close`.
    """

    def __init__(
        self,
        config: YandexDiskConfig,
        cache_dir: Path,
        client: httpx.Client | None = None,
    ) -> None:
        self.config = config
        self.cache_dir = Path(cache_dir)
        self.public_key = self.public_key_from_url(config.public_url)
        self._client = client
        self._owns_client = client is None

    # -- HTTP plumbing -----------------------------------------------------

    @property
    def client(self) -> httpx.Client:
        """The reusable HTTP client, created on first use."""
        if self._client is None:
            self._client = httpx.Client(
                timeout=httpx.Timeout(self.config.timeout_seconds),
                headers={"User-Agent": USER_AGENT},
                follow_redirects=True,
            )
            self._owns_client = True
        return self._client

    def close(self) -> None:
        """Close the HTTP client, but only if this instance created it."""
        if self._client is not None and self._owns_client:
            self._client.close()
            self._client = None

    def __enter__(self) -> YandexDiskStorage:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def _get_json(
        self,
        url: str,
        params: dict[str, Any],
        *,
        allow_404: bool = False,
    ) -> dict[str, Any] | None:
        """GET a JSON document, mapping every failure to :class:`StorageError`.

        Returns ``None`` only for a 404 when ``allow_404`` is set. Response
        bodies are never logged: they can be large and may carry signed URLs.
        """
        try:
            response = self.client.get(url, params=params)
        except httpx.TimeoutException as error:
            raise StorageError(f"Yandex Disk request timed out: {url}") from error
        except httpx.HTTPError as error:
            raise StorageError(f"Yandex Disk request failed: {type(error).__name__}") from error

        if response.status_code == 404 and allow_404:
            return None
        if response.status_code >= 400:
            raise StorageError(
                f"Yandex Disk returned HTTP {response.status_code} for {url}"
            )

        try:
            payload = response.json()
        except ValueError as error:
            raise StorageError(f"Yandex Disk returned malformed JSON for {url}") from error

        if not isinstance(payload, dict):
            raise StorageError(f"Yandex Disk returned an unexpected payload for {url}")
        return payload

    # -- ReadableStorage ---------------------------------------------------

    def describe_root(self) -> SourceRoot:
        """Resolve the source root to a stable identity plus a folder name.

        The name is read from the public resource metadata, so it works for any
        public folder. If the API reports no usable name, the public id is used
        instead (see :func:`fallback_root_name`) rather than failing the scan.
        """
        payload = self._get_json(
            PUBLIC_RESOURCES_ENDPOINT, {"public_key": self.public_key, "limit": 0}
        )
        assert payload is not None  # allow_404 is off, so this cannot be None.

        name = payload.get("name")
        if not isinstance(name, str) or not name.strip():
            LOG.debug("Public resource reported no usable name; using URL fallback")
            name = fallback_root_name(self.config.public_url)

        remote_id = payload.get("resource_id") or payload.get("public_key")
        return SourceRoot(
            url=self.config.public_url,
            name=name.strip(),
            remote_id=str(remote_id) if remote_id else None,
        )

    def list_folder(self, relative_path: str = "") -> list[RemoteSourceItem]:
        """Return the direct children of one folder, following pagination.

        Yandex returns children in pages; this walks ``offset`` in steps of the
        configured ``page_size`` until every child has been retrieved.
        """
        api_path = _api_path(relative_path)
        collected: list[RemoteSourceItem] = []
        offset = 0
        limit = max(1, int(self.config.page_size))

        while True:
            payload = self._get_json(
                PUBLIC_RESOURCES_ENDPOINT,
                {
                    "public_key": self.public_key,
                    "path": api_path,
                    "limit": limit,
                    "offset": offset,
                },
            )
            assert payload is not None

            embedded = payload.get("_embedded")
            if not isinstance(embedded, dict):
                # A file has no _embedded block; only folders can be listed.
                raise StorageError(f"Not a listable folder on Yandex Disk: {api_path}")

            raw_items = embedded.get("items")
            if not isinstance(raw_items, list):
                raise StorageError(f"Yandex Disk returned no item list for {api_path}")

            for raw in raw_items:
                if isinstance(raw, dict):
                    collected.append(_to_source_item(raw, relative_path))

            total = embedded.get("total")
            offset += len(raw_items)
            if not raw_items or len(raw_items) < limit:
                break
            if isinstance(total, int) and offset >= total:
                break

        return collected

    def list_recursive(self, relative_path: str = "") -> Iterator[RemoteSourceItem]:
        """Depth-first walk of the public folder, yielding folders and files.

        Relative paths are relative to the *supplied* root and never include
        the root folder's own name — that name is applied only when planning
        the Google Drive destination. Children are visited in a deterministic
        order (folders first, then files, each by name), so repeated runs of an
        unchanged folder produce identical output.

        Traversal stays inside the supplied public root.
        """
        yield from self._walk(relative_path, depth=0)

    def _walk(self, relative_path: str, depth: int) -> Iterator[RemoteSourceItem]:
        if depth >= MAX_TRAVERSAL_DEPTH:
            raise StorageError(
                f"Maximum traversal depth {MAX_TRAVERSAL_DEPTH} exceeded at {relative_path!r}"
            )

        children = sorted(
            self.list_folder(relative_path),
            key=lambda item: (not item.is_directory, item.name),
        )
        for child in children:
            yield child
            if child.is_directory:
                yield from self._walk(child.relative_path, depth + 1)

    def exists(self, relative_path: str) -> bool:
        """Report whether an item exists at ``relative_path``.

        A 404 means "absent"; any other API failure is raised as a
        :class:`StorageError` rather than being silently reported as absent.
        """
        payload = self._get_json(
            PUBLIC_RESOURCES_ENDPOINT,
            {"public_key": self.public_key, "path": _api_path(relative_path), "limit": 0},
            allow_404=True,
        )
        return payload is not None

    def download(self, relative_path: str, destination: Path) -> Path:
        """Stream one public source file to ``destination``.

        The bytes are streamed to a sibling ``.part`` file and moved into place
        only once the whole stream has been consumed, so an interrupted
        download can never be mistaken for a complete file. The partial file is
        removed if anything fails. The source is never modified.
        """
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        partial = destination.with_name(f"{destination.name}.part")

        href = self._download_href(relative_path)

        try:
            with self.client.stream("GET", href) as response:
                if response.status_code >= 400:
                    raise StorageError(
                        f"Yandex Disk download failed with HTTP {response.status_code}"
                    )
                with partial.open("wb") as handle:
                    for chunk in response.iter_bytes():
                        handle.write(chunk)
        except httpx.TimeoutException as error:
            _remove_quietly(partial)
            raise StorageError(f"Yandex Disk download timed out: {relative_path}") from error
        except httpx.HTTPError as error:
            _remove_quietly(partial)
            raise StorageError(
                f"Yandex Disk download failed: {type(error).__name__}"
            ) from error
        except BaseException:
            _remove_quietly(partial)
            raise

        os.replace(partial, destination)
        return destination

    def _download_href(self, relative_path: str) -> str:
        """Ask the API for the temporary download URL of one public file."""
        payload = self._get_json(
            PUBLIC_DOWNLOAD_ENDPOINT,
            {"public_key": self.public_key, "path": _api_path(relative_path)},
        )
        assert payload is not None

        href = payload.get("href")
        if not isinstance(href, str) or not href:
            raise StorageError(f"Yandex Disk returned no download link for {relative_path}")
        return href

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


def _api_path(relative_path: str) -> str:
    """Map a root-relative path to the ``path`` parameter the API expects."""
    segments = [segment for segment in relative_path.replace("\\", "/").split("/") if segment]
    return "/" + "/".join(segments)


def _to_source_item(raw: dict[str, Any], parent_relative_path: str) -> RemoteSourceItem:
    """Map one API resource object onto the provider-neutral model.

    Unicode names are preserved verbatim. The relative path is built from the
    parent path plus the name rather than from the provider's own ``path``, so
    it stays relative to the supplied root.
    """
    name = raw.get("name")
    if not isinstance(name, str) or not name:
        raise StorageError("Yandex Disk returned an item without a name")

    parent = "/".join(
        segment for segment in parent_relative_path.replace("\\", "/").split("/") if segment
    )
    relative_path = f"{parent}/{name}" if parent else name

    is_directory = raw.get("type") == "dir"
    size = raw.get("size")
    resource_id = raw.get("resource_id")

    return RemoteSourceItem(
        name=name,
        relative_path=relative_path,
        is_directory=is_directory,
        remote_id=str(resource_id) if resource_id else None,
        size=int(size) if isinstance(size, int) and not is_directory else None,
        modified_at=_parse_timestamp(raw.get("modified")),
        # Provider-supplied hash; never computed by downloading the file.
        content_hash=_first_string(raw.get("md5"), raw.get("sha256")),
    )


def _first_string(*candidates: Any) -> str | None:
    for candidate in candidates:
        if isinstance(candidate, str) and candidate:
            return candidate
    return None


def _parse_timestamp(value: Any) -> datetime | None:
    """Parse an API timestamp, tolerating anything unexpected."""
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        LOG.debug("Ignoring unparsable timestamp from Yandex Disk")
        return None


def _remove_quietly(path: Path) -> None:
    """Delete a partial download, ignoring the case where it never existed."""
    try:
        path.unlink(missing_ok=True)
    except OSError:
        LOG.debug("Could not remove partial download %s", path.name)
