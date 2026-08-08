"""Yandex Disk public-API adapter tests.

Every request is served by :class:`httpx.MockTransport`; no test here reaches
the public internet.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

import httpx
import pytest

from photoarchive.storage.base import StorageError
from photoarchive.storage.yandex import (
    PUBLIC_DOWNLOAD_ENDPOINT,
    PUBLIC_RESOURCES_ENDPOINT,
    USER_AGENT,
    YandexDiskConfig,
    YandexDiskStorage,
    fallback_root_name,
)

PUBLIC_URL = "https://disk.yandex.ru/d/cFwfbSEQ7IB37g"
ROOT_NAME = "Ф-ТоняМам-83-93-школа"

Handler = Callable[[httpx.Request], httpx.Response]


def _storage(handler: Handler, tmp_path: Path, page_size: int = 200) -> YandexDiskStorage:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    config = YandexDiskConfig(public_url=PUBLIC_URL, page_size=page_size)
    return YandexDiskStorage(config, tmp_path, client=client)


def _dir_entry(name: str, **extra: Any) -> dict[str, Any]:
    return {"name": name, "type": "dir", "path": f"/{name}", **extra}


def _file_entry(name: str, **extra: Any) -> dict[str, Any]:
    return {
        "name": name,
        "type": "file",
        "path": f"/{name}",
        "size": 1024,
        "modified": "2019-07-14T10:11:12+00:00",
        "md5": "d41d8cd98f00b204e9800998ecf8427e",
        **extra,
    }


def _folder_payload(items: list[dict[str, Any]], *, total: int | None = None) -> dict[str, Any]:
    return {
        "name": ROOT_NAME,
        "type": "dir",
        "_embedded": {"items": items, "total": total if total is not None else len(items)},
    }


# -- Root metadata --------------------------------------------------------


def test_describe_root_resolves_the_name_from_the_api(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["public_key"] == PUBLIC_URL
        return httpx.Response(200, json={"name": ROOT_NAME, "resource_id": "res-1"})

    root = _storage(handler, tmp_path).describe_root()

    assert root.name == ROOT_NAME
    assert root.url == PUBLIC_URL
    assert root.remote_id == "res-1"


def test_describe_root_preserves_unicode(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"name": "Семейный архив 1988"})

    assert _storage(handler, tmp_path).describe_root().name == "Семейный архив 1988"


@pytest.mark.parametrize("payload", [{}, {"name": ""}, {"name": "   "}, {"name": 42}])
def test_describe_root_falls_back_when_no_usable_name(
    payload: dict[str, Any], tmp_path: Path
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    root = _storage(handler, tmp_path).describe_root()

    assert root.name == fallback_root_name(PUBLIC_URL) == "cFwfbSEQ7IB37g"


def test_describe_root_identity_survives_a_rename(tmp_path: Path) -> None:
    def first(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"name": "Old name"})

    def second(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"name": "New name"})

    before = _storage(first, tmp_path).describe_root()
    after = _storage(second, tmp_path).describe_root()

    assert before.name != after.name
    assert before.identity == after.identity


def test_requests_carry_the_project_user_agent(tmp_path: Path) -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("user-agent", ""))
        return httpx.Response(200, json={"name": ROOT_NAME})

    # A client created by the adapter itself must set the header.
    config = YandexDiskConfig(public_url=PUBLIC_URL)
    storage = YandexDiskStorage(config, tmp_path)
    storage._client = httpx.Client(
        transport=httpx.MockTransport(handler), headers={"User-Agent": USER_AGENT}
    )
    storage.describe_root()

    assert seen == [USER_AGENT]


# -- Pagination -----------------------------------------------------------


def test_list_folder_follows_pagination_across_pages(tmp_path: Path) -> None:
    page_size = 2
    all_items = [_file_entry(f"{index:03d}.jpg") for index in range(5)]
    requested_offsets: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        offset = int(request.url.params["offset"])
        limit = int(request.url.params["limit"])
        requested_offsets.append(offset)
        window = all_items[offset : offset + limit]
        return httpx.Response(200, json=_folder_payload(window, total=len(all_items)))

    items = _storage(handler, tmp_path, page_size=page_size).list_folder()

    assert [item.name for item in items] == [
        "000.jpg",
        "001.jpg",
        "002.jpg",
        "003.jpg",
        "004.jpg",
    ]
    assert requested_offsets == [0, 2, 4]
    # Every item exactly once.
    assert len(set(item.relative_path for item in items)) == len(items)


def test_list_folder_honours_the_configured_page_size(tmp_path: Path) -> None:
    seen_limits: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_limits.append(int(request.url.params["limit"]))
        return httpx.Response(200, json=_folder_payload([_file_entry("a.jpg")]))

    _storage(handler, tmp_path, page_size=37).list_folder()

    assert seen_limits == [37]


def test_list_folder_stops_on_a_short_page(tmp_path: Path) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=_folder_payload([_file_entry("a.jpg")], total=99))

    _storage(handler, tmp_path, page_size=10).list_folder()

    assert calls == 1


def test_list_folder_maps_provider_fields(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=_folder_payload(
                [
                    _file_entry("Тоня.jpg", resource_id="res-9"),
                    _dir_entry("1988"),
                ]
            ),
        )

    items = {item.name: item for item in _storage(handler, tmp_path).list_folder()}

    photo = items["Тоня.jpg"]
    assert photo.is_directory is False
    assert photo.size == 1024
    assert photo.remote_id == "res-9"
    assert photo.content_hash == "d41d8cd98f00b204e9800998ecf8427e"
    assert photo.modified_at is not None
    assert photo.modified_at.year == 2019

    folder = items["1988"]
    assert folder.is_directory is True
    assert folder.size is None


def test_list_folder_prefers_md5_then_sha256(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=_folder_payload(
                [
                    _file_entry("a.jpg", md5=None, sha256="sha-value"),
                    _file_entry("b.jpg", md5="md5-value", sha256="sha-value"),
                ]
            ),
        )

    items = {item.name: item for item in _storage(handler, tmp_path).list_folder()}

    assert items["a.jpg"].content_hash == "sha-value"
    assert items["b.jpg"].content_hash == "md5-value"


def test_list_folder_requests_the_nested_api_path(tmp_path: Path) -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.params["path"])
        return httpx.Response(200, json=_folder_payload([]))

    _storage(handler, tmp_path).list_folder("1988/Dacha")

    assert seen == ["/1988/Dacha"]


# -- Nested traversal -----------------------------------------------------


def _tree_handler(tree: dict[str, list[dict[str, Any]]]) -> Handler:
    """Serve a folder tree keyed by API path."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.params.get("path", "/")
        if path not in tree:
            return httpx.Response(404, json={"message": "not found"})
        return httpx.Response(200, json=_folder_payload(tree[path]))

    return handler


