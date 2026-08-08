"""Scanner path and folder-planning tests. No cloud access."""

from __future__ import annotations

from photoarchive.models import RemoteSourceItem, SourceRoot
from photoarchive.scanning.scanner import (
    FALLBACK_ROOT_NAME,
    destination_path,
    is_description_document,
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
        _file("1988/Dacha/Dacha.docx"),
        _file("1988/School/010.jpg"),
        _file("1990/Valaam/020.jpg"),
        _file("1990/Valaam/Валаам.docx"),
    ]

    plans = plan_folders(items)

    assert [plan.folder_path for plan in plans] == [
        "1988/Dacha",
        "1988/School",
        "1990/Valaam",
    ]
    assert [photo.name for photo in plans[0].photos] == ["001.jpg", "002.jpg"]
    assert plans[0].description is not None
    assert plans[0].description.name == "Dacha.docx"
    assert plans[1].description is None
    assert plans[2].description is not None
    assert plans[2].folder_names == ("1990", "Valaam")


def test_plan_folders_skips_folders_without_photos() -> None:
    items = [
        _file("1988/readme.txt"),
        _file("1988/notes.docx"),
        _file("1988/Dacha/001.jpg"),
    ]

    plans = plan_folders(items)

    assert [plan.folder_path for plan in plans] == ["1988/Dacha"]


def test_plan_folders_handles_photos_in_source_root() -> None:
    plans = plan_folders([_file("001.jpg")])

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


# -- DOCX-only description discovery --------------------------------------


def test_is_description_document_accepts_only_docx() -> None:
    assert is_description_document("archive.docx")
    assert is_description_document("ARCHIVE.DOCX")
    assert not is_description_document("archive.rtf")
    assert not is_description_document("archive.txt")
    assert not is_description_document("archive.doc")
    assert not is_description_document("archive.pdf")


def test_exactly_one_docx_is_selected() -> None:
    items = [_file("1990/020.jpg"), _file("1990/Валаам.docx")]

    plan = plan_folders(items)[0]

    assert plan.has_description
    assert plan.description.name == "Валаам.docx"
    assert not plan.has_ambiguous_description


def test_zero_docx_means_no_description() -> None:
    plan = plan_folders([_file("1990/020.jpg")])[0]

    assert plan.description is None
    assert not plan.has_ambiguous_description
    assert not plan.has_description


def test_multiple_docx_is_a_conflict_and_none_is_chosen() -> None:
    items = [_file("1990/020.jpg"), _file("1990/b.docx"), _file("1990/a.docx")]

    plan = plan_folders(items)[0]

    assert plan.has_ambiguous_description
    # Deliberately no automatic pick: guessing would attach wrong descriptions.
    assert plan.description is None
    assert [item.name for item in plan.docx_candidates] == ["a.docx", "b.docx"]


def test_non_photo_non_docx_files_are_preserved_as_diagnostics() -> None:
    items = [
        _file("1990/020.jpg"),
        _file("1990/Валаам.docx"),
        _file("1990/Валаам.rtf"),
        _file("1990/notes.txt"),
    ]

    plan = plan_folders(items)[0]

    # Sorted by name; ASCII sorts before Cyrillic.
    assert [item.name for item in plan.other_files] == ["notes.txt", "Валаам.rtf"]
    assert plan.description.name == "Валаам.docx"


def test_rtf_alone_is_not_treated_as_a_description() -> None:
    items = [_file("1990/020.jpg"), _file("1990/Валаам.rtf")]

    plan = plan_folders(items)[0]

    assert plan.description is None
    assert [item.name for item in plan.other_files] == ["Валаам.rtf"]


def test_plan_folders_is_deterministic() -> None:
    items = [
        _file("1990/021.jpg"),
        _file("1990/020.jpg"),
        _file("1990/b.rtf"),
        _file("1990/a.rtf"),
    ]

    first = plan_folders(items)[0]
    second = plan_folders(list(reversed(items)))[0]

    assert [p.name for p in first.photos] == [p.name for p in second.photos]
    assert [o.name for o in first.other_files] == [o.name for o in second.other_files]
