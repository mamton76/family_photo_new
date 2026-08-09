"""The core promise: delete everything local and carry on.

This test simulates the disaster the portable state exists for. A machine does
real work, publishes portable state, and is then wiped — database, cache,
generated outputs, sync baselines, machine identity. A second, otherwise empty
machine bootstraps from the portable state alone and must continue safely:

* the same stable identities for source roots and dictionary entities;
* the same Drive file ids, so an upload updates rather than duplicating;
* the same evidence behind every alias;
* no rebuild of photos whose fingerprint still matches;
* no re-upload of workbooks whose content has not changed.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from photoarchive.catalog.learning import learn_from_rows
from photoarchive.catalog.models import EntityType
from photoarchive.catalog.store import DictionaryStore
from photoarchive.geo import LatLon
from photoarchive.models import SourceRoot, WorkflowStatus
from photoarchive.portable.bootstrap import bootstrap
from photoarchive.portable.catalog_state import export_catalog
from photoarchive.portable.fingerprint import build_fingerprint, needs_rebuild
from photoarchive.portable.models import ArtifactSyncState, ItemState, Manifest, SourceState
from photoarchive.portable.provenance import MachineIdentity, OperationProvenance
from photoarchive.portable.store import PortableArchiveState, PortableStateStore
from photoarchive.portable.sync import SyncAction, decide, record_sync
from photoarchive.review.model import ReviewRow
from photoarchive.state import StateRepository

MOSCOW = LatLon(55.751244, 37.618423)
ROOT = SourceRoot(url="https://disk.yandex.ru/d/abc123", name="Архив A")

FIRST_MACHINE = MachineIdentity(machine_id="7c26e8", label="Tonya MacBook")
SECOND_MACHINE = MachineIdentity(machine_id="91ab42", label="Home PC")

CATALOG_HASH = "sha256:catalog-v1"


def _row(reference: str, **kwargs) -> ReviewRow:
    status = kwargs.pop("status", WorkflowStatus.APPROVED)
    row = ReviewRow(reference=reference, **kwargs)
    row.status = status
    return row


REVIEW_ROWS = [
    _row("020", filename="020.jpg", date="1979", place="Михнево",
         latlon=MOSCOW.format(), people="Антонина Мамаева", tags="дача"),
    _row("021", filename="021.jpg", date="1980", place="Михнево",
         people="Антонина Мамаева"),
]

SOURCE_HASHES = {"020": "sha256:src-020", "021": "sha256:src-021"}
DRIVE_IDS = {"020": "drive-file-020", "021": "drive-file-021"}


def _machine_a(root: Path) -> tuple[PortableStateStore, dict[str, str]]:
    """Do real work on the first machine and publish portable state."""
    local = root / "machine-a"
    local.mkdir(parents=True, exist_ok=True)

    state = StateRepository(local / "archive.sqlite")
    state.initialize()
    state.register_source_root(ROOT)

    dictionary = DictionaryStore(local / "archive.sqlite")
    dictionary.initialize()
    learn_from_rows(dictionary, REVIEW_ROWS)

    fingerprints = {
        reference: build_fingerprint(row, SOURCE_HASHES[reference])
        for reference, row in zip(SOURCE_HASHES, REVIEW_ROWS)
    }
    provenance = OperationProvenance.create(FIRST_MACHINE, "run-a", commit="df0fb26")

    source = SourceState(
        source_id=ROOT.identity,
        source_url=ROOT.url,
        display_name=ROOT.name,
        drive_folder_id="drive-folder-a",
        last_scan=provenance,
        items={
            f"|{reference}": ItemState(
                key=f"|{reference}",
                relative_path=f"{reference}.jpg",
                source_hash=SOURCE_HASHES[reference],
                drive_file_id=DRIVE_IDS[reference],
                drive_path=f"Архив A/{reference}.jpg",
                build_fingerprint=fingerprints[reference],
                last_build=provenance,
                status=WorkflowStatus.APPROVED.value,
            )
            for reference in SOURCE_HASHES
        },
        artifacts={
            "review.xlsx": record_sync(
                ArtifactSyncState(path="Архив A/review.xlsx"),
                "sha256:review-v1", provenance, drive_file_id="drive-review-a",
            )
        },
    )

    manifest = Manifest(
        artifacts={
            "catalog.xlsx": record_sync(
                ArtifactSyncState(path="catalog.xlsx"),
                CATALOG_HASH, provenance, drive_file_id="drive-catalog",
            )
        }
    )

    portable = PortableStateStore(root / "drive" / "_archive_state")
    portable.publish(
        PortableArchiveState(
            manifest=manifest,
            sources={ROOT.identity: source},
            catalog=export_catalog(dictionary),
        ),
        FIRST_MACHINE,
        "run-a",
        expected_generation=0,
    )
    return portable, fingerprints


def test_clean_machine_recovers_everything(tmp_path: Path) -> None:
    portable, fingerprints = _machine_a(tmp_path)

    # --- the laptop dies ---------------------------------------------------
    shutil.rmtree(tmp_path / "machine-a")
    assert not (tmp_path / "machine-a").exists()
    # Only the Drive archive survives.
    assert portable.exists

    # --- a clean machine --------------------------------------------------
    fresh = tmp_path / "machine-b"
    fresh.mkdir()
    state = StateRepository(fresh / "archive.sqlite")
    dictionary = DictionaryStore(fresh / "archive.sqlite")

    result = bootstrap(portable, state, dictionary)

    # Identities and Drive ids came back.
    assert result.generation == 1
    assert result.source_roots == 1
    assert result.items == 2
    assert result.drive_ids == 2
    assert result.built_items == 2
    assert "Tonya MacBook" in result.machines

    roots = state.list_source_roots()
    assert [root.url for root in roots] == [ROOT.url]
    assert roots[0].identity == ROOT.identity

    # Dictionary entities, aliases and evidence came back.
    restored = dictionary.load()
    assert [person.canonical_name for person in restored.people] == ["Антонина Мамаева"]
    assert restored.places[0].canonical_place == "Михнево"
    assert restored.places[0].latlon.format() == MOSCOW.format()
    assert [tag.canonical_tag for tag in restored.tags] == ["дача"]
    assert dictionary.evidence_count(EntityType.PERSON, "Антонина Мамаева") > 0

    # --- carry on: nothing should be rebuilt or re-uploaded ---------------
    loaded = portable.load()
    source = loaded.sources[ROOT.identity]

    for reference, row in zip(SOURCE_HASHES, REVIEW_ROWS):
        item = source.items[f"|{reference}"]
        assert item.drive_file_id == DRIVE_IDS[reference]
        assert not needs_rebuild(row, SOURCE_HASHES[reference], item.build_fingerprint)
        assert item.build_fingerprint == fingerprints[reference]

    # Unchanged workbooks need no transfer.
    catalog_state = loaded.manifest.artifacts["catalog.xlsx"]
    assert decide("catalog.xlsx", CATALOG_HASH, CATALOG_HASH, catalog_state).action is (
        SyncAction.NOOP
    )
    assert catalog_state.drive_file_id == "drive-catalog"
    assert catalog_state.last_sync.machine_label == "Tonya MacBook"


def test_clean_machine_creates_no_duplicate_drive_files(tmp_path: Path) -> None:
    portable, _ = _machine_a(tmp_path)
    shutil.rmtree(tmp_path / "machine-a")

    fresh = tmp_path / "machine-b"
    fresh.mkdir()
    state = StateRepository(fresh / "archive.sqlite")
    dictionary = DictionaryStore(fresh / "archive.sqlite")
    bootstrap(portable, state, dictionary)

    # The rebuilt machine publishes again from what it restored.
    loaded = portable.load()
    portable.publish(
        loaded, SECOND_MACHINE, "run-b", expected_generation=loaded.generation
    )

    republished = portable.load()
    items = republished.sources[ROOT.identity].items
    assert len(items) == 2
    assert {item.drive_file_id for item in items.values()} == set(DRIVE_IDS.values())
    # Both machines are now on record; neither replaced the other.
    assert set(republished.manifest.machines) == {"7c26e8", "91ab42"}


def test_bootstrapping_twice_is_idempotent(tmp_path: Path) -> None:
    portable, _ = _machine_a(tmp_path)
    shutil.rmtree(tmp_path / "machine-a")

    fresh = tmp_path / "machine-b"
    fresh.mkdir()
    state = StateRepository(fresh / "archive.sqlite")
    dictionary = DictionaryStore(fresh / "archive.sqlite")

    bootstrap(portable, state, dictionary)
    bootstrap(portable, state, dictionary)

    assert len(state.list_source_roots()) == 1
    restored = dictionary.load()
    assert len(restored.people) == 1
    assert len(restored.places) == 1
    assert len(restored.tags) == 1


def test_a_changed_workbook_still_uploads_after_recovery(tmp_path: Path) -> None:
    portable, _ = _machine_a(tmp_path)
    shutil.rmtree(tmp_path / "machine-a")

    catalog_state = portable.load().manifest.artifacts["catalog.xlsx"]

    # The recovered machine has an edited catalog; Drive still has the old one.
    decision = decide("catalog.xlsx", "sha256:catalog-v2", CATALOG_HASH, catalog_state)

    assert decision.action is SyncAction.UPLOAD
    assert decision.last_sync.machine_label == "Tonya MacBook"


def test_edits_on_both_machines_conflict_rather_than_overwrite(tmp_path: Path) -> None:
    portable, _ = _machine_a(tmp_path)
    catalog_state = portable.load().manifest.artifacts["catalog.xlsx"]

    decision = decide("catalog.xlsx", "sha256:local-edit", "sha256:remote-edit", catalog_state)

    assert decision.is_conflict
    assert not decision.changes_anything


def test_source_row_state_is_restored_for_incremental_rescan(tmp_path: Path) -> None:
    portable, _ = _machine_a(tmp_path)
    shutil.rmtree(tmp_path / "machine-a")

    fresh = tmp_path / "machine-b"
    fresh.mkdir()
    state = StateRepository(fresh / "archive.sqlite")
    bootstrap(portable, state, DictionaryStore(fresh / "archive.sqlite"))

    rows = state.load_row_states(ROOT.identity, "")

    # Without these the next scan would treat every photo as new.
    assert set(rows) == {"020", "021"}
    assert rows["020"].photo_hash == SOURCE_HASHES["020"]
