"""Portable state: provenance, serialisation, sync, fingerprints, recovery."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from photoarchive.catalog.learning import learn_from_rows
from photoarchive.catalog.models import ConfidenceStatus, EntityType
from photoarchive.catalog.store import DictionaryStore
from photoarchive.geo import LatLon
from photoarchive.models import SourceRoot, WorkflowStatus
from photoarchive.portable.bootstrap import bootstrap
from photoarchive.portable.catalog_state import export_catalog, import_catalog
from photoarchive.portable.fingerprint import build_fingerprint, needs_rebuild
from photoarchive.portable.models import (
    ArtifactSyncState,
    ItemState,
    Manifest,
    SourceState,
    StateVersionError,
)
from photoarchive.portable.provenance import (
    DIRTY_SUFFIX,
    UNKNOWN_COMMIT,
    MachineIdentity,
    OperationProvenance,
    app_commit,
    format_timestamp,
    load_machine_identity,
)
from photoarchive.portable.store import (
    PortableArchiveState,
    PortableStateStore,
    StateConflictError,
)
from photoarchive.portable.sync import SyncAction, decide, describe_conflict, record_sync
from photoarchive.review.model import ReviewRow
from photoarchive.state import StateRepository

MOSCOW = LatLon(55.751244, 37.618423)
MACHINE = MachineIdentity(machine_id="7c26e8", label="Tonya MacBook")


def _row(**kwargs) -> ReviewRow:
    status = kwargs.pop("status", WorkflowStatus.REVIEW)
    row = ReviewRow(reference=kwargs.pop("reference", "020"), **kwargs)
    row.status = status
    return row


# -- Machine identity ------------------------------------------------------


def test_machine_id_is_created_once_and_reused(tmp_path: Path) -> None:
    path = tmp_path / "machine.json"

    first = load_machine_identity(path)
    second = load_machine_identity(path)

    assert first.machine_id == second.machine_id
    assert path.exists()


def test_explicit_label_overrides_the_hostname(tmp_path: Path) -> None:
    path = tmp_path / "machine.json"
    load_machine_identity(path)

    relabelled = load_machine_identity(path, label="Tonya MacBook")

    assert relabelled.label == "Tonya MacBook"
    assert load_machine_identity(path).label == "Tonya MacBook"


def test_identity_does_not_record_personal_data(tmp_path: Path) -> None:
    path = tmp_path / "machine.json"
    load_machine_identity(path, label="Home PC")

    stored = json.loads(path.read_text(encoding="utf-8"))

    assert set(stored) == {"machine_id", "label"}


def test_unreadable_identity_file_regenerates(tmp_path: Path) -> None:
    path = tmp_path / "machine.json"
    path.write_text("not json", encoding="utf-8")

    identity = load_machine_identity(path)

    assert identity.machine_id


def test_a_new_machine_id_does_not_break_anything(tmp_path: Path) -> None:
    # An OS reinstall produces a new id; sync correctness must not depend on it.
    first = load_machine_identity(tmp_path / "a.json")
    second = load_machine_identity(tmp_path / "b.json")

    assert first.machine_id != second.machine_id
    state = ArtifactSyncState(path="catalog.xlsx", last_common_hash="sha256:aa")
    assert decide("catalog.xlsx", "sha256:aa", "sha256:aa", state).action is SyncAction.NOOP


# -- Provenance ------------------------------------------------------------


def test_timestamps_are_utc_with_a_z_suffix() -> None:
    moment = datetime(2026, 8, 9, 10, 42, 17, tzinfo=timezone.utc)

    assert format_timestamp(moment) == "2026-08-09T10:42:17Z"


def test_provenance_round_trips() -> None:
    provenance = OperationProvenance.create(MACHINE, "run-1", commit="df0fb26")

    restored = OperationProvenance.from_dict(provenance.as_dict())

    assert restored == provenance
    assert restored.machine_label == "Tonya MacBook"


def test_provenance_describes_itself_readably() -> None:
    provenance = OperationProvenance(
        machine_id="7c26e8", machine_label="Tonya MacBook", run_id="run-1",
        app_commit="df0fb26", at="2026-08-09T10:42:17Z",
    )

    described = provenance.describe()

    assert "09 Aug 2026 10:42 UTC" in described
    assert "Tonya MacBook" in described and "df0fb26" in described


def test_app_commit_reports_a_dirty_tree_honestly() -> None:
    commit = app_commit()

    assert commit
    if commit != UNKNOWN_COMMIT:
        assert DIRTY_SUFFIX in commit or len(commit) >= 7


def test_missing_git_metadata_degrades_gracefully(tmp_path: Path) -> None:
    assert app_commit(tmp_path) == UNKNOWN_COMMIT


# -- Portable state serialisation -----------------------------------------


def _state() -> PortableArchiveState:
    source = SourceState(
        source_id="abc123",
        source_url="https://disk.yandex.ru/d/abc123",
        display_name="Архив A",
        drive_folder_id="drive-folder-1",
        items={
            "|020": ItemState(
                key="|020", relative_path="020.jpg", source_hash="sha256:aa",
                drive_file_id="drive-file-1", build_fingerprint="sha256:bb",
                status="APPROVED",
            )
        },
    )
    return PortableArchiveState(
        manifest=Manifest(artifacts={"catalog.xlsx": ArtifactSyncState(path="catalog.xlsx")}),
        sources={"abc123": source},
        catalog={"schema_version": 1, "people": [], "places": [], "tags": [], "evidence": []},
    )


def test_state_round_trips_through_disk(tmp_path: Path) -> None:
    store = PortableStateStore(tmp_path / "_archive_state")
    store.publish(_state(), MACHINE, "run-1", expected_generation=0)

    loaded = store.load()

    source = loaded.sources["abc123"]
    assert source.display_name == "Архив A"
    assert source.drive_folder_id == "drive-folder-1"
    assert source.items["|020"].drive_file_id == "drive-file-1"
    assert source.items["|020"].build_fingerprint == "sha256:bb"


def test_publishing_is_byte_deterministic(tmp_path: Path) -> None:
    first = PortableStateStore(tmp_path / "a")
    second = PortableStateStore(tmp_path / "b")
    first.publish(_state(), MACHINE, "run-1", expected_generation=0)
    second.publish(_state(), MACHINE, "run-1", expected_generation=0)

    a = json.loads(first.source_path("abc123").read_text(encoding="utf-8"))
    b = json.loads(second.source_path("abc123").read_text(encoding="utf-8"))
    assert a == b


def test_machines_are_registered_in_the_manifest(tmp_path: Path) -> None:
    store = PortableStateStore(tmp_path / "state")
    store.publish(_state(), MACHINE, "run-1", expected_generation=0)

    manifest = store.load().manifest

    assert manifest.machines["7c26e8"].label == "Tonya MacBook"
    assert manifest.machines["7c26e8"].first_seen


def test_future_schema_version_is_refused(tmp_path: Path) -> None:
    store = PortableStateStore(tmp_path / "state")
    store.publish(_state(), MACHINE, "run-1", expected_generation=0)
    data = json.loads(store.manifest_path.read_text(encoding="utf-8"))
    data["schema_version"] = 99
    store.manifest_path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(StateVersionError, match="newer version"):
        store.load()


def test_missing_schema_version_is_refused(tmp_path: Path) -> None:
    with pytest.raises(StateVersionError, match="no schema_version"):
        Manifest.from_dict({"state_generation": 1})


def test_absent_state_loads_as_empty(tmp_path: Path) -> None:
    loaded = PortableStateStore(tmp_path / "nothing").load()

    assert loaded.generation == 0
    assert loaded.sources == {}


def test_no_temporary_files_survive_a_publish(tmp_path: Path) -> None:
    store = PortableStateStore(tmp_path / "state")
    store.publish(_state(), MACHINE, "run-1", expected_generation=0)

    assert list((tmp_path / "state").rglob("*.tmp")) == []


# -- Optimistic concurrency ------------------------------------------------


def test_expected_generation_succeeds(tmp_path: Path) -> None:
    store = PortableStateStore(tmp_path / "state")

    assert store.publish(_state(), MACHINE, "run-1", expected_generation=0) == 1
    assert store.publish(_state(), MACHINE, "run-2", expected_generation=1) == 2


def test_a_stale_write_is_refused(tmp_path: Path) -> None:
    store = PortableStateStore(tmp_path / "state")
    store.publish(_state(), MACHINE, "run-1", expected_generation=0)
    # Another machine publishes while our run was working.
    store.publish(_state(), MachineIdentity("91ab42", "Home PC"), "run-2", expected_generation=1)

    with pytest.raises(StateConflictError, match="changed during this run"):
        store.publish(_state(), MACHINE, "run-3", expected_generation=1)

    assert store.read_generation() == 2


def test_a_refused_write_changes_nothing(tmp_path: Path) -> None:
    store = PortableStateStore(tmp_path / "state")
    store.publish(_state(), MACHINE, "run-1", expected_generation=0)
    store.publish(_state(), MACHINE, "run-2", expected_generation=1)
    before = store.manifest_path.read_bytes()

    with pytest.raises(StateConflictError):
        store.publish(_state(), MACHINE, "run-3", expected_generation=0)

    assert store.manifest_path.read_bytes() == before


# -- Three-way sync --------------------------------------------------------


def _baseline(last: str | None = "sha256:base") -> ArtifactSyncState:
    return ArtifactSyncState(
        path="catalog.xlsx",
        last_common_hash=last,
        last_sync=OperationProvenance.create(MACHINE, "run-1", commit="df0fb26"),
    )


def test_nothing_changed_is_a_noop() -> None:
    decision = decide("catalog.xlsx", "sha256:base", "sha256:base", _baseline())

    assert decision.action is SyncAction.NOOP
    assert not decision.changes_anything


def test_local_only_change_uploads() -> None:
    decision = decide("catalog.xlsx", "sha256:local", "sha256:base", _baseline())

    assert decision.action is SyncAction.UPLOAD


def test_remote_only_change_downloads() -> None:
    decision = decide("catalog.xlsx", "sha256:base", "sha256:remote", _baseline())

    assert decision.action is SyncAction.DOWNLOAD


def test_both_changed_is_a_conflict_and_overwrites_neither() -> None:
    decision = decide("catalog.xlsx", "sha256:local", "sha256:remote", _baseline())

    assert decision.action is SyncAction.CONFLICT
    assert decision.is_conflict
    assert not decision.changes_anything


def test_identical_edits_on_both_sides_are_not_a_conflict() -> None:
    decision = decide("catalog.xlsx", "sha256:same", "sha256:same", _baseline())

    assert decision.action is SyncAction.NOOP


def test_first_sync_adopts_whichever_side_exists() -> None:
    assert decide("c.xlsx", "sha256:l", None, _baseline(None)).action is SyncAction.ADOPT_LOCAL
    assert decide("c.xlsx", None, "sha256:r", _baseline(None)).action is SyncAction.ADOPT_REMOTE


def test_two_independent_copies_without_a_baseline_conflict() -> None:
    # No baseline and no semantic content to compare: conservatively a
    # first-sync conflict, not an ordinary one — there is no BASE.
    decision = decide("c.xlsx", "sha256:l", "sha256:r", _baseline(None))

    assert decision.action is SyncAction.FIRST_SYNC_CONFLICT
    assert decision.is_conflict
    assert decision.is_first_sync


def test_conflict_report_names_the_last_common_sync() -> None:
    report = describe_conflict(decide("catalog.xlsx", "sha256:l", "sha256:r", _baseline()))

    assert "CONFLICT: catalog.xlsx" in report
    assert "Tonya MacBook" in report
    assert "df0fb26" in report
    assert "Nothing was overwritten" in report


def test_conflict_report_handles_a_missing_baseline() -> None:
    report = describe_conflict(decide("catalog.xlsx", "sha256:l", "sha256:r", None))

    assert "never" in report


def test_recording_a_sync_moves_the_baseline() -> None:
    state = _baseline()
    provenance = OperationProvenance.create(MACHINE, "run-9", commit="abc1234")

    record_sync(state, "sha256:new", provenance, drive_file_id="drive-1")

    assert state.last_common_hash == "sha256:new"
    assert state.drive_file_id == "drive-1"
    assert decide("catalog.xlsx", "sha256:new", "sha256:new", state).action is SyncAction.NOOP


# -- Build fingerprint -----------------------------------------------------


def test_same_inputs_give_the_same_fingerprint() -> None:
    row = _row(date="1979", place="Михнево", people="Тоня Мамаева")

    assert build_fingerprint(row, "sha256:src") == build_fingerprint(row, "sha256:src")


def test_metadata_change_changes_the_fingerprint() -> None:
    before = build_fingerprint(_row(date="1979"), "sha256:src")
    after = build_fingerprint(_row(date="1980"), "sha256:src")

    assert before != after


def test_source_change_changes_the_fingerprint() -> None:
    row = _row(date="1979")

    assert build_fingerprint(row, "sha256:one") != build_fingerprint(row, "sha256:two")


def test_build_version_change_changes_the_fingerprint() -> None:
    row = _row(date="1979")

    assert build_fingerprint(row, "sha256:src", 1) != build_fingerprint(row, "sha256:src", 2)


def test_list_order_does_not_change_the_fingerprint() -> None:
    first = build_fingerprint(_row(people="Тоня Мамаева, Аня Архангельская"), "sha256:s")
    second = build_fingerprint(_row(people="Аня Архангельская; Тоня Мамаева"), "sha256:s")

    assert first == second


def test_provenance_values_do_not_affect_the_fingerprint() -> None:
    row = _row(date="1979")
    baseline = build_fingerprint(row, "sha256:src")

    # Status, review reason and source text are not build inputs.
    row.status = WorkflowStatus.APPROVED
    row.review_reason = "Description changed"
    row.source_description = "совершенно другой текст"

    assert build_fingerprint(row, "sha256:src") == baseline


def test_needs_rebuild_detects_drift() -> None:
    row = _row(date="1979")
    recorded = build_fingerprint(row, "sha256:src")

    assert not needs_rebuild(row, "sha256:src", recorded)
    assert needs_rebuild(row, "sha256:changed", recorded)
    assert needs_rebuild(row, "sha256:src", None)


# -- Catalog portability ---------------------------------------------------


def _dictionary(tmp_path: Path, name: str = "dict.sqlite") -> DictionaryStore:
    store = DictionaryStore(tmp_path / name)
    store.initialize()
    return store


def test_catalog_exports_and_imports_whole(tmp_path: Path) -> None:
    original = _dictionary(tmp_path)
    learn_from_rows(
        original,
        [_row(people="Антонина Мамаева", place="Михнево", tags="дача",
              latlon=MOSCOW.format(), source_description="Тоня Мамаева")],
    )
    exported = export_catalog(original)

    restored = _dictionary(tmp_path, "restored.sqlite")
    counts = import_catalog(restored, exported)

    dictionary = restored.load()
    assert [p.canonical_name for p in dictionary.people] == ["Антонина Мамаева"]
    assert dictionary.places[0].latlon.format() == MOSCOW.format()
    assert [t.canonical_tag for t in dictionary.tags] == ["дача"]
    assert counts["evidence"] > 0


def test_stable_ids_survive_the_round_trip(tmp_path: Path) -> None:
    original = _dictionary(tmp_path)
    person = original.add_person("Антонина Мамаева")
    original.add_alias(EntityType.PERSON, person, "Тоня", ConfidenceStatus.CONFIRMED)

    restored = _dictionary(tmp_path, "restored.sqlite")
    import_catalog(restored, export_catalog(original))

    assert restored.load().people[0].person_id == person
    assert "Тоня" in restored.load().people[0].confirmed_aliases


def test_evidence_survives_sqlite_deletion(tmp_path: Path) -> None:
    original = _dictionary(tmp_path)
    learn_from_rows(
        original,
        [_row(people="Антонина Мамаева", source_description="Тоня Мамаева")],
    )
    exported = export_catalog(original)
    before = original.evidence_for(EntityType.PERSON, "Антонина Мамаева")

    (tmp_path / "dict.sqlite").unlink()  # the laptop dies

    restored = _dictionary(tmp_path, "restored.sqlite")
    import_catalog(restored, exported)

    after = restored.evidence_for(EntityType.PERSON, "Антонина Мамаева")
    assert len(after) == len(before)
    assert {item.candidate_text for item in after} == {item.candidate_text for item in before}


def test_candidate_and_confirmed_stay_distinct(tmp_path: Path) -> None:
    original = _dictionary(tmp_path)
    place = original.add_place("Михнево", MOSCOW)
    original.add_alias(EntityType.PLACE, place, "дача", ConfidenceStatus.CANDIDATE)
    original.propose_place_latlon(place, LatLon(59.9, 30.3))

    restored = _dictionary(tmp_path, "restored.sqlite")
    import_catalog(restored, export_catalog(original))

    reloaded = restored.load().places[0]
    assert reloaded.candidate_aliases == ("дача",)
    assert reloaded.confirmed_aliases == ()
    assert reloaded.latlon.format() == MOSCOW.format()
    assert len(reloaded.candidate_latlon) == 1


def test_importing_twice_does_not_duplicate(tmp_path: Path) -> None:
    original = _dictionary(tmp_path)
    learn_from_rows(original, [_row(people="Антонина Мамаева", place="Михнево")])
    exported = export_catalog(original)

    restored = _dictionary(tmp_path, "restored.sqlite")
    import_catalog(restored, exported)
    import_catalog(restored, exported)

    dictionary = restored.load()
    assert len(dictionary.people) == 1
    assert len(dictionary.places) == 1
