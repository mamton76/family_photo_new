"""Place ↔ LatLon linkage, and the final-Place coordinate lookup (route B)."""

from __future__ import annotations

from pathlib import Path

from photoarchive.catalog.learning import learn_from_rows
from photoarchive.catalog.models import ConfidenceStatus, EntityType
from photoarchive.catalog.places import coordinates_for_place, resolve_place
from photoarchive.catalog.store import DictionaryStore
from photoarchive.geo import LatLon
from photoarchive.models import RemoteSourceItem, WorkflowStatus
from photoarchive.parsing.descriptions import (
    DescriptionEntry,
    ReconciledEntry,
    Reconciliation,
)
from photoarchive.parsing.suggestions import Suggestion
from photoarchive.review.builder import build_rows
from photoarchive.review.model import ReviewRow

SCHOOL = LatLon(55.618485, 37.600828)
HOME = LatLon(55.619898, 37.598040)
FAR = LatLon(59.934280, 30.335099)


def _store(tmp_path: Path) -> DictionaryStore:
    store = DictionaryStore(tmp_path / "dict.sqlite")
    store.initialize()
    return store


def _row(status=WorkflowStatus.REVIEW, **kwargs) -> ReviewRow:
    row = ReviewRow(reference=kwargs.pop("reference", "020"), **kwargs)
    row.status = status
    return row


def _reconciliation(reference="020", text=""):
    """A photo row. Empty text models a folder with no DOCX."""
    entry = DescriptionEntry(reference=reference, paragraphs=(text,), text=text)
    photo = RemoteSourceItem(
        name=f"{reference}.jpg", relative_path=f"{reference}.jpg", is_directory=False
    )
    return Reconciliation(entries=(ReconciledEntry(entry=entry, photo=photo),))


def _scan(store: DictionaryStore, existing_row: ReviewRow, suggestion=None):
    """Rebuild one existing row with route B enabled."""
    dictionary = store.load()
    outcome, _ = build_rows(
        _reconciliation(),
        {"020": suggestion or Suggestion()},
        existing={"020": existing_row},
        states={},
        place_lookup=lambda value: resolve_place(dictionary, value),
    )
    return outcome, outcome.rows[0]


# -- Resolution -----------------------------------------------------------


