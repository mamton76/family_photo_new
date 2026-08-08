"""Google Drive destination adapter (skeleton — no network calls yet).

All content this pipeline generates lives **below one configured Google Drive
root folder** (``google_drive.root_folder_id``). Nothing is ever created,
searched for or modified outside that subtree, and the root id comes from
configuration rather than from business logic.

The relative hierarchy of the Yandex source is mirrored under that root, so
``1988/Dacha/001.jpg`` in the source becomes ``<root>/1988/Dacha/001.jpg`` in
the destination.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

from photoarchive.models import RemoteSourceItem

FOLDER_MIME_TYPE = "application/vnd.google-apps.folder"

DEFAULT_SCOPES: tuple[str, ...] = ("https://www.googleapis.com/auth/drive",)


@dataclass(frozen=True, slots=True)
class GoogleDriveConfig:
    """Connection parameters for the destination archive.

    Credential paths point at local files that are git-ignored; no secret
    values are stored in configuration.
    """

    root_folder_id: str
    credentials_path: Path = Path("credentials.json")
    token_path: Path = Path("token.json")
    scopes: tuple[str, ...] = field(default=DEFAULT_SCOPES)


class GoogleDriveStorage:
    """Writable archive destination rooted at one Drive folder.

    Implements :class:`~photoarchive.storage.base.WritableStorage`.
    """

    def __init__(self, config: GoogleDriveConfig, cache_dir: Path) -> None:
        self.config = config
        self.cache_dir = Path(cache_dir)
        self._folder_ids: dict[str, str] = {"": config.root_folder_id}

    # -- Authentication ----------------------------------------------------

    def authenticate(self) -> None:
        """Establish an API client.

        TODO: use google-auth-oauthlib's installed-app flow, cache the token in
        ``config.token_path``, refresh it when expired, and build the Drive v3
        service with google-api-python-client.
        """
        raise NotImplementedError("GoogleDriveStorage.authenticate is not implemented yet")

    # -- ReadableStorage ---------------------------------------------------

    def list_recursive(self, relative_path: str = "") -> Iterable[RemoteSourceItem]:
        """TODO: walk the subtree with paginated ``files.list`` queries."""
        raise NotImplementedError("GoogleDriveStorage.list_recursive is not implemented yet")

    def list_folder(self, relative_path: str = "") -> Iterator[RemoteSourceItem]:
        """TODO: ``files.list`` with ``'<parent_id>' in parents and trashed = false``."""
        raise NotImplementedError("GoogleDriveStorage.list_folder is not implemented yet")

    def exists(self, relative_path: str) -> bool:
        """TODO: resolve the path segment by segment below the root folder."""
        raise NotImplementedError("GoogleDriveStorage.exists is not implemented yet")

    def download(self, relative_path: str, destination: Path) -> Path:
        """TODO: ``files.get(alt='media')`` streamed into the local cache."""
        raise NotImplementedError("GoogleDriveStorage.download is not implemented yet")

    # -- WritableStorage ---------------------------------------------------

    def ensure_folder(self, relative_path: str) -> str:
        """Create missing folders along ``relative_path`` and return the leaf id.

        TODO: resolve each segment with a name+parent query and create it only
        when absent. Drive allows duplicate names in one parent, so this lookup
        must run per segment to keep repeated runs idempotent.
        """
        raise NotImplementedError("GoogleDriveStorage.ensure_folder is not implemented yet")

    def upload(self, local_path: Path, relative_path: str, *, overwrite: bool = True) -> str:
        """Upload one local file into the mirrored destination folder.

        TODO: resumable ``MediaFileUpload``; when ``overwrite`` is set and a
        file with the same name exists in the parent, update that file instead
        of creating a second copy.
        """
        raise NotImplementedError("GoogleDriveStorage.upload is not implemented yet")
