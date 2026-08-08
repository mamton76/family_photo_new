"""Scanner path and folder-planning tests. No cloud access."""

from __future__ import annotations

from photoarchive.config import DEFAULT_DESCRIPTION_PATTERNS
from photoarchive.models import RemoteSourceItem, SourceRoot
from photoarchive.scanning.scanner import (
    FALLBACK_ROOT_NAME,
    description_priority,
    destination_path,
    is_photo,
    join_relative_path,
    normalize_relative_path,
    plan_folders,
    sanitize_folder_name,
)


def _file(relative_path: str) -> RemoteSourceItem:
    return RemoteSourceItem(
        name=relative_path.rsplit("/", 1)[-1],
        relative_path=relative_path,
        is_directory=False,
    )


def test_normalize_relative_path_strips_and_unifies_separators() -> None:
    assert normalize_relative_path("/1988/Dacha/") == "1988/Dacha"
    assert normalize_relative_path("1988\\Dacha") == "1988/Dacha"
    assert normalize_relative_path("1988//Dacha") == "1988/Dacha"
    assert normalize_relative_path("") == ""


def test_join_relative_path_keeps_root_empty() -> None:
    assert join_relative_path("", "1988") == "1988"
    assert join_relative_path("1988", "Dacha") == "1988/Dacha"
    assert join_relative_path("1988/Dacha", "") == "1988/Dacha"


def test_parent_path_of_nested_item() -> None:
    item = _file("Family Archive/1990/Valaam/020.jpg")
    assert item.parent_path == "Family Archive/1990/Valaam"


def test_is_photo_by_extension() -> None:
    assert is_photo("001.jpg")
    assert is_photo("001.JPEG")
    assert not is_photo("description.txt")
    assert not is_photo("noextension")


def test_plan_folders_preserves_nested_relative_paths() -> None:
    items = [
        RemoteSourceItem(name="1988", relative_path="1988", is_directory=True),
        _file("1988/Dacha/001.jpg"),
        _file("1988/Dacha/002.jpg"),
        _file("1988/Dacha/description.txt"),
        _file("1988/School/010.jpg"),
        _file("1990/Valaam/020.jpg"),
        _file("1990/Valaam/описание.txt"),
    ]

    plans = plan_folders(items, DEFAULT_DESCRIPTION_PATTERNS)

    assert [plan.folder_path for plan in plans] == [
        "1988/Dacha",
        "1988/School",
        "1990/Valaam",
    ]
    assert [photo.name for photo in plans[0].photos] == ["001.jpg", "002.jpg"]
    assert plans[0].description is not None
    assert plans[0].description.name == "description.txt"
    assert plans[1].description is None
    assert plans[2].description is not None
    assert plans[2].folder_names == ("1990", "Valaam")


def test_plan_folders_skips_folders_without_photos() -> None:
    items = [
        _file("1988/readme.txt"),
        _file("1988/description.txt"),
        _file("1988/Dacha/001.jpg"),
    ]

    plans = plan_folders(items, DEFAULT_DESCRIPTION_PATTERNS)

    assert [plan.folder_path for plan in plans] == ["1988/Dacha"]


def test_plan_folders_handles_photos_in_source_root() -> None:
    plans = plan_folders([_file("001.jpg")], DEFAULT_DESCRIPTION_PATTERNS)

    assert len(plans) == 1
    assert plans[0].folder_path == ""
    assert plans[0].folder_names == ()


# -- Dedicated destination folder per source root -------------------------


def _root(name: str, url: str = "https://disk.yandex.ru/d/abc123") -> SourceRoot:
    return SourceRoot(url=url, name=name)


def test_destination_path_nests_below_the_source_root_folder() -> None:
    root = _root("Family Archive")

    assert destination_path(root, "1990/Valaam") == "Family Archive/1990/Valaam"
    assert destination_path(root, "1990/Valaam/020.jpg") == (
        "Family Archive/1990/Valaam/020.jpg"
    )


def test_destination_path_of_source_root_itself_is_its_own_folder() -> None:
    assert destination_path(_root("Family Archive")) == "Family Archive"
    assert destination_path(_root("Family Archive"), "") == "Family Archive"


def test_two_source_roots_never_share_a_destination_subtree() -> None:
    first = destination_path(_root("Archive A"), "1990/Valaam")
    second = destination_path(_root("Archive B"), "1990/Valaam")

    assert first != second
    assert not first.startswith(second) and not second.startswith(first)


def test_sanitize_folder_name_keeps_a_root_in_one_folder() -> None:
    # Separators would otherwise split one source root across several folders.
    assert sanitize_folder_name("1988/Dacha") == "1988-Dacha"
    assert sanitize_folder_name("a\\b") == "a-b"
    assert sanitize_folder_name("  Family   Archive  ") == "Family Archive"
    assert sanitize_folder_name("Семейный архив") == "Семейный архив"


def test_sanitize_folder_name_falls_back_when_empty() -> None:
    assert sanitize_folder_name("") == FALLBACK_ROOT_NAME
    assert sanitize_folder_name("   ") == FALLBACK_ROOT_NAME
    assert sanitize_folder_name("...") == FALLBACK_ROOT_NAME


def test_destination_path_uses_the_sanitized_name() -> None:
    assert destination_path(_root("1988/Dacha"), "001.jpg") == "1988-Dacha/001.jpg"


# -- Deterministic description selection ----------------------------------


def test_description_priority_follows_configuration_order() -> None:
    patterns = ("описание.txt", "description.txt")

    assert description_priority("описание.txt", patterns) == 0
    assert description_priority("description.txt", patterns) == 1
    assert description_priority("DESCRIPTION.TXT", patterns) == 1
    assert description_priority("readme.txt", patterns) is None


def test_multiple_description_files_resolve_by_configuration_order() -> None:
    items = [
        _file("1990/Valaam/020.jpg"),
        _file("1990/Valaam/description.txt"),
        _file("1990/Valaam/описание.txt"),
    ]

    plans = plan_folders(items, DEFAULT_DESCRIPTION_PATTERNS)

    assert plans[0].has_ambiguous_description
    # "описание.txt" comes first in DEFAULT_DESCRIPTION_PATTERNS and so wins,
    # regardless of the order the storage provider listed the files in.
    assert plans[0].description is not None
    assert plans[0].description.name == "описание.txt"
    assert [item.name for item in plans[0].descriptions] == [
        "описание.txt",
        "description.txt",
    ]


def test_description_selection_is_independent_of_listing_order() -> None:
    photo = _file("1990/Valaam/020.jpg")
    first = _file("1990/Valaam/описание.txt")
    second = _file("1990/Valaam/description.txt")

    forward = plan_folders([photo, first, second], DEFAULT_DESCRIPTION_PATTERNS)
    reverse = plan_folders([photo, second, first], DEFAULT_DESCRIPTION_PATTERNS)

    assert forward[0].description.name == reverse[0].description.name


def test_single_description_file_is_not_ambiguous() -> None:
    items = [_file("1988/Dacha/001.jpg"), _file("1988/Dacha/description.txt")]

    plans = plan_folders(items, DEFAULT_DESCRIPTION_PATTERNS)

    assert not plans[0].has_ambiguous_description
    assert plans[0].description.name == "description.txt"


def test_folder_without_description_has_none() -> None:
    plans = plan_folders([_file("1988/School/010.jpg")], DEFAULT_DESCRIPTION_PATTERNS)

    assert plans[0].description is None
    assert plans[0].descriptions == []
    assert not plans[0].has_ambiguous_description
