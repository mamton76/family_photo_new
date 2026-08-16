"""Rescan semantics: what a scan may rewrite and what it must never touch."""

from __future__ import annotations

from photoarchive.models import RemoteSourceItem, WorkflowStatus
from photoarchive.parsing.descriptions import (
    DescriptionEntry,
    ReconciledEntry,
    Reconciliation,
)
from photoarchive.parsing.suggestions import Suggestion
from photoarchive.review.builder import build_rows
from photoarchive.review.model import (
    REASON_DESCRIPTION_CHANGED,
    REASON_DESCRIPTION_CHANGED_AFTER_APPROVAL,
    REASON_PREVIOUSLY_ABSENT_FOUND,
    REASON_PHOTO_RETURNED,
    REASON_SOURCE_MISSING,
    REASON_SOURCE_PHOTO_CHANGED,
    REASON_SOURCE_TEXT_STALE,
)


def _photo(name: str) -> RemoteSourceItem:
    return RemoteSourceItem(name=name, relative_path=name, is_directory=False)


def _entry(reference: str, text: str = "описание", section: str | None = None):
    return DescriptionEntry(
        reference=reference, paragraphs=(text,), text=text, section_context=section
    )


def _reconciliation(*pairs, undescribed=()) -> Reconciliation:
    return Reconciliation(
        entries=tuple(ReconciledEntry(entry=entry, photo=photo) for entry, photo in pairs),
        undescribed_photos=tuple(undescribed),
    )


SUGGESTION = Suggestion(
    date="1979", place="Дача", latlon="55.700000, 37.600000",
    people=("Антонина Мамаева",), tags=("дача",),
)


def _first_scan(reference="020", text="описание", photo=True, suggestion=SUGGESTION):
    reconciliation = _reconciliation((_entry(reference, text), _photo("020.jpg") if photo else None))
    return build_rows(reconciliation, {reference: suggestion})


# -- First creation -------------------------------------------------------


def test_new_row_copies_suggestions_into_final_fields() -> None:
    outcome, _ = _first_scan()
    row = outcome.rows[0]

    assert row.date == "1979"
    assert row.place == "Дача"
    assert row.latlon == "55.700000, 37.600000"
    assert row.people == "Антонина Мамаева"
    assert row.tags == "дача"
    assert outcome.created == ["020"]


def test_new_present_row_starts_as_new() -> None:
    outcome, _ = _first_scan()

    assert outcome.rows[0].status is WorkflowStatus.NEW


def test_new_absent_row_starts_as_described_absent() -> None:
    outcome, _ = _first_scan(photo=False)

    assert outcome.rows[0].status is WorkflowStatus.DESCRIBED_ABSENT
    assert outcome.rows[0].filename == ""


# -- Rescan preserves user-owned fields -----------------------------------


def test_rescan_preserves_manually_changed_final_fields() -> None:
    outcome, states = _first_scan()
    row = outcome.rows[0]
    row.date = "1981"
    row.people = "Аня Архангельская"
    existing = {"020": row}

    new_suggestion = Suggestion(date="1990", people=("Кто-то Другой",))
    rescan, _ = build_rows(
        _reconciliation((_entry("020"), _photo("020.jpg"))),
        {"020": new_suggestion},
        existing=existing,
        states=states,
    )

    updated = rescan.rows[0]
    assert updated.date == "1981"
    assert updated.people == "Аня Архангельская"
    # Suggestions are machine-owned and do get refreshed.
    assert updated.suggested_date == "1990"
    assert updated.suggested_people == "Кто-то Другой"


def test_rescan_preserves_final_fields_even_when_they_equal_old_suggestions() -> None:
    # The reviewer may have deliberately kept the suggested value. The pipeline
    # cannot tell, so it must not overwrite either way.
    outcome, states = _first_scan()
    existing = {"020": outcome.rows[0]}

    rescan, _ = build_rows(
        _reconciliation((_entry("020"), _photo("020.jpg"))),
        {"020": Suggestion(date="1990", place="Другое место")},
        existing=existing,
        states=states,
    )

    assert rescan.rows[0].date == "1979"
    assert rescan.rows[0].place == "Дача"