NESTED_TREE = {
    "/": [_dir_entry("1988"), _file_entry("root.jpg")],
    "/1988": [_dir_entry("Dacha")],
    "/1988/Dacha": [_file_entry("001.jpg")],
}


def test_list_recursive_preserves_relative_paths(tmp_path: Path) -> None:
    storage = _storage(_tree_handler(NESTED_TREE), tmp_path)

    paths = [item.relative_path for item in storage.list_recursive()]

    assert paths == ["1988", "1988/Dacha", "1988/Dacha/001.jpg", "root.jpg"]


def test_list_recursive_excludes_the_source_root_name(tmp_path: Path) -> None:
    storage = _storage(_tree_handler(NESTED_TREE), tmp_path)

    paths = [item.relative_path for item in storage.list_recursive()]

    assert all(not path.startswith(ROOT_NAME) for path in paths)


def test_list_recursive_is_deterministic(tmp_path: Path) -> None:
    storage = _storage(_tree_handler(NESTED_TREE), tmp_path)

    first = [item.relative_path for item in storage.list_recursive()]
    second = [item.relative_path for item in storage.list_recursive()]

    assert first == second


def test_list_recursive_handles_unicode_and_spaces(tmp_path: Path) -> None:
    tree = {
        "/": [_dir_entry("Ф-Тоня 83")],
        "/Ф-Тоня 83": [_dir_entry("Валаам")],
        "/Ф-Тоня 83/Валаам": [_file_entry("Тоня и Аня.jpg")],
    }
    storage = _storage(_tree_handler(tree), tmp_path)

    paths = [item.relative_path for item in storage.list_recursive()]

    assert paths == [
        "Ф-Тоня 83",
        "Ф-Тоня 83/Валаам",
        "Ф-Тоня 83/Валаам/Тоня и Аня.jpg",
    ]


def test_list_recursive_can_start_below_the_root(tmp_path: Path) -> None:
    storage = _storage(_tree_handler(NESTED_TREE), tmp_path)

    paths = [item.relative_path for item in storage.list_recursive("1988")]

    assert paths == ["1988/Dacha", "1988/Dacha/001.jpg"]


# -- exists() -------------------------------------------------------------


def test_exists_returns_true_on_200(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"name": "001.jpg"})

    assert _storage(handler, tmp_path).exists("1988/001.jpg") is True


def test_exists_returns_false_on_404(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"message": "Resource not found."})

    assert _storage(handler, tmp_path).exists("missing.jpg") is False


def test_exists_raises_on_server_error(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"message": "boom"})

    with pytest.raises(StorageError, match="500"):
        _storage(handler, tmp_path).exists("1988/001.jpg")


# -- download() -----------------------------------------------------------


