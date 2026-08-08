"""Filename-to-description matching tests. Pure functions, no cloud access."""

from __future__ import annotations

from photoarchive.parsing.descriptions import PlainTextDescriptionParser, description_hash
from photoarchive.scanning.matcher import (
    filename_stem,
    find_referenced_filename,
    locate_reference,
    match_fragments_to_photos,
    split_fragments,
    strip_reference,
)


def test_split_fragments_drops_blank_lines() -> None:
    assert split_fragments("020.jpg — Tonya\n\n  \n021.jpg — Anya\n") == [
        "020.jpg — Tonya",
        "021.jpg — Anya",
    ]


def test_filename_stem() -> None:
    assert filename_stem("020.jpg") == "020"
    assert filename_stem("no_extension") == "no_extension"


def test_find_referenced_filename_matches_full_name_and_stem() -> None:
    filenames = ["020.jpg", "021.jpg"]

    assert find_referenced_filename("020.jpg — Tonya and Anya", filenames) == "020.jpg"
    assert find_referenced_filename("021 - Valaam, boat pier", filenames) == "021.jpg"
    assert find_referenced_filename("A summer at the dacha", filenames) is None


def test_match_fragments_attributes_by_explicit_reference() -> None:
    filenames = ["020.jpg", "021.jpg"]
    fragments = [
        "Valaam, summer 1990",
        "020.jpg — Tonya and Anya",
        "021 - boat pier",
    ]

    result = match_fragments_to_photos(filenames, fragments)

    assert result.text_for("020.jpg") == "Tonya and Anya"
    assert result.text_for("021.jpg") == "boat pier"
    assert result.shared_fragments == ("Valaam, summer 1990",)


def test_match_fragments_leaves_unmentioned_photos_empty() -> None:
    result = match_fragments_to_photos(["001.jpg", "002.jpg"], ["001.jpg — Anya"])

    assert result.text_for("001.jpg") == "Anya"
    assert result.text_for("002.jpg") == ""
    assert [match.filename for match in result.matches] == ["001.jpg", "002.jpg"]


def test_match_fragments_without_any_photos() -> None:
    result = match_fragments_to_photos([], ["some text"])

    assert result.matches == ()
    assert result.shared_fragments == ("some text",)


# -- References are not required to start at character 0 ------------------


def test_locate_reference_reports_the_span_of_the_reference() -> None:
    reference = locate_reference("Photo 020.jpg — Tonya", ["020.jpg"])

    assert reference is not None
    assert reference.filename == "020.jpg"
    assert (reference.start, reference.end) == (6, 13)


def test_reference_in_the_middle_of_a_fragment() -> None:
    result = match_fragments_to_photos(["020.jpg"], ["Photo 020.jpg — Tonya and Anya"])

    assert result.text_for("020.jpg") == "Tonya and Anya"


def test_reference_at_the_end_of_a_fragment_uses_the_text_before_it() -> None:
    result = match_fragments_to_photos(["020.jpg"], ["Tonya and Anya (020.jpg)"])

    assert result.text_for("020.jpg") == "Tonya and Anya"


def test_stem_reference_in_the_middle_of_a_fragment() -> None:
    result = match_fragments_to_photos(["021.jpg"], ["На фото 021 — Тоня"])

    assert result.text_for("021.jpg") == "Тоня"


def test_fragment_that_is_only_a_filename_yields_no_description() -> None:
    result = match_fragments_to_photos(["020.jpg"], ["020.jpg"])

    assert result.text_for("020.jpg") == ""
    assert result.shared_fragments == ()


def test_stem_match_respects_word_boundaries() -> None:
    # "020" must not match inside "1020" or "0201".
    assert find_referenced_filename("photo 1020 here", ["020.jpg"]) is None
    assert find_referenced_filename("photo 0201 here", ["020.jpg"]) is None
    assert find_referenced_filename("photo 020 here", ["020.jpg"]) == "020.jpg"


def test_longer_filename_wins_over_a_shorter_prefix() -> None:
    filenames = ["02.jpg", "021.jpg"]

    assert find_referenced_filename("see 021.jpg now", filenames) == "021.jpg"
    assert find_referenced_filename("see 02.jpg now", filenames) == "02.jpg"


def test_strip_reference_trims_surrounding_punctuation() -> None:
    fragment = "Photo 020.jpg: Tonya"
    reference = locate_reference(fragment, ["020.jpg"])

    assert reference is not None
    assert strip_reference(fragment, reference) == "Tonya"


def test_matching_is_case_insensitive() -> None:
    result = match_fragments_to_photos(["IMG_020.JPG"], ["see img_020.jpg — Tonya"])

    assert result.text_for("IMG_020.JPG") == "Tonya"


def test_plain_text_parser_produces_fragments_and_stable_hash() -> None:
    parser = PlainTextDescriptionParser()

    parsed = parser.parse("020.jpg — Tonya\n021.jpg — Anya\n")

    assert parsed.fragments == ("020.jpg — Tonya", "021.jpg — Anya")
    assert parsed.content_hash == description_hash("020.jpg — Tonya\r\n021.jpg — Anya")
