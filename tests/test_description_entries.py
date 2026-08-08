"""Entry grouping, section context, source notes and reconciliation."""

from __future__ import annotations

from photoarchive.models import RemoteSourceItem, WorkflowStatus
from photoarchive.parsing.descriptions import (
    build_reference_context,
    deduplicate,
    extract_source_notes,
    filename_like_reference,
    is_entry_start,
    is_section_divider,
    known_photo_reference,
    leading_token,
    parse_description_document,
    reconcile_entries,
)


def _reference(paragraph: str, photos: tuple[str, ...] = ()) -> str | None:
    """Detect an entry start using only the folder's own photo vocabulary."""
    return is_entry_start(paragraph, build_reference_context(photos))


def _photo(name: str, folder: str = "") -> RemoteSourceItem:
    path = f"{folder}/{name}" if folder else name
    return RemoteSourceItem(name=name, relative_path=path, is_directory=False)


# -- Reference detection --------------------------------------------------


def test_filename_shaped_references_need_no_context() -> None:
    assert _reference("20200512_150442   1979 февраль") == "20200512_150442"
    assert _reference("IMG_001   description") == "IMG_001"
    assert _reference("IMG_001.jpg   description") == "IMG_001.jpg"
    assert _reference("DSC0042   description") == "DSC0042"
    assert _reference("DSC_1201   description") == "DSC_1201"


def test_continuation_paragraphs_are_never_entry_starts() -> None:
    assert _reference("1979. Тоня Мамаева (3г). Дома") is None
    assert _reference("1980 февраль. Дома") is None
    assert _reference("27 лет") is None
    assert _reference("(6.5лет). Настя Платова (4г)") is None
    assert _reference("Валерий Мамаев (39лет).") is None
    assert _reference("- нет фото") is None
    assert _reference("В зоопарке в Берлине.") is None


def test_leading_token_trims_trailing_punctuation() -> None:
    assert leading_token("1979. Тоня") == "1979"
    assert leading_token("020 Тоня") == "020"
    assert leading_token("") is None


def test_known_photo_reference_matches_name_or_stem() -> None:
    context = build_reference_context(("020.jpg", "IMG_001.jpeg"))

    assert known_photo_reference("020", context)
    assert known_photo_reference("020.jpg", context)
    assert known_photo_reference("img_001", context)
    assert not known_photo_reference("999", context)


def test_filename_like_needs_a_digit_and_a_letter_or_underscore() -> None:
    assert filename_like_reference("IMG_001")
    assert filename_like_reference("20200512_150442")
    assert not filename_like_reference("1979")
    assert not filename_like_reference("Валерий")


# -- Numeric references, resolved by context ------------------------------


def test_numeric_reference_matching_a_present_photo() -> None:
    assert _reference("020 Тоня на даче", ("020.jpg", "021.jpg")) == "020"
    assert _reference("021 Аня у дома", ("020.jpg", "021.jpg")) == "021"


def test_year_stays_a_continuation_next_to_three_digit_photos() -> None:
    # "1979" is four digits; the established style here is three.
    assert _reference("1979. Дома на Днепропетровской.", ("020.jpg",)) is None


def test_numeric_absent_reference_uses_the_established_width() -> None:
    present = ("018.jpg", "019.jpg", "022.jpg")

    assert _reference("020 missing photo description", present) == "020"
    assert _reference("021 missing photo description", present) == "021"


def test_numeric_tokens_are_not_references_without_evidence() -> None:
    # No numeric-stemmed photo present, so a bare number proves nothing.
    assert _reference("020 something", ("20200512_1.jpg",)) is None
    assert _reference("123 something", ()) is None


def test_present_numeric_stems_group_two_entries() -> None:
    document = parse_description_document(
        ["020 Тоня на даче", "продолжение", "021 Аня у дома"],
        photo_names=["020.jpg", "021.jpg"],
    )

    assert [entry.reference for entry in document.entries] == ["020", "021"]
    assert document.entries[0].paragraphs == ("Тоня на даче", "продолжение")


def test_year_like_paragraph_is_attached_as_continuation() -> None:
    document = parse_description_document(
        ["020 Тоня", "1979. Дома на Днепропетровской."],
        photo_names=["020.jpg"],
    )

    assert len(document.entries) == 1
    assert document.entries[0].paragraphs == ("Тоня", "1979. Дома на Днепропетровской.")


def test_absent_numeric_references_become_described_absent() -> None:
    present = ["018.jpg", "019.jpg", "022.jpg"]
    document = parse_description_document(
        [
            "018 present one",
            "020 missing photo description",
            "021 missing photo description",
        ],
        photo_names=present,
    )
    photos = [_photo(name) for name in present]

    result = reconcile_entries(document.entries, photos)

    assert len(document.entries) == 3
    absent = {item.entry.reference for item in result.described_but_absent}
    assert absent == {"020", "021"}
    for item in result.described_but_absent:
        assert item.status is WorkflowStatus.DESCRIBED_ABSENT


