"""Local SQLite state schema tests. No cloud access."""

from __future__ import annotations

from pathlib import Path

from photoarchive.state import SCHEMA_VERSION, StateRepository


def _columns(repository: StateRepository, table: str) -> set[str]:
    with repository.connect() as connection:
        rows = connection.execute(f"PRAGMA table_info({table})").fetchall()
    return {row["name"] for row in rows}


def test_initialize_creates_the_schema(tmp_path: Path) -> None:
    repository = StateRepository(tmp_path / "archive.sqlite")

    repository.initialize()

    assert (tmp_path / "archive.sqlite").exists()
    with repository.connect() as connection:
        version = connection.execute("SELECT version FROM schema_info").fetchone()
    assert version["version"] == SCHEMA_VERSION


def test_initialize_is_idempotent(tmp_path: Path) -> None:
    repository = StateRepository(tmp_path / "archive.sqlite")

    repository.initialize()
    repository.initialize()

    with repository.connect() as connection:
        rows = connection.execute("SELECT version FROM schema_info").fetchall()
    assert len(rows) == 1


def test_source_roots_track_identity_and_name(tmp_path: Path) -> None:
    repository = StateRepository(tmp_path / "archive.sqlite")
    repository.initialize()

    columns = _columns(repository, "source_roots")

    assert {"identity", "name", "destination_name", "destination_id"} <= columns


def test_source_root_identity_is_unique(tmp_path: Path) -> None:
    repository = StateRepository(tmp_path / "archive.sqlite")
    repository.initialize()

    with repository.connect() as connection:
        indexes = connection.execute("PRAGMA index_list(source_roots)").fetchall()
        unique_columns = set()
        for index in indexes:
            if not index["unique"]:
                continue
            info = connection.execute(f"PRAGMA index_info({index['name']})").fetchall()
            unique_columns.update(row["name"] for row in info)

    assert "identity" in unique_columns


def test_source_items_are_scoped_to_a_root(tmp_path: Path) -> None:
    repository = StateRepository(tmp_path / "archive.sqlite")
    repository.initialize()

    columns = _columns(repository, "source_items")

    # Identity is (root, relative path) + hash, never the bare filename.
    assert {"root_id", "relative_path", "content_hash"} <= columns
    assert "filename" not in columns
