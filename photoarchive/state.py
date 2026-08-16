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
from collections.abc import Iterable, Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from photoarchive.models import SourceRoot
from photoarchive.naming import sanitize_folder_name as sanitize_destination_name

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

-- Per-row bookkeeping for review workbooks. Deliberately not workbook
-- columns: this is the pipeline's memory, not something a reviewer edits.
CREATE TABLE IF NOT EXISTS review_rows (
    id               INTEGER PRIMARY KEY,
    root_identity    TEXT NOT NULL,
    folder_path      TEXT NOT NULL,
    identity         TEXT NOT NULL,
    photo_hash        TEXT,
    description_hash  TEXT,
    suggestion_hash   TEXT,
    status            TEXT,
    was_absent        INTEGER NOT NULL DEFAULT 0,
    photo_id          TEXT,
    image_fingerprint TEXT,
    last_scan        TEXT,
    UNIQUE (root_identity, folder_path, identity)
);

CREATE INDEX IF NOT EXISTS idx_source_items_root ON source_items(root_id);
CREATE INDEX IF NOT EXISTS idx_source_items_hash ON source_items(content_hash);
CREATE INDEX IF NOT EXISTS idx_review_rows_folder
    ON review_rows(root_identity, folder_path);
"""


def utc_now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _item_moved(
    previous: sqlite3.Row,
    content_hash: str | None,
    size: int | None,
    modified_at: str | None,
) -> bool:
    """Whether a re-listed item differs from what was recorded.

    A provider hash is authoritative when both sides have one. Without it,
    size and modified time are the only signals available.
    """
    if content_hash and previous["content_hash"]:
        return content_hash != previous["content_hash"]
    if content_hash != previous["content_hash"]:
        return True
    return size != previous["size"] or modified_at != previous["modified_at"]


@dataclass(frozen=True, slots=True)
class ChangeSet:
    """Result of comparing a fresh listing against recorded state."""

    new: tuple[str, ...] = ()
    changed: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()
    unchanged: tuple[str, ...] = ()


#: Columns added to existing databases. ``CREATE TABLE IF NOT EXISTS`` leaves a
#: table that already exists untouched, so a new column reaches an established
#: archive only by being added explicitly.
_ADDED_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("review_rows", "photo_id", "TEXT"),
    ("review_rows", "image_fingerprint", "TEXT"),
)


def _add_missing_columns(cursor) -> None:
    """Bring an older database up to the current schema, in place."""
    for table, column, kind in _ADDED_COLUMNS:
        existing = {
            row["name"] for row in cursor.execute(f"PRAGMA table_info({table})")
        }
        if column not in existing:
            cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")


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
            _add_missing_columns(cursor)
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

        Recording the URL is what later lets the dashboard link a generated
        folder back to the Yandex share it came from, and lets local caches be
        found by identity rather than by guessing from filenames.
        """
        timestamp = utc_now().isoformat()
        destination = sanitize_destination_name(source_root.name)

        with self.connect() as connection:
            connection.execute(
                "INSERT INTO source_roots (identity, source_url, name,"
                " destination_name, first_seen, last_scan)"
                " VALUES (?, ?, ?, ?, ?, ?)"
                " ON CONFLICT (identity) DO UPDATE SET"
                " source_url = excluded.source_url,"
                " name = excluded.name,"
                " destination_name = excluded.destination_name,"
                " last_scan = excluded.last_scan",
                (
                    source_root.identity,
                    source_root.url,
                    source_root.name,
                    destination,
                    timestamp,
                    timestamp,
                ),
            )
            row = connection.execute(
                "SELECT id FROM source_roots WHERE identity = ?",
                (source_root.identity,),
            ).fetchone()
        return int(row["id"])

    def list_source_roots(self) -> list[SourceRoot]:
        """Every source root the pipeline has scanned."""
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT identity, source_url, name FROM source_roots ORDER BY name"
            ).fetchall()
        return [
            SourceRoot(url=row["source_url"], name=row["name"]) for row in rows
        ]

    def record_listing(self, root_id: int, items: Iterable[object]) -> ChangeSet:
        """Persist a fresh listing and report what moved since the last one.

        Identity is ``(root, relative_path)`` — never a bare filename, because
        the same name appears in many folders and under many roots.

        An item counts as *changed* when the provider's content hash moved, or
        when it has no hash and its size or modified time moved. Files that
        vanished are marked missing rather than deleted, so their history and
        any downstream ids survive; a file that comes back clears that mark.
        """
        timestamp = utc_now().isoformat()
        new: list[str] = []
        changed: list[str] = []
        unchanged: list[str] = []
        seen: set[str] = set()

        with self.connect() as connection:
            existing = {
                row["relative_path"]: row
                for row in connection.execute(
                    "SELECT * FROM source_items WHERE root_id = ?", (root_id,)
                )
            }

            for item in items:
                relative_path = getattr(item, "relative_path", "")
                if not relative_path:
                    continue
                seen.add(relative_path)

                modified = getattr(item, "modified_at", None)
                modified_text = modified.isoformat() if modified else None
                size = getattr(item, "size", None)
                content_hash = getattr(item, "content_hash", None)
                is_directory = bool(getattr(item, "is_directory", False))
                previous = existing.get(relative_path)

                if previous is None:
                    connection.execute(
                        "INSERT INTO source_items (root_id, relative_path, remote_id,"
                        " is_directory, size, modified_at, content_hash, first_seen,"
                        " last_seen) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            root_id, relative_path, getattr(item, "remote_id", None),
                            int(is_directory), size, modified_text, content_hash,
                            timestamp, timestamp,
                        ),
                    )
                    new.append(relative_path)
                    continue

                if _item_moved(previous, content_hash, size, modified_text):
                    changed.append(relative_path)
                else:
                    unchanged.append(relative_path)

                connection.execute(
                    "UPDATE source_items SET remote_id = ?, is_directory = ?, size = ?,"
                    " modified_at = ?, content_hash = ?, last_seen = ?, missing_since = NULL"
                    " WHERE root_id = ? AND relative_path = ?",
                    (
                        getattr(item, "remote_id", None), int(is_directory), size,
                        modified_text, content_hash, timestamp, root_id, relative_path,
                    ),
                )

            missing = sorted(set(existing) - seen)
            for relative_path in missing:
                if existing[relative_path]["missing_since"] is None:
                    connection.execute(
                        "UPDATE source_items SET missing_since = ?"
                        " WHERE root_id = ? AND relative_path = ?",
                        (timestamp, root_id, relative_path),
                    )

        return ChangeSet(
            new=tuple(sorted(new)),
            changed=tuple(sorted(changed)),
            missing=tuple(missing),
            unchanged=tuple(sorted(unchanged)),
        )

    def load_source_items(self, root_id: int) -> list[dict[str, object]]:
        """Every item recorded under one root, including missing ones."""
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM source_items WHERE root_id = ? ORDER BY relative_path",
                (root_id,),
            ).fetchall()
        return [dict(row) for row in rows]

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

    # -- Review row bookkeeping -------------------------------------------

    def load_row_states(self, root_identity: str, folder_path: str) -> dict[str, object]:
        """Return the stored state of every row in one folder, by identity."""
        from photoarchive.review.builder import RowState

        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM review_rows WHERE root_identity = ? AND folder_path = ?",
                (root_identity, folder_path),
            ).fetchall()

        return {
            row["identity"]: RowState(
                identity=row["identity"],
                photo_hash=row["photo_hash"] or "",
                description_hash=row["description_hash"] or "",
                suggestion_hash=row["suggestion_hash"] or "",
                status=row["status"] or "",
                was_absent=bool(row["was_absent"]),
                photo_id=row["photo_id"] or "",
                image_fingerprint=row["image_fingerprint"] or "",
            )
            for row in rows
        }

    def save_row_states(
        self, root_identity: str, folder_path: str, states: dict[str, object]
    ) -> None:
        """Persist row bookkeeping after a scan."""
        timestamp = utc_now().isoformat()
        with self.connect() as connection:
            for identity, state in states.items():
                connection.execute(
                    "INSERT INTO review_rows (root_identity, folder_path, identity,"
                    " photo_hash, description_hash, suggestion_hash, status, was_absent,"
                    " photo_id, image_fingerprint, last_scan)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
                    " ON CONFLICT (root_identity, folder_path, identity) DO UPDATE SET"
                    " photo_hash = excluded.photo_hash,"
                    " description_hash = excluded.description_hash,"
                    " suggestion_hash = excluded.suggestion_hash,"
                    " status = excluded.status,"
                    " was_absent = excluded.was_absent,"
                    " photo_id = excluded.photo_id,"
                    " image_fingerprint = excluded.image_fingerprint,"
                    " last_scan = excluded.last_scan",
                    (
                        root_identity,
                        folder_path,
                        identity,
                        state.photo_hash,
                        state.description_hash,
                        state.suggestion_hash,
                        state.status,
                        int(state.was_absent),
                        state.photo_id or None,
                        state.image_fingerprint or None,
                        timestamp,
                    ),
                )