def test_exact_canonical_resolves(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.add_place("Школа 565", SCHOOL)

    resolution = resolve_place(store.load(), "Школа 565")

    assert resolution.resolved
    assert resolution.canonical == "Школа 565"
    assert resolution.latlon.format() == SCHOOL.format()


def test_confirmed_alias_resolves_to_canonical(tmp_path: Path) -> None:
    store = _store(tmp_path)
    place = store.add_place("Дома на Днепропетровской", HOME)
    store.add_alias(
        EntityType.PLACE, place, "Дом на Днепропетровской", ConfidenceStatus.CONFIRMED
    )

    resolution = resolve_place(store.load(), "Дом на Днепропетровской")

    assert resolution.canonical == "Дома на Днепропетровской"
    assert resolution.latlon.format() == HOME.format()


def test_resolution_is_case_and_whitespace_tolerant(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.add_place("Школа 565", SCHOOL)

    assert resolve_place(store.load(), "  школа   565 ").resolved


def test_candidate_alias_does_not_resolve(tmp_path: Path) -> None:
    store = _store(tmp_path)
    place = store.add_place("Школа 565", SCHOOL)
    store.add_alias(EntityType.PLACE, place, "школа", ConfidenceStatus.CANDIDATE)

    assert not resolve_place(store.load(), "школа").resolved
    assert coordinates_for_place(store.load(), "школа") == ""


def test_unknown_place_resolves_to_nothing(tmp_path: Path) -> None:
    assert not resolve_place(_store(tmp_path).load(), "Неизвестно").resolved
    assert not resolve_place(_store(tmp_path).load(), "").resolved


def test_two_places_sharing_a_name_are_ambiguous(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.add_place("Школа 565", SCHOOL)
    other = store.add_place("Другая школа", FAR)
    store.add_alias(EntityType.PLACE, other, "Школа 565", ConfidenceStatus.CONFIRMED)

    resolution = resolve_place(store.load(), "Школа 565")

    assert resolution.is_ambiguous
    assert not resolution.resolved
    assert resolution.latlon is None


# -- Route B: final Place drives Suggested LatLon --------------------------


def test_final_place_fills_blank_latlon_without_any_docx(tmp_path: Path) -> None:
    # The real school-folder case: no description text at all.
    store = _store(tmp_path)
    store.add_place("Школа 565", SCHOOL)

    outcome, row = _scan(store, _row(place="Школа 565"))

    assert row.suggested_latlon == SCHOOL.format()
    assert row.latlon == SCHOOL.format()
    assert len(outcome.place_lookups) == 1


def test_final_place_alias_fills_latlon_and_keeps_the_typed_spelling(tmp_path: Path) -> None:
    store = _store(tmp_path)
    place = store.add_place("Дома на Днепропетровской", HOME)
    store.add_alias(
        EntityType.PLACE, place, "Дом на Днепропетровской", ConfidenceStatus.CONFIRMED
    )

    _, row = _scan(store, _row(place="Дом на Днепропетровской"))

    assert row.latlon == HOME.format()
    # The reviewer's own wording is never rewritten to the canonical form.
    assert row.place == "Дом на Днепропетровской"
    assert row.suggested_place == "Дома на Днепропетровской"


def test_non_empty_final_latlon_is_preserved(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.add_place("Школа 565", SCHOOL)

    _, row = _scan(store, _row(place="Школа 565", latlon=FAR.format()))

    assert row.latlon == FAR.format()
    assert row.suggested_latlon == SCHOOL.format()


def test_place_without_coordinates_fills_nothing(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.add_place("Школа 565")

    outcome, row = _scan(store, _row(place="Школа 565"))

    assert row.suggested_latlon == ""
    assert row.latlon == ""
    assert outcome.place_lookups == []


def test_ambiguous_final_place_supplies_no_coordinates(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.add_place("Школа 565", SCHOOL)
    other = store.add_place("Другая школа", FAR)
    store.add_alias(EntityType.PLACE, other, "Школа 565", ConfidenceStatus.CONFIRMED)

    outcome, row = _scan(store, _row(place="Школа 565"))

    assert row.suggested_latlon == ""
    assert row.latlon == ""
    # No canonical interpretation is offered when there isn't a single one.
    assert row.suggested_place == ""
    assert row.place == "Школа 565"
    assert len(outcome.ambiguous_places) == 1


def test_candidate_alias_place_supplies_no_coordinates(tmp_path: Path) -> None:
    store = _store(tmp_path)
    place = store.add_place("Школа 565", SCHOOL)
    store.add_alias(EntityType.PLACE, place, "школа", ConfidenceStatus.CANDIDATE)

    _, row = _scan(store, _row(place="школа"))

    assert row.latlon == ""
    # A candidate is a hint: it yields neither a canonical name nor coordinates.
    assert row.suggested_place == ""
    assert row.suggested_latlon == ""


def test_route_a_still_wins_when_the_source_text_resolves(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.add_place("Школа 565", SCHOOL)
    suggestion = Suggestion(place="Школа 565", latlon=SCHOOL.format())

    _, row = _scan(store, _row(place="Школа 565"), suggestion)

    assert row.suggested_latlon == SCHOOL.format()


def test_row_without_a_final_place_is_untouched(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.add_place("Школа 565", SCHOOL)

    outcome, row = _scan(store, _row())

    assert row.latlon == ""
    assert outcome.place_lookups == []


def test_route_b_is_idempotent(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.add_place("Школа 565", SCHOOL)

    _, first = _scan(store, _row(place="Школа 565"))
    outcome, second = _scan(store, first)

    assert second.latlon == SCHOOL.format()
    # Nothing left blank to fill on the second pass.
    assert outcome.autofilled == []


# -- Place enrichment from review rows -------------------------------------


def test_existing_place_without_coordinates_is_enriched(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.add_place("Школа 565")

    learn_from_rows(store, [_row(place="Школа 565", latlon=SCHOOL.format())])

    places = store.load().places
    assert len(places) == 1
    assert places[0].latlon.format() == SCHOOL.format()


def test_relearning_the_same_coordinates_changes_nothing(tmp_path: Path) -> None:
    store = _store(tmp_path)
    rows = [_row(place="Школа 565", latlon=SCHOOL.format())]

    learn_from_rows(store, rows)
    outcome = learn_from_rows(store, rows)

    assert outcome.latlon_added == []
    assert outcome.latlon_conflicts == []


def test_conflicting_coordinates_become_a_candidate(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.add_place("Школа 565", SCHOOL)

    outcome = learn_from_rows(store, [_row(place="Школа 565", latlon=FAR.format())])

    place = store.load().places[0]
    assert place.latlon.format() == SCHOOL.format()
    assert [point.format() for point in place.candidate_latlon] == [FAR.format()]
    assert outcome.latlon_conflicts == ["Школа 565"]


def test_coordinates_learned_through_a_confirmed_alias(tmp_path: Path) -> None:
    store = _store(tmp_path)
    place = store.add_place("Дома на Днепропетровской")
    store.add_alias(
        EntityType.PLACE, place, "Дом на Днепропетровской", ConfidenceStatus.CONFIRMED
    )

    learn_from_rows(store, [_row(place="Дом на Днепропетровской", latlon=HOME.format())])

    places = store.load().places
    assert len(places) == 1
    assert places[0].latlon.format() == HOME.format()


# -- The full Map Link -> Place -> other rows loop --------------------------


def test_map_link_teaches_a_place_and_propagates(tmp_path: Path) -> None:
    store = _store(tmp_path)

    # 1. A reviewer pastes a Maps URL on one row; the scan turns it into LatLon.
    seed = _row(place="Школа 565", map_link="https://www.google.com/maps/@55.618485,37.600828,15z")
    _, seeded = _scan(store, seed)
    assert seeded.latlon == SCHOOL.format()

    # 2. learn stores those coordinates on the Place.
    learn_from_rows(store, [seeded])
    assert store.load().places[0].latlon.format() == SCHOOL.format()

    # 3. A different row naming the same place gets them on the next scan.
    _, other = _scan(store, _row(reference="021", place="Школа 565"))
    assert other.suggested_latlon == SCHOOL.format()
    assert other.latlon == SCHOOL.format()


# -- The meaning of Suggested Place ----------------------------------------
#
# "Suggested Place" is the system's current canonical interpretation of the
# place, not merely what the source-text parser extracted. These tests pin
# that definition so it cannot be quietly narrowed back.


def test_suggested_place_shows_the_canonical_interpretation(tmp_path: Path) -> None:
    store = _store(tmp_path)
    place = store.add_place("Дома на Днепропетровской", HOME)
    store.add_alias(
        EntityType.PLACE, place, "Дом на Днепропетровской", ConfidenceStatus.CONFIRMED
    )

    _, row = _scan(store, _row(place="Дом на Днепропетровской"))

    # The reviewer sees their own wording resolved to the canonical entity.
    assert row.suggested_place == "Дома на Днепропетровской"
    assert row.place == "Дом на Днепропетровской"


def test_source_text_suggestion_is_not_replaced_by_the_lookup(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.add_place("Школа 565", SCHOOL)
    from_source = Suggestion(place="Школа 565", latlon=SCHOOL.format())

    _, row = _scan(store, _row(place="Моя школа"), from_source)

    assert row.suggested_place == "Школа 565"
    assert row.place == "Моя школа"


def test_final_place_is_never_rewritten_to_canonical(tmp_path: Path) -> None:
    store = _store(tmp_path)
    place = store.add_place("Дома на Днепропетровской", HOME)
    store.add_alias(
        EntityType.PLACE, place, "Дом на Днепропетровской", ConfidenceStatus.CONFIRMED
    )
    row = _row(place="Дом на Днепропетровской")

    for _ in range(3):
        _, row = _scan(store, row)

    assert row.place == "Дом на Днепропетровской"
    assert row.suggested_place == "Дома на Днепропетровской"
    assert row.latlon == HOME.format()


def test_unresolvable_place_leaves_the_suggestion_empty(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.add_place("Школа 565", SCHOOL)

    _, row = _scan(store, _row(place="Совсем другое место"))

    assert row.suggested_place == ""
    assert row.place == "Совсем другое место"
