"""Durable local processing state, backed by stdlib :mod:`sqlite3`.

The state database is a *cache of observations*, not the source of truth: the
cloud archive and the review workbooks are authoritative. It exists so that
repeated runs can answer, cheaply and offline:

* which source items are new;
* which source items changed (content hash / size / mtime moved);
* which source items disappeared;
* which folder descriptions changed;
* which processed copies were already built;
* which processed copies were already published.

Identity rule: a file is identified by ``(source_root, relative_path)`` plus
its content hash where the provider offers one. A bare filename is never an
identity, because names repeat across folders and get renamed.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from photoarchive.models import SourceRoot

DEFAULT_STATE_PATH = Path("archive.sqlite")

SCHEMA_VERSION = 2

_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_info (
    version INTEGER NOT NULL
);

-- One row per scanned source root (a Yandex Disk folder URL).
--
-- `identity` is derived from the URL and is the real key: `name` is the
-- source folder's display name, which doubles as this root's dedicated
-- destination folder and may be renamed by a person without that meaning
-- "a different archive".
CREATE TABLE IF NOT EXISTS source_roots (
    id                INTEGER PRIMARY KEY,
    identity          TEXT NOT NULL UNIQUE,
    source_url        TEXT NOT NULL UNIQUE,
    name              TEXT NOT NULL,
    destination_name  TEXT NOT NULL,
    destination_id    TEXT,
    first_seen        TEXT NOT NULL,
    last_scan         TEXT
);

-- One row per source file observed under a root.
CREATE TABLE IF NOT EXISTS source_items (
    id               INTEGER PRIMARY KEY,
    root_id          INTEGER NOT NULL REFERENCES source_roots(id) ON DELETE CASCADE,
    relative_path    TEXT NOT NULL,
    remote_id        TEXT,
    is_directory     INTEGER NOT NULL DEFAULT 0,
    size             INTEGER,
    modified_at      TEXT,
    content_hash     TEXT,
    first_seen       TEXT NOT NULL,
    last_seen        TEXT NOT NULL,
    missing_since    TEXT,
    UNIQUE (root_id, relative_path)
);

-- One row per folder description file, so description changes are detectable
-- independently of the photos they describe.
CREATE TABLE IF NOT EXISTS folder_descriptions (
    id               INTEGER PRIMARY KEY,
    root_id          INTEGER NOT NULL REFERENCES source_roots(id) ON DELETE CASCADE,
    folder_path      TEXT NOT NULL,
    description_hash TEXT,
    last_seen        TEXT NOT NULL,
    UNIQUE (root_id, folder_path)
);

-- Build/publish bookkeeping for processed copies.
CREATE TABLE IF NOT EXISTS processed_items (
    id                     INTEGER PRIMARY KEY,
    source_item_id         INTEGER NOT NULL REFERENCES source_items(id) ON DELETE CASCADE,
    metadata_hash          TEXT,
    processed_hash         TEXT,
    drive_file_id          TEXT,
    built_at               TEXT,
    google_photos_media_id TEXT,
    published_at           TEXT,
    UNIQUE (source_item_id)
);

CREATE INDEX IF NOT EXISTS idx_source_items_root ON source_items(root_id);
CREATE INDEX IF NOT EXISTS idx_source_items_hash ON source_items(content_hash);
"""


def utc_now() -> datetime:
    return datetime.now(tz=timezone.utc)


@dataclass(frozen=True, slots=True)
class ChangeSet:
    """Result of comparing a fresh listing against recorded state."""

    new: tuple[str, ...] = ()
    changed: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()
    unchanged: tuple[str, ...] = ()


class StateRepository:
    """Thin SQLite repository for scan/build/publish bookkeeping.

    Only connection handling and schema creation are implemented; the query
    methods are deliberate TODOs until the scan flow is wired up.
    """

    def __init__(self, path: Path | str = DEFAULT_STATE_PATH) -> None:
        self.path = Path(path)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        """Open a connection with foreign keys on and rows as mappings."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        """Create the schema if it does not exist. Safe to call repeatedly."""
        with self.connect() as connection, closing(connection.cursor()) as cursor:
            cursor.executescript(_SCHEMA)
            row = cursor.execute("SELECT version FROM schema_info").fetchone()
            if row is None:
                cursor.execute(
                    "INSERT INTO schema_info (version) VALUES (?)", (SCHEMA_VERSION,)
                )

    # -- Scan bookkeeping -------------------------------------------------
    # TODO: implement together with Scanner.

    def register_source_root(self, source_root: SourceRoot) -> int:
        """Return the id of the root, inserting it on first sight.

        Looked up by :attr:`SourceRoot.identity`, so a renamed source folder
        updates the existing row instead of creating a second archive.

        TODO: implement the upsert and report renames to the caller, since a
        rename means the dedicated destination folder needs renaming too.
        """
        raise NotImplementedError("register_source_root is not implemented yet")

    def record_listing(self, root_id: int, items: object) -> ChangeSet:
        """Persist a fresh listing and report new/changed/missing items."""
        raise NotImplementedError("record_listing is not implemented yet")

    def record_description(
        self, root_id: int, folder_path: str, description_hash: str
    ) -> bool:
        """Store a folder description hash; return True when it changed."""
        raise NotImplementedError("record_description is not implemented yet")

    # -- Build / publish bookkeeping --------------------------------------
    # TODO: implement together with the build and publish commands.

    def mark_built(
        self, source_item_id: int, metadata_hash: str, processed_hash: str
    ) -> None:
        raise NotImplementedError("mark_built is not implemented yet")

    def mark_published(self, source_item_id: int, media_id: str) -> None:
        raise NotImplementedError("mark_published is not implemented yet")
