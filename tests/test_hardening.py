"""Hardening: don't rewrite unchanged files, don't act on failed observations.

Two rules that only matter once real synchronisation exists, and are much
cheaper to establish now:

* **An unchanged run must change nothing on disk.** ``.xlsx`` is a ZIP, so
  rewriting an identical workbook still produces new bytes — which would mean a
  pointless upload and a pointless portable-state generation on every run.
* **A failed observation is not a fact.** A download or listing that did not
  succeed must never be read as "the source deleted this".
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from photoarchive.catalog.service import CatalogService
from photoarchive.catalog.store import DictionaryStore
from photoarchive.geo import LatLon
from photoarchive.models import RemoteSourceItem, SourceRoot, WorkflowStatus
from photoarchive.portable.exporter import ScannedSource, build_portable_snapshot
from photoarchive.portable.models import (
    ArtifactSyncState,
    ItemState,
    Manifest,
    SourceState,
)
from photoarchive.portable.provenance import MachineIdentity, OperationProvenance
from photoarchive.portable.store import PortableArchiveState
from photoarchive.review.builder import RowState, build_rows
from photoarchive.review.excel import ReviewWorkbookService, rows_signature
from photoarchive.review.model import ReviewRow
from photoarchive.state import StateRepository

MACHINE = MachineIdentity(machine_id="7c26e8", label="Test machine")
ROOT = SourceRoot(url="https://disk.yandex.ru/d/abc123", name="Архив A")


def _row(reference: str, **kwargs) -> ReviewRow:
    status = kwargs.pop("status", WorkflowStatus.NEW)
    row = ReviewRow(reference=reference, **kwargs)
    row.status = status
    return row


# -- Unchanged workbooks are not rewritten ---------------------------------


def test_identical_rows_have_the_same_signature() -> None:
    first = [_row("020", place="Михнево"), _row("021")]
    second = [_row("020", place="Михнево"), _row("021")]

    assert rows_signature(first) == rows_signature(second)


def test_any_visible_change_changes_the_signature() -> None:
    baseline = rows_signature([_row("020", place="Михнево")])

    assert rows_signature([_row("020", place="Другое")]) != baseline
    assert rows_signature([_row("020", place="Михнево", notes="x")]) != baseline
    assert (
        rows_signature([_row("020", place="Михнево", status=WorkflowStatus.REVIEW)])
        != baseline
    )
    assert rows_signature([_row("021", place="Михнево")]) != baseline


def test_row_order_changes_the_signature() -> None:
    # Order is part of the workbook's content; a reordered sheet is a change.
    assert rows_signature([_row("020"), _row("021")]) != rows_signature(
        [_row("021"), _row("020")]
    )


def test_unchanged_workbook_is_left_untouched(tmp_path: Path) -> None:
    path = tmp_path / "review.xlsx"
    rows = [_row("020", place="Михнево"), _row("021")]
    service = ReviewWorkbookService()

    assert service.write(path, rows) is True
    before_bytes = path.read_bytes()
    before_mtime = path.stat().st_mtime_ns
    time.sleep(0.01)

    written = service.write(path, rows, previous_signature=rows_signature(rows))

    assert written is False
    assert path.read_bytes() == before_bytes
    assert path.stat().st_mtime_ns == before_mtime


def test_a_changed_row_does_rewrite(tmp_path: Path) -> None:
    path = tmp_path / "review.xlsx"
    service = ReviewWorkbookService()
    original = [_row("020", place="Михнево")]
    service.write(path, original)

    updated = [_row("020", place="Другое место")]
    written = service.write(path, updated, previous_signature=rows_signature(original))

    assert written is True
    assert service.read(path)["020"].place == "Другое место"


def test_a_missing_workbook_is_always_written(tmp_path: Path) -> None:
    rows = [_row("020")]

    written = ReviewWorkbookService().write(
        tmp_path / "review.xlsx", rows, previous_signature=rows_signature(rows)
    )

    assert written is True


def test_unchanged_catalog_is_left_untouched(tmp_path: Path) -> None:
    store = DictionaryStore(tmp_path / "dict.sqlite")
    store.initialize()
    store.add_place("Михнево", LatLon(55.751244, 37.618423))
    service = CatalogService()

    path, _ = service.export(store, tmp_path)
    assert service.written is True
    before = path.read_bytes()
    before_mtime = path.stat().st_mtime_ns
    time.sleep(0.01)

    service.export(store, tmp_path)

    assert service.written is False
    assert path.read_bytes() == before
    assert path.stat().st_mtime_ns == before_mtime


def test_a_changed_catalog_does_rewrite(tmp_path: Path) -> None:
    store = DictionaryStore(tmp_path / "dict.sqlite")
    store.initialize()
    store.add_place("Михнево")
    service = CatalogService()
    service.export(store, tmp_path)

    store.add_person("Антонина Мамаева")
    service.export(store, tmp_path)

    assert service.written is True


# -- A failed observation is not a fact -------------------------------------


def _entry(reference: str):
    from photoarchive.parsing.descriptions import (
        DescriptionEntry,
        ReconciledEntry,
        Reconciliation,
    )

    photo = RemoteSourceItem(
        name=f"{reference}.jpg", relative_path=f"{reference}.jpg", is_directory=False
    )
    entry = DescriptionEntry(reference=reference, paragraphs=("текст",), text="текст")
    return Reconciliation(entries=(ReconciledEntry(entry=entry, photo=photo),))


def _empty_reconciliation():
    from photoarchive.parsing.descriptions import Reconciliation

    return Reconciliation()


def test_unreadable_description_never_marks_rows_missing() -> None:
    outcome, states = build_rows(_entry("020"), {})
    row = outcome.rows[0]
    row.people = "Антонина Мамаева"

    rescan, next_states = build_rows(
        _empty_reconciliation(),
        {},
        existing={"020": row},
        states=states,
        descriptions_readable=False,
    )

    assert rescan.went_missing == []
    assert rescan.rows[0].people == "Антонина Мамаева"
    # The bookkeeping is carried forward, so the next good scan sees no change.
    assert next_states["020"].description_hash == states["020"].description_hash


def test_a_failed_listing_writes_no_state(tmp_path: Path) -> None:
    """A provider error must abort before any observation is recorded."""

    class Failing:
        def list_recursive(self, relative_path: str = ""):
            yield RemoteSourceItem(
                name="a.jpg", relative_path="a.jpg", is_directory=False
            )
            raise RuntimeError("connection reset mid-listing")

    state = StateRepository(tmp_path / "archive.sqlite")
    state.initialize()
    root_id = state.register_source_root(ROOT)

    with pytest.raises(RuntimeError):
        # The scan materialises the listing before recording anything, so a
        # mid-pagination failure never reaches the database.
        state.record_listing(root_id, list(Failing().list_recursive()))

    assert state.load_source_items(root_id) == []


def test_a_partial_listing_does_not_orphan_known_items(tmp_path: Path) -> None:
    state = StateRepository(tmp_path / "archive.sqlite")
    state.initialize()
    root_id = state.register_source_root(ROOT)

    complete = [
        RemoteSourceItem(name=f"{n}.jpg", relative_path=f"{n}.jpg", is_directory=False)
        for n in ("a", "b", "c")
    ]
    state.record_listing(root_id, complete)

    class Failing:
        def __iter__(self):
            yield complete[0]
            raise RuntimeError("page 2 failed")

    with pytest.raises(RuntimeError):
        state.record_listing(root_id, list(Failing()))

    # b and c must not be marked missing on the strength of a failed read.
    stored = {row["relative_path"]: row for row in state.load_source_items(root_id)}
    assert all(stored[name]["missing_since"] is None for name in stored)


# -- Portable refresh preserves downstream ids ------------------------------


def _previous_with_downstream() -> PortableArchiveState:
    provenance = OperationProvenance.create(MACHINE, "run-old", commit="abc1234")
    source = SourceState(
        source_id=ROOT.identity,
        source_url=ROOT.url,
        display_name=ROOT.name,
        drive_folder_id="drive-folder-1",
        items={
            "|020": ItemState(
                key="|020",
                drive_file_id="drive-file-1",
                drive_path="Архив A/020.jpg",
                build_fingerprint="sha256:fingerprint",
                built_hash="sha256:built",
                last_build=provenance,
                google_photos_media_id="media-1",
                google_photos_product_url="https://photos.google.com/lr/photo/1",
                published_at="2026-08-01T00:00:00Z",
            )
        },
        artifacts={
            "review.xlsx": ArtifactSyncState(
                path="Архив A/review.xlsx",
                drive_file_id="drive-review-1",
                last_common_hash="sha256:agreed",
                last_sync=provenance,
            )
        },
    )
    return PortableArchiveState(
        manifest=Manifest(
            artifacts={
                "catalog.xlsx": ArtifactSyncState(
                    path="catalog.xlsx",
                    drive_file_id="drive-catalog",
                    last_common_hash="sha256:catalog-agreed",
                    last_sync=provenance,
                )
            }
        ),
        sources={ROOT.identity: source},
        catalog={"schema_version": 1, "people": [], "places": [], "tags": [], "evidence": []},
    )


def _refresh(tmp_path: Path, previous: PortableArchiveState) -> PortableArchiveState:
    """A normal scan that knows nothing about Drive or Photos."""
    store = DictionaryStore(tmp_path / "dict.sqlite")
    store.initialize()

    scanned = ScannedSource(
        source_root=ROOT,
        items=[
            RemoteSourceItem(
                name="020.jpg", relative_path="020.jpg", is_directory=False,
                content_hash="sha256:src",
            )
        ],
        row_states={"": {"020": RowState(identity="020", photo_hash="sha256:src")}},
    )
    return build_portable_snapshot(
        scanned=[scanned], dictionary=store, previous=previous,
        machine=MACHINE, run_id="run-new",
    )


def test_refresh_preserves_drive_and_photos_ids(tmp_path: Path) -> None:
    snapshot = _refresh(tmp_path, _previous_with_downstream())

    source = snapshot.sources[ROOT.identity]
    item = source.items["|020"]

    assert source.drive_folder_id == "drive-folder-1"
    assert item.drive_file_id == "drive-file-1"
    assert item.drive_path == "Архив A/020.jpg"
    assert item.build_fingerprint == "sha256:fingerprint"
    assert item.built_hash == "sha256:built"
    assert item.last_build is not None and item.last_build.run_id == "run-old"
    assert item.google_photos_media_id == "media-1"
    assert item.google_photos_product_url == "https://photos.google.com/lr/photo/1"
    assert item.published_at == "2026-08-01T00:00:00Z"


def test_refresh_preserves_sync_baselines(tmp_path: Path) -> None:
    snapshot = _refresh(tmp_path, _previous_with_downstream())

    workbook = snapshot.sources[ROOT.identity].artifacts["review.xlsx"]
    catalog = snapshot.manifest.artifacts["catalog.xlsx"]

    assert workbook.last_common_hash == "sha256:agreed"
    assert workbook.drive_file_id == "drive-review-1"
    assert workbook.last_sync.run_id == "run-old"
    assert catalog.last_common_hash == "sha256:catalog-agreed"
    assert catalog.drive_file_id == "drive-catalog"


def test_refresh_still_updates_scan_derived_facts(tmp_path: Path) -> None:
    snapshot = _refresh(tmp_path, _previous_with_downstream())

    item = snapshot.sources[ROOT.identity].items["|020"]

    assert item.source_hash == "sha256:src"
    assert item.relative_path == "020.jpg"


# -- last_common_hash is strictly a remote-sync fact ------------------------


def test_a_local_run_never_advances_the_sync_baseline(tmp_path: Path) -> None:
    workbook = tmp_path / "review.xlsx"
    ReviewWorkbookService().write(workbook, [_row("020")])

    store = DictionaryStore(tmp_path / "dict.sqlite")
    store.initialize()
    scanned = ScannedSource(source_root=ROOT, items=[], workbooks=[workbook])

    snapshot = build_portable_snapshot(
        scanned=[scanned], dictionary=store,
        previous=PortableArchiveState(Manifest(), {}, {"schema_version": 1}),
        machine=MACHINE, run_id="run-1",
    )

    artifact = snapshot.sources[ROOT.identity].artifacts["review.xlsx"]
    # Generating a file locally is not the same as synchronising it.
    assert artifact.local_content_hash
    assert artifact.last_common_hash is None
    assert artifact.last_sync is None
    assert artifact.drive_file_id is None
    assert not artifact.is_synced


def test_an_existing_baseline_is_not_overwritten_by_a_local_run(tmp_path: Path) -> None:
    workbook = tmp_path / "review.xlsx"
    ReviewWorkbookService().write(workbook, [_row("020")])
    previous = _previous_with_downstream()
    previous.sources[ROOT.identity].artifacts["review.xlsx"].path = str(workbook)

    store = DictionaryStore(tmp_path / "dict.sqlite")
    store.initialize()
    snapshot = build_portable_snapshot(
        scanned=[ScannedSource(source_root=ROOT, items=[], workbooks=[workbook])],
        dictionary=store, previous=previous, machine=MACHINE, run_id="run-2",
    )

    artifact = snapshot.sources[ROOT.identity].artifacts["review.xlsx"]
    assert artifact.last_common_hash == "sha256:agreed"
    assert artifact.last_sync.run_id == "run-old"
    # But the local hash does track the file as it is now.
    assert artifact.local_content_hash != "sha256:agreed"


# -- Incident states settle rather than oscillating -------------------------


def test_a_row_that_returned_to_review_stays_put(tmp_path: Path) -> None:
    """After an incident a row keeps its state; repeated scans do not churn it.

    The real archive's 12 described-but-absent rows went to REVIEW when a
    transient DOCX failure was mistaken for deletion. They must stay
    DESCRIBED_ABSENT in source-presence terms and must not be quietly reset.
    """
    from photoarchive.parsing.descriptions import (
        DescriptionEntry,
        ReconciledEntry,
        Reconciliation,
    )

    entry = DescriptionEntry(reference="020", paragraphs=("текст",), text="текст")
    absent = Reconciliation(entries=(ReconciledEntry(entry=entry, photo=None),))

    outcome, states = build_rows(absent, {})
    row = outcome.rows[0]
    assert row.status is WorkflowStatus.DESCRIBED_ABSENT

    # Two further unchanged scans must not move it again.
    for _ in range(2):
        outcome, states = build_rows(
            absent, {}, existing={"020": row}, states=states
        )
        row = outcome.rows[0]
        assert row.status is WorkflowStatus.DESCRIBED_ABSENT
        assert outcome.unchanged == ["020"]
        assert outcome.went_missing == []
        assert outcome.became_present == []
