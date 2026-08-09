"""Recovery through the real application flow, not a hand-built fixture.

The previous milestone proved that *given* a complete portable state, a clean
machine recovers. It did not prove that a real ``run`` ever produces one — and
it did not: `manifest.sources` was `{}` on the real archive.

So this test drives the same services ``run`` drives, against a fake Yandex
provider, and asserts that what lands in ``_archive_state`` is genuinely
complete: logical rows including `DESCRIBED_ABSENT`, per-row bookkeeping, and
catalog evidence. Then it deletes the database and shows the next scan is still
incremental.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

from PIL import Image

from photoarchive.catalog.learning import learn_from_rows
from photoarchive.catalog.models import EntityType
from photoarchive.catalog.store import DictionaryStore
from photoarchive.dashboard.aggregate import collect
from photoarchive.models import RemoteSourceItem, SourceRoot, WorkflowStatus
from photoarchive.portable.bootstrap import bootstrap
from photoarchive.portable.exporter import ScannedSource, build_portable_snapshot, publish_snapshot
from photoarchive.portable.provenance import MachineIdentity
from photoarchive.portable.store import PortableStateStore
from photoarchive.review.excel import ReviewWorkbookService
from photoarchive.scanning.local_review import generate_folder_review
from photoarchive.scanning.report import build_dry_run_report
from photoarchive.state import StateRepository

MACHINE = MachineIdentity(machine_id="7c26e8", label="Test machine")
ROOT = SourceRoot(url="https://disk.yandex.ru/d/fixture", name="Архив Тест")

PRESENT = ["020.jpg", "021.jpg"]
#: The DOCX describes two photos that are not in the folder.
ABSENT = ["022", "023"]

DOCX_NAME = "Архив Тест.docx"

PARAGRAPHS = (
    "Архив Тест",
    "020 1979. Тоня Мамаева. Дома. - нет фото",
    "021 1980. Тоня Мамаева. На даче.",
    "022 1981. Описание отсутствующего фото.",
    "023 1982. Ещё одно отсутствующее.",
)


class FakeYandex:
    """The provider surface the scan uses, backed by local fixture files."""

    def __init__(self, base: Path) -> None:
        self.base = base

    def describe_root(self) -> SourceRoot:
        return ROOT

    def list_recursive(self, relative_path: str = ""):
        for name in [*PRESENT, DOCX_NAME]:
            path = self.base / name
            yield RemoteSourceItem(
                name=name,
                relative_path=name,
                is_directory=False,
                size=path.stat().st_size,
                content_hash=f"sha256:fixture-{name}",
            )

    def download(self, relative_path: str, destination: Path) -> Path:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes((self.base / relative_path).read_bytes())
        return destination

    def close(self) -> None:
        pass


def _fixture(base: Path) -> FakeYandex:
    base.mkdir(parents=True, exist_ok=True)
    for name in PRESENT:
        Image.new("RGB", (300, 200), (90, 120, 60)).save(base / name, format="JPEG")

    namespace = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
    body = "".join(
        f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>" for text in PARAGRAPHS
    )
    document = (
        f'<?xml version="1.0"?><w:document {namespace}><w:body>{body}</w:body></w:document>'
    )
    with zipfile.ZipFile(base / DOCX_NAME, "w") as archive:
        archive.writestr("word/document.xml", document.encode())
    return FakeYandex(base)


def _run(workspace: Path, provider: FakeYandex) -> ScannedSource:
    """The same sequence ``command_run`` performs, for one source."""
    output_dir = workspace / "review-output"
    cache_dir = workspace / "cache"

    def load_document(item: RemoteSourceItem) -> tuple[str, ...]:
        from photoarchive.parsing.docx import extract_paragraphs

        local = cache_dir / "descriptions" / item.relative_path
        provider.download(item.relative_path, local)
        return extract_paragraphs(local)

    def fetch_photo(item: RemoteSourceItem, destination: Path) -> Path | None:
        if destination.exists():
            return destination
        return provider.download(item.relative_path, destination)

    source_root = provider.describe_root()
    items = list(provider.list_recursive())
    report = build_dry_run_report(source_root, items, load_document)

    state = StateRepository(workspace / "archive.sqlite")
    state.initialize()
    root_id = state.register_source_root(source_root)
    state.record_listing(root_id, items)

    dictionary = DictionaryStore(workspace / "archive.sqlite")
    dictionary.initialize()

    row_states: dict[str, dict] = {}
    workbooks: list[Path] = []
    for folder in report.folders:
        result = generate_folder_review(
            source_root=source_root,
            folder=folder,
            dictionary=dictionary.load(),
            output_dir=output_dir,
            cache_dir=cache_dir,
            fetch_photo=fetch_photo,
            existing_states=state.load_row_states(
                source_root.identity, folder.plan.folder_path
            ),
        )
        state.save_row_states(
            source_root.identity, folder.plan.folder_path, result.states
        )
        row_states[folder.plan.folder_path] = result.states
        workbooks.append(result.workbook_path)

    return ScannedSource(
        source_root=source_root, items=items, row_states=row_states, workbooks=workbooks
    )


def _publish(workspace: Path, scanned: ScannedSource) -> PortableStateStore:
    output_dir = workspace / "review-output"
    portable = PortableStateStore(output_dir / "_archive_state")
    dictionary = DictionaryStore(workspace / "archive.sqlite")

    snapshot = build_portable_snapshot(
        scanned=[scanned],
        dictionary=dictionary,
        previous=portable.load(),
        machine=MACHINE,
        run_id="run-test",
    )
    publish_snapshot(portable, snapshot, MACHINE, "run-test", portable.read_generation())
    return portable


def _teach(workspace: Path) -> None:
    """A reviewer fills in metadata, then learn stores it."""
    output_dir = workspace / "review-output"
    service = ReviewWorkbookService()
    path = next(output_dir.rglob("review.xlsx"))
    rows = list(service.read(path).values())
    for row in rows:
        row.people = "Антонина Мамаева"
        row.place = "Михнево"
    service.write(path, rows)

    learn_from_rows(DictionaryStore(workspace / "archive.sqlite"), rows)


# -- A real run produces complete portable state ---------------------------


def test_real_run_populates_portable_sources(tmp_path: Path) -> None:
    provider = _fixture(tmp_path / "source")
    scanned = _run(tmp_path, provider)
    _teach(tmp_path)
    portable = _publish(tmp_path, scanned)

    # The gap this milestone closes: sources/<id>.json must exist.
    assert portable.source_path(ROOT.identity).exists()
    manifest = json.loads(portable.manifest_path.read_text(encoding="utf-8"))
    assert manifest["sources"] == {ROOT.identity: ROOT.name}

    state = portable.load().sources[ROOT.identity]
    assert state.source_url == ROOT.url
    assert state.display_name == ROOT.name


def test_logical_rows_include_described_absent(tmp_path: Path) -> None:
    provider = _fixture(tmp_path / "source")
    scanned = _run(tmp_path, provider)
    portable = _publish(tmp_path, scanned)

    source = portable.load().sources[ROOT.identity]

    # 4 logical rows from 3 files: two photos plus two absent references.
    assert len(source.items) == 4
    absent = [item for item in source.items.values() if item.was_absent]
    assert len(absent) == 2
    assert {item.status for item in absent} == {WorkflowStatus.DESCRIBED_ABSENT.value}


def test_physical_observations_are_separate_from_logical_rows(tmp_path: Path) -> None:
    provider = _fixture(tmp_path / "source")
    scanned = _run(tmp_path, provider)
    portable = _publish(tmp_path, scanned)

    source = portable.load().sources[ROOT.identity]

    # Three files were listed; four rows exist. Both are recorded.
    assert set(source.source_items) == {*PRESENT, DOCX_NAME}
    assert len(source.items) == 4
    assert source.source_items["020.jpg"].content_hash == "sha256:fixture-020.jpg"


def test_full_row_state_is_exported(tmp_path: Path) -> None:
    provider = _fixture(tmp_path / "source")
    scanned = _run(tmp_path, provider)
    portable = _publish(tmp_path, scanned)

    source = portable.load().sources[ROOT.identity]
    present = [item for item in source.items.values() if not item.was_absent]

    assert all(item.description_hash for item in present)
    assert all(item.suggestion_hash for item in present)
    assert all(item.source_hash for item in present)


def test_catalog_evidence_is_exported(tmp_path: Path) -> None:
    provider = _fixture(tmp_path / "source")
    scanned = _run(tmp_path, provider)
    _teach(tmp_path)
    portable = _publish(tmp_path, scanned)

    catalog = portable.load().catalog

    assert [person["canonical_name"] for person in catalog["people"]] == [
        "Антонина Мамаева"
    ]
    assert catalog["evidence"]


def test_workbook_artifact_is_not_claimed_as_synced(tmp_path: Path) -> None:
    provider = _fixture(tmp_path / "source")
    scanned = _run(tmp_path, provider)
    portable = _publish(tmp_path, scanned)

    artifact = portable.load().sources[ROOT.identity].artifacts["review.xlsx"]

    # Locally generated is not the same as synchronised with Drive.
    assert artifact.local_content_hash
    assert artifact.last_common_hash is None
    assert artifact.drive_file_id is None
    assert not artifact.is_synced


def test_unchanged_rerun_does_not_bump_the_generation(tmp_path: Path) -> None:
    provider = _fixture(tmp_path / "source")
    scanned = _run(tmp_path, provider)
    portable = _publish(tmp_path, scanned)
    first = portable.read_generation()

    _publish(tmp_path, _run(tmp_path, provider))

    assert portable.read_generation() == first


# -- The acceptance test ---------------------------------------------------


def test_clean_machine_recovers_from_a_real_run(tmp_path: Path) -> None:
    provider = _fixture(tmp_path / "source")
    scanned = _run(tmp_path, provider)
    _teach(tmp_path)
    portable = _publish(tmp_path, scanned)

    # --- the database dies -------------------------------------------------
    (tmp_path / "archive.sqlite").unlink()
    assert not (tmp_path / "archive.sqlite").exists()

    state = StateRepository(tmp_path / "archive.sqlite")
    dictionary = DictionaryStore(tmp_path / "archive.sqlite")
    result = bootstrap(portable, state, dictionary)

    assert result.source_roots == 1
    assert result.items == 4
    assert [root.url for root in state.list_source_roots()] == [ROOT.url]
    assert dictionary.evidence_count(EntityType.PERSON, "Антонина Мамаева") > 0

    # --- carry on ----------------------------------------------------------
    recovered = _run(tmp_path, provider)

    for folder, rows in recovered.row_states.items():
        assert rows, f"no rows rebuilt for {folder!r}"

    output_dir = tmp_path / "review-output"
    aggregate = collect(output_dir)
    assert aggregate.rows == 4
    assert aggregate.absent_photos == 2

    # The reviewer's values survived the whole cycle untouched.
    service = ReviewWorkbookService()
    rows = list(service.read(next(output_dir.rglob("review.xlsx"))).values())
    assert all(row.people == "Антонина Мамаева" for row in rows)
    assert all(row.place == "Михнево" for row in rows)


def test_rescan_after_recovery_reports_no_changes(tmp_path: Path) -> None:
    provider = _fixture(tmp_path / "source")
    _publish(tmp_path, _run(tmp_path, provider))
    (tmp_path / "archive.sqlite").unlink()

    state = StateRepository(tmp_path / "archive.sqlite")
    bootstrap(portable_store(tmp_path), state, DictionaryStore(tmp_path / "archive.sqlite"))

    # Re-running the scan must see an unchanged archive, not a brand new one.
    outcomes = _scan_outcomes(tmp_path, provider)

    assert outcomes["created"] == 0
    assert outcomes["description_changed"] == 0
    assert outcomes["photo_changed"] == 0
    assert outcomes["became_present"] == 0
    assert outcomes["went_missing"] == 0
    assert outcomes["unchanged"] == 4


def portable_store(workspace: Path) -> PortableStateStore:
    return PortableStateStore(workspace / "review-output" / "_archive_state")


def _scan_outcomes(workspace: Path, provider: FakeYandex) -> dict[str, int]:
    """Run one more scan and total the builder's change counters."""
    from photoarchive.parsing.docx import extract_paragraphs

    output_dir = workspace / "review-output"
    cache_dir = workspace / "cache"
    state = StateRepository(workspace / "archive.sqlite")
    dictionary = DictionaryStore(workspace / "archive.sqlite")

    source_root = provider.describe_root()
    items = list(provider.list_recursive())
    report = build_dry_run_report(
        source_root,
        items,
        lambda item: extract_paragraphs(
            provider.download(item.relative_path, cache_dir / "d" / item.relative_path)
        ),
    )

    totals = {
        "created": 0, "description_changed": 0, "photo_changed": 0,
        "became_present": 0, "went_missing": 0, "unchanged": 0,
    }
    for folder in report.folders:
        result = generate_folder_review(
            source_root=source_root,
            folder=folder,
            dictionary=dictionary.load(),
            output_dir=output_dir,
            cache_dir=cache_dir,
            fetch_photo=lambda item, destination: provider.download(
                item.relative_path, destination
            ),
            existing_states=state.load_row_states(
                source_root.identity, folder.plan.folder_path
            ),
        )
        outcome = result.outcome
        totals["created"] += len(outcome.created)
        totals["description_changed"] += len(outcome.description_changed)
        totals["photo_changed"] += len(outcome.photo_changed)
        totals["became_present"] += len(outcome.became_present)
        totals["went_missing"] += len(outcome.went_missing)
        totals["unchanged"] += len(outcome.unchanged)
    return totals