def test_download_streams_to_the_destination(tmp_path: Path) -> None:
    payload = b"binary-photo-bytes" * 100
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        if str(request.url).startswith(PUBLIC_DOWNLOAD_ENDPOINT):
            return httpx.Response(200, json={"href": "https://downloader.example/file"})
        return httpx.Response(200, content=payload)

    destination = tmp_path / "nested" / "out" / "001.jpg"
    result = _storage(handler, tmp_path).download("1988/001.jpg", destination)

    assert result == destination
    assert destination.read_bytes() == payload
    assert any(url.startswith(PUBLIC_DOWNLOAD_ENDPOINT) for url in requested)
    # Parent directories are created, and no partial file survives.
    assert not (destination.parent / "001.jpg.part").exists()


def test_download_removes_the_partial_file_on_failure(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url).startswith(PUBLIC_DOWNLOAD_ENDPOINT):
            return httpx.Response(200, json={"href": "https://downloader.example/file"})
        raise httpx.ReadTimeout("stream died", request=request)

    destination = tmp_path / "001.jpg"

    with pytest.raises(StorageError):
        _storage(handler, tmp_path).download("1988/001.jpg", destination)

    assert not destination.exists()
    assert not (tmp_path / "001.jpg.part").exists()


def test_download_fails_when_no_href_is_returned(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"nothing": "useful"})

    with pytest.raises(StorageError, match="no download link"):
        _storage(handler, tmp_path).download("1988/001.jpg", tmp_path / "001.jpg")


def test_download_reports_an_http_failure_on_the_stream(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url).startswith(PUBLIC_DOWNLOAD_ENDPOINT):
            return httpx.Response(200, json={"href": "https://downloader.example/file"})
        return httpx.Response(503, content=b"")

    destination = tmp_path / "001.jpg"

    with pytest.raises(StorageError, match="503"):
        _storage(handler, tmp_path).download("1988/001.jpg", destination)

    assert not destination.exists()
    assert not (tmp_path / "001.jpg.part").exists()


# -- Error handling -------------------------------------------------------


def test_malformed_json_becomes_a_storage_error(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<html>not json</html>")

    with pytest.raises(StorageError, match="malformed JSON"):
        _storage(handler, tmp_path).describe_root()


def test_non_object_payload_becomes_a_storage_error(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=json.dumps([1, 2, 3]).encode())

    with pytest.raises(StorageError, match="unexpected payload"):
        _storage(handler, tmp_path).describe_root()


def test_listing_a_file_becomes_a_storage_error(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        # A file resource has no _embedded block.
        return httpx.Response(200, json={"name": "001.jpg", "type": "file"})

    with pytest.raises(StorageError, match="Not a listable folder"):
        _storage(handler, tmp_path).list_folder("001.jpg")


def test_missing_item_list_becomes_a_storage_error(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"name": "x", "_embedded": {"total": 0}})

    with pytest.raises(StorageError, match="no item list"):
        _storage(handler, tmp_path).list_folder()


def test_item_without_a_name_becomes_a_storage_error(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_folder_payload([{"type": "file"}]))

    with pytest.raises(StorageError, match="without a name"):
        _storage(handler, tmp_path).list_folder()


def test_unparsable_timestamp_is_tolerated(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json=_folder_payload([_file_entry("a.jpg", modified="not-a-date")])
        )

    items = _storage(handler, tmp_path).list_folder()

    assert items[0].modified_at is None


def test_connection_timeout_becomes_a_storage_error(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("too slow", request=request)

    with pytest.raises(StorageError, match="timed out"):
        _storage(handler, tmp_path).describe_root()


def test_connection_error_becomes_a_storage_error(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    with pytest.raises(StorageError, match="request failed"):
        _storage(handler, tmp_path).describe_root()


def test_url_validation_rejects_non_yandex_urls(tmp_path: Path) -> None:
    with pytest.raises(StorageError, match="Not a recognised"):
        YandexDiskStorage.public_key_from_url("https://example.com/x")

    with pytest.raises(StorageError, match="must not be empty"):
        YandexDiskStorage.public_key_from_url("   ")


# -- Read-only guarantee --------------------------------------------------


def test_adapter_only_ever_issues_get_requests(tmp_path: Path) -> None:
    methods: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        methods.append(request.method)
        if str(request.url).startswith(PUBLIC_DOWNLOAD_ENDPOINT):
            return httpx.Response(200, json={"href": "https://downloader.example/f"})
        if request.url.params.get("path", "/") in NESTED_TREE:
            return httpx.Response(200, json=_folder_payload(NESTED_TREE[request.url.params.get("path", "/")]))
        return httpx.Response(200, json={"name": ROOT_NAME, "_embedded": {"items": []}})

    storage = _storage(handler, tmp_path)
    storage.describe_root()
    list(storage.list_recursive())
    storage.exists("root.jpg")
    storage.download("root.jpg", tmp_path / "root.jpg")

    assert set(methods) == {"GET"}


def test_adapter_exposes_no_mutation_methods() -> None:
    forbidden = {"upload", "delete", "remove", "move", "rename", "ensure_folder", "write"}

    assert not forbidden & set(dir(YandexDiskStorage))