def test_changed_description_updates_suggestions_but_not_finals() -> None:
    outcome, states = _first_scan(text="старое описание")
    existing = {"020": outcome.rows[0]}

    rescan, _ = build_rows(
        _reconciliation((_entry("020", "новое описание"), _photo("020.jpg"))),
        {"020": Suggestion(date="1985")},
        existing=existing,
        states=states,
    )

    row = rescan.rows[0]
    assert row.source_description == "новое описание"
    assert row.suggested_date == "1985"
    assert row.date == "1979"
    assert row.status is WorkflowStatus.REVIEW
    assert row.review_reason == REASON_DESCRIPTION_CHANGED
    assert rescan.description_changed == ["020"]


def test_approved_row_returns_to_review_without_losing_final_metadata() -> None:
    outcome, states = _first_scan(text="старое описание")
    row = outcome.rows[0]
    row.status = WorkflowStatus.APPROVED
    row.date = "1979-06-01"
    states["020"].status = WorkflowStatus.APPROVED.value

    rescan, _ = build_rows(
        _reconciliation((_entry("020", "новое описание"), _photo("020.jpg"))),
        {"020": SUGGESTION},
        existing={"020": row},
        states=states,
    )

    updated = rescan.rows[0]
    assert updated.status is WorkflowStatus.REVIEW
    assert updated.review_reason == REASON_DESCRIPTION_CHANGED_AFTER_APPROVAL
    assert updated.date == "1979-06-01"


def test_unchanged_description_changes_nothing() -> None:
    outcome, states = _first_scan()
    existing = {"020": outcome.rows[0]}

    rescan, _ = build_rows(
        _reconciliation((_entry("020"), _photo("020.jpg"))),
        {"020": SUGGESTION},
        existing=existing,
        states=states,
    )

    assert rescan.unchanged == ["020"]
    assert rescan.description_changed == []
    assert rescan.rows[0].review_reason == ""


# -- Photo changes --------------------------------------------------------


def test_changed_image_hash_returns_the_row_to_review() -> None:
    reconciliation = _reconciliation((_entry("020"), _photo("020.jpg")))
    outcome, states = build_rows(
        reconciliation, {"020": SUGGESTION}, photo_hashes={"020": "hash-one"}
    )
    outcome.rows[0].date = "1981"

    rescan, _ = build_rows(
        reconciliation,
        {"020": SUGGESTION},
        existing={"020": outcome.rows[0]},
        states=states,
        photo_hashes={"020": "hash-two"},
    )

    row = rescan.rows[0]
    assert row.status is WorkflowStatus.REVIEW
    assert row.review_reason == REASON_SOURCE_PHOTO_CHANGED
    assert row.date == "1981"
    assert rescan.photo_changed == ["020"]


def test_missing_photo_keeps_the_row_and_final_metadata() -> None:
    outcome, states = _first_scan()
    outcome.rows[0].date = "1981"

    rescan, _ = build_rows(
        _reconciliation(),
        {},
        existing={"020": outcome.rows[0]},
        states=states,
    )

    assert len(rescan.rows) == 1
    assert rescan.rows[0].status is WorkflowStatus.SOURCE_MISSING
    assert rescan.rows[0].date == "1981"
    assert rescan.went_missing == ["020"]


# -- Absent becoming present ----------------------------------------------


def test_absent_row_that_becomes_present_reuses_the_same_row() -> None:
    outcome, states = _first_scan(photo=False)
    row = outcome.rows[0]
    row.date = "1979-02-19"
    assert row.status is WorkflowStatus.DESCRIBED_ABSENT

    rescan, _ = build_rows(
        _reconciliation((_entry("020"), _photo("020.jpg"))),
        {"020": SUGGESTION},
        existing={"020": row},
        states=states,
    )

    assert len(rescan.rows) == 1
    updated = rescan.rows[0]
    assert updated.status is WorkflowStatus.REVIEW
    assert updated.review_reason == REASON_PREVIOUSLY_ABSENT_FOUND
    assert updated.filename == "020.jpg"
    assert updated.date == "1979-02-19"
    assert rescan.became_present == ["020"]


def test_reference_and_filename_share_one_identity() -> None:
    # "020" described and "020.jpg" present must never become two rows.
    outcome, _ = build_rows(
        _reconciliation((_entry("020"), _photo("020.jpg"))), {"020": Suggestion()}
    )

    assert len(outcome.rows) == 1


