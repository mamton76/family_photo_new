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


# -- Source listing bookkeeping -------------------------------------------


def _item(relative_path: str, content_hash: str | None = None, size: int = 100,
          modified: str = "2020-01-01T00:00:00+00:00"):
    from datetime import datetime

    from photoarchive.models import RemoteSourceItem

    return RemoteSourceItem(
        name=relative_path.rsplit("/", 1)[-1],
        relative_path=relative_path,
        is_directory=False,
        size=size,
        modified_at=datetime.fromisoformat(modified),
        content_hash=content_hash,
    )


def _repo(tmp_path: Path, name: str = "archive.sqlite"):
    from photoarchive.models import SourceRoot

    state = StateRepository(tmp_path / name)
    state.initialize()
    root_id = state.register_source_root(
        SourceRoot(url="https://disk.yandex.ru/d/one", name="Root A")
    )
    return state, root_id


def test_first_listing_is_all_new(tmp_path: Path) -> None:
    state, root_id = _repo(tmp_path)

    changes = state.record_listing(root_id, [_item("a.jpg", "h1"), _item("b.jpg", "h2")])

    assert changes.new == ("a.jpg", "b.jpg")
    assert changes.changed == () and changes.missing == () and changes.unchanged == ()


def test_identical_listing_is_all_unchanged(tmp_path: Path) -> None:
    state, root_id = _repo(tmp_path)
    items = [_item("a.jpg", "h1"), _item("b.jpg", "h2")]
    state.record_listing(root_id, items)

    changes = state.record_listing(root_id, items)

    assert changes.unchanged == ("a.jpg", "b.jpg")
    assert changes.new == () and changes.changed == () and changes.missing == ()


def test_changed_content_hash_is_detected(tmp_path: Path) -> None:
    state, root_id = _repo(tmp_path)
    state.record_listing(root_id, [_item("a.jpg", "h1")])

    changes = state.record_listing(root_id, [_item("a.jpg", "h2")])

    assert changes.changed == ("a.jpg",)


def test_size_change_is_detected_without_a_hash(tmp_path: Path) -> None:
    state, root_id = _repo(tmp_path)
    state.record_listing(root_id, [_item("a.jpg", None, size=10)])

    changes = state.record_listing(root_id, [_item("a.jpg", None, size=20)])

    assert changes.changed == ("a.jpg",)


def test_modified_time_change_is_detected_without_a_hash(tmp_path: Path) -> None:
    state, root_id = _repo(tmp_path)
    state.record_listing(root_id, [_item("a.jpg", None)])

    changes = state.record_listing(
        root_id, [_item("a.jpg", None, modified="2021-06-06T00:00:00+00:00")]
    )

    assert changes.changed == ("a.jpg",)


def test_added_item_is_new_and_others_unchanged(tmp_path: Path) -> None:
    state, root_id = _repo(tmp_path)
    state.record_listing(root_id, [_item("a.jpg", "h1")])

    changes = state.record_listing(root_id, [_item("a.jpg", "h1"), _item("b.jpg", "h2")])

    assert changes.new == ("b.jpg",)
    assert changes.unchanged == ("a.jpg",)


def test_disappeared_item_is_missing_but_kept(tmp_path: Path) -> None:
    state, root_id = _repo(tmp_path)
    state.record_listing(root_id, [_item("a.jpg", "h1"), _item("b.jpg", "h2")])

    changes = state.record_listing(root_id, [_item("a.jpg", "h1")])

    assert changes.missing == ("b.jpg",)
    stored = {row["relative_path"]: row for row in state.load_source_items(root_id)}
    assert stored["b.jpg"]["missing_since"] is not None


def test_a_returning_item_clears_the_missing_mark(tmp_path: Path) -> None:
    state, root_id = _repo(tmp_path)
    both = [_item("a.jpg", "h1"), _item("b.jpg", "h2")]
    state.record_listing(root_id, both)
    state.record_listing(root_id, [_item("a.jpg", "h1")])

    changes = state.record_listing(root_id, both)

    assert changes.missing == ()
    stored = {row["relative_path"]: row for row in state.load_source_items(root_id)}
    assert stored["b.jpg"]["missing_since"] is None


def test_same_filename_in_different_folders_does_not_collide(tmp_path: Path) -> None:
    state, root_id = _repo(tmp_path)

    changes = state.record_listing(
        root_id, [_item("A/001.jpg", "h1"), _item("B/001.jpg", "h2")]
    )

    assert changes.new == ("A/001.jpg", "B/001.jpg")
    assert len(state.load_source_items(root_id)) == 2


def test_same_path_under_different_roots_does_not_collide(tmp_path: Path) -> None:
    from photoarchive.models import SourceRoot

    state, first_root = _repo(tmp_path)
    second_root = state.register_source_root(
        SourceRoot(url="https://disk.yandex.ru/d/two", name="Root B")
    )

    state.record_listing(first_root, [_item("001.jpg", "h1")])
    changes = state.record_listing(second_root, [_item("001.jpg", "h2")])

    assert changes.new == ("001.jpg",)
    assert len(state.load_source_items(first_root)) == 1
    assert len(state.load_source_items(second_root)) == 1