def test_year_never_becomes_an_absent_entry() -> None:
    document = parse_description_document(
        ["20200512_150442 Тоня", "1979. Тоня Мамаева (3г)."],
        photo_names=["20200512_150442.jpg"],
    )
    result = reconcile_entries(document.entries, [_photo("20200512_150442.jpg")])

    assert [entry.reference for entry in document.entries] == ["20200512_150442"]
    assert result.described_but_absent == ()


def test_numeric_style_can_be_established_by_the_document_itself() -> None:
    # No photos present, but "020.jpg" in the text proves a 3-digit style.
    document = parse_description_document(["020.jpg first", "021 second"])

    assert [entry.reference for entry in document.entries] == ["020.jpg", "021"]


# -- Section dividers -----------------------------------------------------


def test_section_divider_detection_is_conservative() -> None:
    assert is_section_divider("Далее Аня или Тоня ????????")
    assert is_section_divider("далее мама с Тоней")
    assert not is_section_divider("Тоня Мамаева (3г)")
    assert not is_section_divider("20200512_150442 something")


# -- Source notes ---------------------------------------------------------


def test_source_note_is_lifted_out_of_the_description() -> None:
    raw = "1979. Тоня Мамаева (3г). Дома на Днепропетровской. - нет фото"

    cleaned, notes = extract_source_notes(raw)

    assert cleaned == "1979. Тоня Мамаева (3г). Дома на Днепропетровской."
    assert notes == ("нет фото",)


def test_text_without_a_note_is_unchanged() -> None:
    cleaned, notes = extract_source_notes("1981. Тоня Мамаева (5лет).")

    assert cleaned == "1981. Тоня Мамаева (5лет)."
    assert notes == ()


# -- Entry grouping -------------------------------------------------------


GROUPING_INPUT = [
    "20200512_150442 first line",
    "continuation one",
    "continuation two",
    "20200512_150501 next entry",
    "continuation",
]


def test_multi_paragraph_entries_are_grouped() -> None:
    document = parse_description_document(GROUPING_INPUT)

    assert len(document.entries) == 2

    first, second = document.entries
    assert first.reference == "20200512_150442"
    assert first.paragraphs == ("first line", "continuation one", "continuation two")
    assert first.text == "first line continuation one continuation two"

    assert second.reference == "20200512_150501"
    assert second.paragraphs == ("next entry", "continuation")


def test_paragraphs_before_the_first_entry_are_preamble() -> None:
    document = parse_description_document(
        ["Ф-ТоняМам-76-83-разное", "20200512_150442 first"]
    )

    assert document.preamble == ("Ф-ТоняМам-76-83-разное",)
    assert len(document.entries) == 1


def test_empty_paragraphs_are_ignored() -> None:
    document = parse_description_document(["", "   ", "IMG_001 text", ""])

    assert len(document.entries) == 1
    assert document.entries[0].text == "text"


# -- Section context inheritance ------------------------------------------


SECTION_INPUT = [
    "Далее Аня или Тоня ????????",
    "20200512_150442 first",
    "continuation",
    "20200512_150501 second",
    "Далее мама с Тоней",
    "20200512_151010 third",
]


def test_section_context_is_inherited_by_following_entries() -> None:
    document = parse_description_document(SECTION_INPUT)

    contexts = {entry.reference: entry.section_context for entry in document.entries}

    assert contexts["20200512_150442"] == "Далее Аня или Тоня ????????"
    assert contexts["20200512_150501"] == "Далее Аня или Тоня ????????"
    assert contexts["20200512_151010"] == "Далее мама с Тоней"


def test_section_dividers_are_recorded_and_not_described() -> None:
    document = parse_description_document(SECTION_INPUT)

    assert document.section_dividers == (
        "Далее Аня или Тоня ????????",
        "Далее мама с Тоней",
    )
    # Divider text never leaks into a photo description.
    for entry in document.entries:
        assert "Далее" not in entry.text


def test_a_divider_ends_the_active_entry() -> None:
    document = parse_description_document(
        ["IMG_001 text", "continuation", "Далее новая тема", "IMG_002 other"]
    )

    assert document.entries[0].paragraphs == ("text", "continuation")
    assert document.entries[0].section_context is None
    assert document.entries[1].section_context == "Далее новая тема"


def test_entries_before_any_divider_have_no_section() -> None:
    document = parse_description_document(["IMG_001 text"])

    assert document.entries[0].section_context is None


# -- Combined real-format sample ------------------------------------------