# -- Ordering and undescribed photos --------------------------------------


def test_undescribed_photos_get_rows_after_described_entries() -> None:
    reconciliation = _reconciliation(
        (_entry("020"), _photo("020.jpg")), undescribed=[_photo("099.jpg")]
    )

    outcome, _ = build_rows(reconciliation, {})

    assert [row.reference for row in outcome.rows] == ["020", "099"]
    assert outcome.rows[1].filename == "099.jpg"


def test_row_order_is_stable_across_unchanged_rescans() -> None:
    reconciliation = _reconciliation(
        (_entry("021"), _photo("021.jpg")),
        (_entry("020"), _photo("020.jpg")),
        undescribed=[_photo("099.jpg")],
    )
    first, states = build_rows(reconciliation, {})
    existing = {row.reference: row for row in first.rows}

    second, _ = build_rows(reconciliation, {}, existing=existing, states=states)

    assert [row.reference for row in first.rows] == [row.reference for row in second.rows]


# -- Map Link -------------------------------------------------------------


def test_parsable_map_link_updates_final_latlon() -> None:
    outcome, states = _first_scan()
    row = outcome.rows[0]
    row.map_link = "https://www.google.com/maps/@59.934280,30.335099,15z"

    rescan, _ = build_rows(
        _reconciliation((_entry("020"), _photo("020.jpg"))),
        {"020": SUGGESTION},
        existing={"020": row},
        states=states,
    )

    updated = rescan.rows[0]
    assert updated.latlon == "59.934280, 30.335099"
    assert updated.map_link == "https://www.google.com/maps/@59.934280,30.335099,15z"
    # The suggestion is untouched: a pasted link is a human act, not evidence.
    assert updated.suggested_latlon == "55.700000, 37.600000"
    assert rescan.map_links_applied == ["020"]


def test_unparseable_map_link_leaves_latlon_alone() -> None:
    outcome, states = _first_scan()
    row = outcome.rows[0]
    row.map_link = "https://www.google.com/maps/place/Валаам"

    rescan, _ = build_rows(
        _reconciliation((_entry("020"), _photo("020.jpg"))),
        {"020": SUGGESTION},
        existing={"020": row},
        states=states,
    )

    updated = rescan.rows[0]
    assert updated.latlon == "55.700000, 37.600000"
    assert "could not be parsed" in updated.review_reason
    assert rescan.map_links_unparsed == ["020"]


# -- An unreadable description must not look like missing photos ------------


def test_unreadable_description_does_not_mark_rows_missing() -> None:
    # A DOCX that fails to download leaves no entries. Treating that as "every
    # described photo disappeared" would rewrite a dozen rows on a network blip.
    outcome, states = _first_scan(photo=False)
    row = outcome.rows[0]
    row.date = "1979-02-19"

    rescan, next_states = build_rows(
        _reconciliation(),
        {},
        existing={"020": row},
        states=states,
        descriptions_readable=False,
    )

    assert rescan.went_missing == []
    assert rescan.unchanged == ["020"]
    assert rescan.rows[0].status is WorkflowStatus.DESCRIBED_ABSENT
    assert rescan.rows[0].date == "1979-02-19"
    # The previous bookkeeping survives, so the next good scan sees no change.
    assert next_states["020"].description_hash == states["020"].description_hash


def test_a_genuinely_missing_photo_is_still_marked() -> None:
    outcome, states = _first_scan()
    outcome.rows[0].date = "1981"

    rescan, _ = build_rows(
        _reconciliation(),
        {},
        existing={"020": outcome.rows[0]},
        states=states,
        descriptions_readable=True,
    )

    assert rescan.went_missing == ["020"]
    assert rescan.rows[0].status is WorkflowStatus.SOURCE_MISSING
    assert rescan.rows[0].date == "1981"


# -- SKIP is a decision, not a state the pipeline may revise ---------------


def test_changed_description_reports_but_does_not_unskip() -> None:
    outcome, states = _first_scan(text="старое описание")
    row = outcome.rows[0]
    row.status = WorkflowStatus.SKIP
    states["020"].status = WorkflowStatus.SKIP.value

    rescan, _ = build_rows(
        _reconciliation((_entry("020", "новое описание"), _photo("020.jpg"))),
        {"020": SUGGESTION},
        existing={"020": row},
        states=states,
    )

    updated = rescan.rows[0]
    assert updated.status is WorkflowStatus.SKIP
    assert updated.review_reason == REASON_DESCRIPTION_CHANGED
    # The change itself is still reported, so it can be reconsidered.
    assert rescan.description_changed == ["020"]


