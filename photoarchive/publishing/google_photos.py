"""Google Photos publishing (skeleton — no network calls yet).

Publishing is the last stage: only files that were already *built* (processed
copy with reviewed metadata written) are uploaded, and only when new or
changed. Albums are derived from the reviewed ``Albums`` column, and the
returned media ids are persisted so repeated runs do not create duplicates.

The Google Photos API only lets an application manage the albums it created
itself, so album creation and lookup are limited to app-created albums.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_SCOPES: tuple[str, ...] = (
    "https://www.googleapis.com/auth/photoslibrary.appendonly",
)


@dataclass(frozen=True, slots=True)
class GooglePhotosConfig:
    """Credential locations for the publisher; no secrets stored inline."""

    credentials_path: Path = Path("credentials.json")
    token_path: Path = Path("token.json")
    scopes: tuple[str, ...] = field(default=DEFAULT_SCOPES)


class GooglePhotosPublisher:
    """Uploads built photos and files them into application-managed albums."""

    def __init__(self, config: GooglePhotosConfig) -> None:
        self.config = config

    def authenticate(self) -> None:
        """TODO: OAuth installed-app flow with a cached, refreshable token."""
        raise NotImplementedError("GooglePhotosPublisher.authenticate is not implemented yet")

    def ensure_album(self, title: str) -> str:
        """Return the id of an app-created album, creating it if absent.

        TODO: list app-created albums, match by title, create on miss. Must be
        idempotent so repeated runs never create a duplicate album.
        """
        raise NotImplementedError("GooglePhotosPublisher.ensure_album is not implemented yet")

    def upload(self, local_path: Path) -> str:
        """Upload one built file and return its upload token.

        TODO: raw upload to ``/v1/uploads``, then ``mediaItems:batchCreate``.
        """
        raise NotImplementedError("GooglePhotosPublisher.upload is not implemented yet")

    def add_to_album(self, media_id: str, album_id: str) -> None:
        """TODO: ``albums.batchAddMediaItems`` for an app-created album."""
        raise NotImplementedError("GooglePhotosPublisher.add_to_album is not implemented yet")