REAL_SAMPLE = [
    "Ф-ТоняМам-76-83-разное",
    "20200512_150241   1979. Тоня Мамаева (3г). Дома на Днепропетровской. - нет фото",
    "20200512_150442   1979 февраль 19. Тоня Мамаева (2.5г)-справа. Сережа Мамаев",
    "(6.5лет). Настя Платова (4г)-в центре. Аня Архангельская (3г).",
    "Валерий Мамаев (39лет). Дома на Днепропетровской.  - нет фото",
]


def test_real_format_sample_is_parsed_correctly() -> None:
    document = parse_description_document(REAL_SAMPLE)

    assert document.preamble == ("Ф-ТоняМам-76-83-разное",)
    assert len(document.entries) == 2

    first, second = document.entries
    assert first.reference == "20200512_150241"
    assert first.text == "1979. Тоня Мамаева (3г). Дома на Днепропетровской."
    assert first.source_notes == ("нет фото",)

    assert second.reference == "20200512_150442"
    assert len(second.paragraphs) == 3
    assert "Валерий Мамаев (39лет)" in second.text
    assert second.source_notes == ("нет фото",)
    assert "нет фото" not in second.text


def test_ages_are_left_as_source_text() -> None:
    document = parse_description_document(REAL_SAMPLE)

    # No birth year is derived from "(2.5г)" plus a date; the text is verbatim.
    assert "(2.5г)" in document.entries[1].text


# -- Reconciliation -------------------------------------------------------


def test_stem_reference_matches_the_photo_file() -> None:
    document = parse_description_document(["20200512_150442 text"])
    photos = [_photo("20200512_150442.jpg")]

    result = reconcile_entries(document.entries, photos)

    assert result.entries[0].present
    assert result.entries[0].photo is not None
    assert result.entries[0].photo.name == "20200512_150442.jpg"
    assert result.entries[0].status is WorkflowStatus.NEW


def test_full_filename_reference_matches() -> None:
    document = parse_description_document(["IMG_001.jpg text"])

    result = reconcile_entries(document.entries, [_photo("IMG_001.jpg")])

    assert result.entries[0].present


def test_described_but_absent_is_preserved() -> None:
    document = parse_description_document(["20200512_999999 описание"])

    result = reconcile_entries(document.entries, [_photo("20200512_150442.jpg")])

    absent = result.described_but_absent
    assert len(absent) == 1
    assert absent[0].photo is None
    assert absent[0].status is WorkflowStatus.DESCRIBED_ABSENT
    # The description survives even though there is no photo to preview.
    assert absent[0].entry.text == "описание"


def test_described_absent_is_not_source_missing() -> None:
    document = parse_description_document(["20200512_999999 text"])

    result = reconcile_entries(document.entries, [])

    assert result.entries[0].status is not WorkflowStatus.SOURCE_MISSING
    assert result.entries[0].status is WorkflowStatus.DESCRIBED_ABSENT


def test_photos_without_a_description_are_reported() -> None:
    document = parse_description_document(["IMG_001 text"])
    photos = [_photo("IMG_001.jpg"), _photo("IMG_002.jpg")]

    result = reconcile_entries(document.entries, photos)

    assert [photo.name for photo in result.undescribed_photos] == ["IMG_002.jpg"]


def test_twenty_four_entries_against_twelve_photos() -> None:
    # Mirrors the real folder: the document describes twice as many photos as
    # are present.
    paragraphs = [f"20200512_1500{index:02d} описание {index}" for index in range(24)]
    photos = [_photo(f"20200512_1500{index:02d}.jpg") for index in range(12)]

    document = parse_description_document(paragraphs)
    result = reconcile_entries(document.entries, photos)

    assert len(document.entries) == 24
    assert len(result.present_and_described) == 12
    assert len(result.described_but_absent) == 12
    assert len(result.undescribed_photos) == 0


def test_matching_is_case_insensitive() -> None:
    document = parse_description_document(["img_001 text"])

    result = reconcile_entries(document.entries, [_photo("IMG_001.JPG")])

    assert result.entries[0].present


# -- Source-note duplicates -----------------------------------------------


def test_duplicate_source_notes_are_preserved_raw() -> None:
    document = parse_description_document(
        ["IMG_001 первая часть - нет фото", "вторая часть - нет фото"]
    )

    entry = document.entries[0]
    assert entry.source_notes == ("нет фото", "нет фото")
    assert document.source_note_count == 2


def test_duplicate_source_notes_collapse_for_display() -> None:
    document = parse_description_document(
        ["IMG_001 первая часть - нет фото", "вторая часть - нет фото"]
    )

    entry = document.entries[0]
    assert entry.display_source_notes == ("нет фото",)
    assert document.display_source_note_count == 1


def test_deduplicate_preserves_first_seen_order() -> None:
    assert deduplicate(("b", "a", "b", "c", "a")) == ("b", "a", "c")
    assert deduplicate(()) == ()