def test_changed_image_reports_but_does_not_unskip() -> None:
    reconciliation = _reconciliation((_entry("020"), _photo("020.jpg")))
    outcome, states = build_rows(
        reconciliation, {"020": SUGGESTION}, photo_hashes={"020": "hash-one"}
    )
    row = outcome.rows[0]
    row.status = WorkflowStatus.SKIP

    rescan, _ = build_rows(
        reconciliation,
        {"020": SUGGESTION},
        existing={"020": row},
        states=states,
        photo_hashes={"020": "hash-two"},
    )

    assert rescan.rows[0].status is WorkflowStatus.SKIP
    assert rescan.rows[0].review_reason == REASON_SOURCE_PHOTO_CHANGED
    assert rescan.photo_changed == ["020"]


def test_changed_image_does_not_unskip_an_undescribed_photo() -> None:
    reconciliation = _reconciliation(undescribed=[_photo("021.jpg")])
    outcome, states = build_rows(
        reconciliation, {}, photo_hashes={"021": "hash-one"}
    )
    row = outcome.rows[0]
    row.status = WorkflowStatus.SKIP

    rescan, _ = build_rows(
        reconciliation,
        {},
        existing={"021": row},
        states=states,
        photo_hashes={"021": "hash-two"},
    )

    assert rescan.rows[0].status is WorkflowStatus.SKIP
    assert rescan.rows[0].review_reason == REASON_SOURCE_PHOTO_CHANGED


def test_missing_photo_reports_but_does_not_unskip() -> None:
    outcome, states = _first_scan()
    row = outcome.rows[0]
    row.status = WorkflowStatus.SKIP

    rescan, next_states = build_rows(
        _reconciliation(),
        {},
        existing={"020": row},
        states=states,
        descriptions_readable=True,
    )

    assert rescan.rows[0].status is WorkflowStatus.SKIP
    assert rescan.rows[0].review_reason == REASON_SOURCE_MISSING
    assert rescan.went_missing == ["020"]
    # The recorded state agrees with the row, rather than claiming SOURCE_MISSING.
    assert next_states["020"].status == WorkflowStatus.SKIP.value


def test_skip_still_survives_when_a_described_photo_returns() -> None:
    outcome, states = _first_scan(photo=False)
    row = outcome.rows[0]
    assert row.status is WorkflowStatus.DESCRIBED_ABSENT
    row.status = WorkflowStatus.SKIP

    rescan, _ = build_rows(
        _reconciliation((_entry("020"), _photo("020.jpg"))),
        {"020": SUGGESTION},
        existing={"020": row},
        states=states,
    )

    assert rescan.rows[0].status is WorkflowStatus.SKIP
    assert rescan.rows[0].review_reason == REASON_PREVIOUSLY_ABSENT_FOUND


# -- A photo that comes back, and text its document no longer supplies ------


def test_a_returning_photo_says_so_rather_than_claiming_it_changed() -> None:
    outcome, states = _first_scan()
    row = outcome.rows[0]

    gone, states = build_rows(
        _reconciliation(), {}, existing={"020": row}, states=states
    )
    assert gone.rows[0].status is WorkflowStatus.SOURCE_MISSING

    back, _ = build_rows(
        _reconciliation((_entry("020"), _photo("020.jpg"))),
        {"020": SUGGESTION},
        existing={"020": gone.rows[0]},
        states=states,
    )

    returned = back.rows[0]
    assert returned.status is WorkflowStatus.REVIEW
    assert returned.review_reason == REASON_PHOTO_RETURNED
    assert back.photos_returned == ["020"]


def test_an_undescribed_photo_that_returns_is_reported_the_same_way() -> None:
    photo = _photo("021.jpg")
    outcome, states = build_rows(_reconciliation(undescribed=[photo]), {})

    gone, states = build_rows(
        _reconciliation(), {}, existing={"021": outcome.rows[0]}, states=states
    )
    back, _ = build_rows(
        _reconciliation(undescribed=[photo]),
        {},
        existing={"021": gone.rows[0]},
        states=states,
    )

    assert back.rows[0].review_reason == REASON_PHOTO_RETURNED
    assert back.photos_returned == ["021"]


def test_text_left_by_a_deleted_entry_is_kept_and_flagged_as_stale() -> None:
    """The document is gone; what it last said is not thrown away."""
    outcome, states = _first_scan(text="Тоня у школы")
    row = outcome.rows[0]
    assert row.source_description == "Тоня у школы"

    # The DOCX disappears: the photo is still there, now undescribed.
    rescan, _ = build_rows(
        _reconciliation(undescribed=[_photo("020.jpg")]),
        {},
        existing={"020": row},
        states=states,
    )

    updated = rescan.rows[0]
    assert updated.source_description == "Тоня у школы"
    assert updated.review_reason == REASON_SOURCE_TEXT_STALE
    assert rescan.stale_source_text == ["020"]
    # A source-quality remark, not a workflow decision.
    assert updated.status is WorkflowStatus.NEW


def test_a_row_that_never_had_a_description_is_not_stale() -> None:
    outcome, _ = build_rows(_reconciliation(undescribed=[_photo("021.jpg")]), {})

    assert outcome.stale_source_text == []
    assert outcome.rows[0].review_reason == ""


# -- Lasting identity and picture fingerprint ------------------------------


def test_a_photo_id_is_assigned_once_and_then_carried() -> None:
    outcome, states = _first_scan()
    assigned = outcome.rows[0].photo_id
    assert assigned.startswith("photo-")

    rescan, next_states = build_rows(
        _reconciliation((_entry("020"), _photo("020.jpg"))),
        {"020": SUGGESTION},
        existing={"020": outcome.rows[0]},
        states=states,
    )

    assert rescan.rows[0].photo_id == assigned
    assert next_states["020"].photo_id == assigned


def test_a_photo_id_survives_a_lost_workbook() -> None:
    """The pipeline's own memory is enough; the column is a display copy."""
    outcome, states = _first_scan()
    assigned = outcome.rows[0].photo_id

    rebuilt, _ = build_rows(
        _reconciliation((_entry("020"), _photo("020.jpg"))),
        {"020": SUGGESTION},
        existing={},
        states=states,
    )

    assert rebuilt.rows[0].photo_id == assigned


def test_a_fingerprint_is_kept_when_a_photo_cannot_be_fetched() -> None:
    """A scan that fetched nothing must not erase what an earlier one learned."""
    reconciliation = _reconciliation((_entry("020"), _photo("020.jpg")))
    outcome, states = build_rows(
        reconciliation, {"020": SUGGESTION}, fingerprints={"020": "ceda9e969cbebafc"}
    )
    assert states["020"].image_fingerprint == "ceda9e969cbebafc"

    _, next_states = build_rows(
        reconciliation,
        {"020": SUGGESTION},
        existing={"020": outcome.rows[0]},
        states=states,
        fingerprints={},
    )

    assert next_states["020"].image_fingerprint == "ceda9e969cbebafc"


def test_two_rows_never_share_a_photo_id() -> None:
    outcome, _ = build_rows(
        _reconciliation(
            (_entry("020"), _photo("020.jpg")), undescribed=[_photo("021.jpg")]
        ),
        {},
    )

    ids = {row.photo_id for row in outcome.rows}
    assert len(ids) == len(outcome.rows)


def test_a_source_change_never_overturns_a_duplicate_verdict() -> None:
    """`DUPLICATE` is a person's ruling on the photograph, like `SKIP`."""
    reconciliation = _reconciliation((_entry("020"), _photo("020.jpg")))
    outcome, states = build_rows(
        reconciliation, {"020": SUGGESTION}, photo_hashes={"020": "hash-one"}
    )
    row = outcome.rows[0]
    row.status = WorkflowStatus.DUPLICATE
    row.notes = "дубль photo-abc123def456"

    rescan, _ = build_rows(
        reconciliation,
        {"020": SUGGESTION},
        existing={"020": row},
        states=states,
        photo_hashes={"020": "hash-two"},
    )

    updated = rescan.rows[0]
    assert updated.status is WorkflowStatus.DUPLICATE
    assert updated.review_reason == REASON_SOURCE_PHOTO_CHANGED
    assert updated.notes == "дубль photo-abc123def456"
