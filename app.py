"""Command-line entry point for the family photo archive pipeline.

    python app.py scan "<yandex-folder-url>"
    python app.py learn
    python app.py build
    python app.py publish

Only ``scan`` is wired to a (placeholder) application flow. The other commands
fail loudly rather than pretending to have done anything.
"""

from __future__ import annotations

import argparse
import logging
import shutil
import sys
import tempfile
from pathlib import Path

from photoarchive.config import AppConfig, ConfigError
from photoarchive.models import RemoteSourceItem
from photoarchive.parsing.docx import extract_paragraphs
from photoarchive.runlog import (
    DEFAULT_LOG_DIR,
    configure_logging,
    log_summary,
    new_run_id,
)
from photoarchive.catalog.discovery import DEFAULT_REVIEW_DIR, read_review_workbooks
from photoarchive.catalog.importer import import_catalog
from photoarchive.catalog.learning import LearningContext, learn_from_rows
from photoarchive.catalog.service import CATALOG_FILENAME, CatalogService
from photoarchive.catalog.store import DictionaryStore
from photoarchive.dashboard.aggregate import collect as collect_review_rows
from photoarchive.dashboard.html import DASHBOARD_FILENAME, write_dashboard
from photoarchive.dashboard.preview import PreviewProvider
from photoarchive.merge.apply import ApplyStatus, archive_merge_workbook
from photoarchive.merge.workbook import read_conflict_workbook
from photoarchive.portable.bootstrap import bootstrap as bootstrap_state
from photoarchive.portable.catalog_state import export_catalog
from photoarchive.portable.exporter import (
    ScannedSource,
    build_portable_snapshot,
    folder_description_record,
    publish_snapshot,
    scanned_from_state,
)
from photoarchive.portable.models import STATE_DIRECTORY
from photoarchive.portable.provenance import app_commit, load_machine_identity
from photoarchive.portable.store import (
    PortableArchiveState,
    PortableStateStore,
    StateConflictError,
)
from photoarchive.scanning.local_review import generate_folder_review
from photoarchive.scanning.report import (
    build_dry_run_report,
    render_dry_run_report,
    render_learn_report,
    render_local_review_report,
)
from photoarchive.scanning.scanner import Scanner, destination_path
from photoarchive.state import StateRepository
from photoarchive.storage.base import StorageError
from photoarchive.storage.google_drive import GoogleDriveConfig, GoogleDriveStorage
from photoarchive.storage.yandex import YandexDiskConfig, YandexDiskStorage

LOG = logging.getLogger("photoarchive")

RUN_ID = new_run_id()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="app.py",
        description="Process a family photo archive from Yandex Disk into Google Drive.",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to config.yaml (defaults to ./config.yaml, then config.example.yaml).",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Enable debug logging."
    )
    parser.add_argument(
        "--log-dir",
        type=Path,
        default=DEFAULT_LOG_DIR,
        help=(
            "Where the detailed per-run log is written (default: ./logs). "
            "The file always records DEBUG detail for diagnosis."
        ),
    )

    # Accept --verbose after the subcommand too. SUPPRESS keeps the flag from
    # overwriting a --verbose given before the subcommand with its default.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Enable debug logging and per-entry output.",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    scan = subparsers.add_parser(
        "scan",
        parents=[common],
        help="Scan one Yandex Disk source folder and update review workbooks.",
    )
    scan.add_argument(
        "source_url",
        nargs="?",
        default=None,
        help=(
            "Public Yandex Disk folder URL. Omit it to scan every source "
            "listed in config.yaml."
        ),
    )
    scan.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Inspect the Yandex source and print what a scan would do. "
            "Read-only: never contacts Google Drive and writes no workbooks."
        ),
    )
    scan.add_argument(
        "--local-review",
        action="store_true",
        help=(
            "Generate review.xlsx workbooks locally, with embedded previews. "
            "Reads Yandex and writes only to --output-dir; no Google service."
        ),
    )
    scan.add_argument(
        "--output-dir",
        type=Path,
        default=Path("./review-output"),
        help="Where --local-review writes workbooks (default: ./review-output).",
    )
    scan.set_defaults(handler=command_scan)

    learn = subparsers.add_parser(
        "learn",
        parents=[common],
        help="Learn dictionaries from human-entered values in review.xlsx files.",
    )
    learn.add_argument(
        "--source",
        type=Path,
        default=DEFAULT_REVIEW_DIR,
        help="Directory searched recursively for review.xlsx (default: ./review-output).",
    )
    learn.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be learned without changing SQLite or catalog.xlsx.",
    )
    learn.set_defaults(handler=command_learn)

    dashboard = subparsers.add_parser(
        "dashboard",
        parents=[common],
        help="Generate the read-only review-all.html archive dashboard.",
    )
    dashboard.add_argument(
        "--source",
        type=Path,
        default=DEFAULT_REVIEW_DIR,
        help="Directory searched recursively for review.xlsx (default: ./review-output).",
    )
    dashboard.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output path (default: <source>/review-all.html).",
    )
    dashboard.set_defaults(handler=command_dashboard)

    run = subparsers.add_parser(
        "run",
        parents=[common],
        help="Scan every configured source, learn, and rebuild the dashboard.",
    )
    run.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Override the configured output directory.",
    )
    run.add_argument(
        "--skip-learn", action="store_true", help="Do not update the dictionaries."
    )
    run.add_argument(
        "--skip-dashboard", action="store_true", help="Do not rebuild review-all.html."
    )
    run.set_defaults(handler=command_run)

    resolve = subparsers.add_parser(
        "resolve-conflicts",
        parents=[common],
        help="Apply a merge workbook you have resolved in Excel.",
    )
    resolve.add_argument(
        "merge_workbook",
        type=Path,
        help="Path to a *.merge.xlsx generated by a conflicted sync.",
    )
    resolve.add_argument(
        "--check",
        action="store_true",
        help="Only report whether every conflict has a valid resolution.",
    )
    resolve.set_defaults(handler=command_resolve_conflicts)

    bootstrap = subparsers.add_parser(
        "bootstrap",
        parents=[common],
        help="Rebuild local state from the portable archive state (clean machine).",
    )
    bootstrap.add_argument(
        "--archive",
        type=Path,
        default=DEFAULT_REVIEW_DIR,
        help="Archive root holding _archive_state (default: ./review-output).",
    )
    bootstrap.add_argument(
        "--machine-label",
        default=None,
        help="Human label for this machine, e.g. \"Tonya MacBook\".",
    )
    bootstrap.add_argument(
        "--publish",
        action="store_true",
        help="Publish current local dictionary state as a new portable generation.",
    )
    bootstrap.set_defaults(handler=command_bootstrap)

    build = subparsers.add_parser(
        "build",
        parents=[common],
        help="Create processed copies with EXIF/IPTC/XMP metadata (not implemented).",
    )
    build.set_defaults(handler=command_build)

    publish = subparsers.add_parser(
        "publish", parents=[common], help="Publish built photos to Google Photos (not implemented)."
    )
    publish.set_defaults(handler=command_publish)

    return parser


def command_scan(args: argparse.Namespace, config: AppConfig) -> int:
    """Scan one source root, or inspect it read-only with ``--dry-run``."""
    cache_dir = config.cache.directory
    cache_dir.mkdir(parents=True, exist_ok=True)

    if args.source_url is None:
        # No URL given: process everything listed in config.yaml.
        urls = _configured_sources(config)
        if not urls:
            return 2
        for url in urls:
            print(f"\n=== scan: {url} ===")
            scoped = argparse.Namespace(**{**vars(args), "source_url": url})
            code = command_scan(scoped, config)
            if code != 0:
                return code
        return 0

    source_url = YandexDiskStorage.public_key_from_url(args.source_url)
    source = YandexDiskStorage(YandexDiskConfig(public_url=source_url), cache_dir)

    if args.local_review:
        return _run_local_review(
            source, config, output_dir=args.output_dir, verbose=args.verbose
        )

    if args.dry_run:
        return _run_dry_scan(source, config, verbose=args.verbose)

    LOG.info("Destination root folder id: %s", config.google_drive.root_folder_id)
    destination = GoogleDriveStorage(
        GoogleDriveConfig(root_folder_id=config.google_drive.root_folder_id), cache_dir
    )
    state = StateRepository()
    state.initialize()

    try:
        # Each source root gets its own dedicated folder under the Drive root,
        # so the root is resolved to a name before anything is mirrored.
        source_root = source.describe_root()
        LOG.info("Source root: %s (%s)", source_root.name, source_root.identity)
        LOG.info("Destination folder: %s", destination_path(source_root))

        scanner = Scanner(
            config=config, source=source, destination=destination, state=state
        )
        try:
            scanner.scan(source_root)
        except NotImplementedError:
            # A plain scan mirrors to Google Drive, and that transport is not
            # written yet. Say so in the reader's terms, and point at the two
            # commands that do work today.
            LOG.info("Drive transport missing; scan cannot complete", exc_info=True)
            print(
                "scan: the Google Drive transport is not wired up yet, so a "
                "full scan cannot finish.\n"
                "  scan <url> --local-review   write review.xlsx locally\n"
                "  scan <url> --dry-run        inspect the source, write nothing\n"
                "  run                         the whole local loop, every source",
                file=sys.stderr,
            )
            return 3
    finally:
        source.close()
    return 0


def _run_dry_scan(
    source: YandexDiskStorage, config: AppConfig, *, verbose: bool
) -> int:
    """Inspect the source and print the plan. Nothing outside Yandex is touched.

    No Google Drive client is constructed, no local state is written and no
    workbook is created: this reads the public source and renders a report.
    """
    cache_dir = config.cache.directory

    def load_document(item: RemoteSourceItem) -> tuple[str, ...]:
        """Fetch one DOCX into the local cache and extract its paragraphs.

        The download is a temporary local copy for parsing; the source file is
        never modified.
        """
        local_path = cache_dir / "descriptions" / item.relative_path
        source.download(item.relative_path, local_path)
        try:
            return extract_paragraphs(local_path)
        finally:
            if config.cache.cleanup:
                local_path.unlink(missing_ok=True)

    try:
        source_root = source.describe_root()
        LOG.debug("Resolved source root identity %s", source_root.identity)
        items = list(source.list_recursive())
        report = build_dry_run_report(source_root, items, load_document)
    finally:
        source.close()

    print(render_dry_run_report(report, verbose=verbose))
    return 0


def _run_local_review(
    source: YandexDiskStorage,
    config: AppConfig,
    *,
    output_dir: Path,
    verbose: bool,
) -> ScannedSource:
    """Generate local review workbooks. Reads Yandex, writes only locally."""
    cache_dir = config.cache.directory

    def load_document(item: RemoteSourceItem) -> tuple[str, ...]:
        local_path = cache_dir / "descriptions" / item.relative_path
        source.download(item.relative_path, local_path)
        try:
            return extract_paragraphs(local_path)
        finally:
            if config.cache.cleanup:
                local_path.unlink(missing_ok=True)

    def fetch_photo(item: RemoteSourceItem, destination: Path) -> Path | None:
        if destination.exists():
            return destination
        return source.download(item.relative_path, destination)

    try:
        source_root = source.describe_root()
        items = list(source.list_recursive())
        report = build_dry_run_report(source_root, items, load_document)
    finally:
        source.close()

    state = StateRepository()
    state.initialize()
    # Remember which Yandex share produced this output folder, so the dashboard
    # can link back to it and previews can be found by root identity.
    root_id = state.register_source_root(source_root)
    changes = state.record_listing(root_id, items)
    LOG.info(
        "Source listing: new %s, changed %s, missing %s, unchanged %s",
        len(changes.new), len(changes.changed), len(changes.missing),
        len(changes.unchanged),
    )
    dictionary_store = DictionaryStore()
    dictionary_store.initialize()
    dictionary = dictionary_store.load()

    results = []
    row_states: dict[str, dict] = {}
    folders: dict[str, object] = {}
    workbooks: list[Path] = []
    for folder in report.folders:
        LOG.info("Building review workbook for %r", folder.plan.folder_path or "/")
        existing_states = state.load_row_states(
            source_root.identity, folder.plan.folder_path
        )
        result = generate_folder_review(
            source_root=source_root,
            folder=folder,
            dictionary=dictionary,
            output_dir=output_dir,
            cache_dir=cache_dir,
            fetch_photo=fetch_photo,
            existing_states=existing_states,
        )
        state.save_row_states(
            source_root.identity, folder.plan.folder_path, result.states
        )
        results.append(result)
        row_states[folder.plan.folder_path] = result.states
        folders[folder.plan.folder_path] = folder_description_record(
            folder.plan, folder.error
        )
        workbooks.append(result.workbook_path)

        outcome = result.outcome
        log_summary(
            f"Folder {folder.plan.folder_path or '/'} -> {result.workbook_path}",
            {
                "rows": result.rows,
                "previews": result.previews,
                "described_absent": result.absent_rows,
                "created": len(outcome.created) if outcome else 0,
                "unchanged": len(outcome.unchanged) if outcome else 0,
                "description_changed": len(outcome.description_changed) if outcome else 0,
                "photo_changed": len(outcome.photo_changed) if outcome else 0,
                "became_present": len(outcome.became_present) if outcome else 0,
                "went_missing": len(outcome.went_missing) if outcome else 0,
                "map_links_applied": len(outcome.map_links_applied) if outcome else 0,
                "map_links_unparsed": len(outcome.map_links_unparsed) if outcome else 0,
                "suggested_dates": result.suggested_dates,
                "suggested_places": result.suggested_places,
                "suggested_people": result.suggested_people,
                "suggested_tags": result.suggested_tags,
            },
        )
        if outcome is not None:
            for row in outcome.rows:
                LOG.debug(
                    "row ref=%s file=%r status=%s reason=%r "
                    "suggested(date=%r place=%r latlon=%r people=%r tags=%r) "
                    "final(date=%r place=%r latlon=%r people=%r tags=%r)",
                    row.reference, row.filename, row.status.value, row.review_reason,
                    row.suggested_date, row.suggested_place, row.suggested_latlon,
                    row.suggested_people, row.suggested_tags,
                    row.date, row.place, row.latlon, row.people, row.tags,
                )

    catalog_path, catalog_counts = CatalogService().export(dictionary_store, output_dir)

    print(render_dry_run_report(report, verbose=verbose))
    print()
    print(render_local_review_report(results, dictionary, verbose=verbose))
    print()
    print(
        f"catalog workbook: {catalog_path}\n"
        f"catalog entries: people {catalog_counts.people}, "
        f"places {catalog_counts.places}, tags {catalog_counts.tags}, "
        f"candidate aliases {catalog_counts.candidate_aliases}, "
        f"candidate coordinates {catalog_counts.candidate_coordinates}"
    )
    # Hand the scan's own facts back, so portable state can be exported
    # without asking Yandex for the same listing a second time.
    return ScannedSource(
        source_root=source_root,
        items=items,
        row_states=row_states,
        folders=folders,
        workbooks=workbooks,
    )


def command_dashboard(args: argparse.Namespace, config: AppConfig) -> int:
    """Generate the archive-wide dashboard. Reads only; writes one HTML file."""
    output = args.output or (args.source / DASHBOARD_FILENAME)

    state = StateRepository()
    state.initialize()
    source_roots = state.list_source_roots()

    aggregate = collect_review_rows(args.source, source_roots)
    previews = PreviewProvider(config.cache.directory)
    path = write_dashboard(aggregate, output, previews, catalog_dir=args.source)

    size_mb = path.stat().st_size / (1024 * 1024)
    log_summary(
        f"Dashboard {path}",
        {
            "folders": len(aggregate.groups),
            "rows": aggregate.rows,
            "present_photos": aggregate.present_photos,
            "described_absent": aggregate.absent_photos,
            "needs_review": aggregate.needs_review,
            "size_mb": f"{size_mb:.1f}",
        },
    )

    print(f"dashboard: {path}")
    print(f"size: {size_mb:.1f} MB")
    print(
        f"folders: {len(aggregate.groups)}  rows: {aggregate.rows}  "
        f"photos: {aggregate.present_photos}  "
        f"described-absent: {aggregate.absent_photos}  "
        f"needs review: {aggregate.needs_review}"
    )
    for group in aggregate.groups:
        print(
            f"  [{group.label}] rows {len(group.rows)}  photos {group.present_photos}  "
            f"absent {group.absent_photos}  needs review {group.needs_review}"
        )
    return 0


def _configured_sources(config: AppConfig) -> list[str]:
    """URLs to process, or an empty list with an explanation printed."""
    sources = config.enabled_sources
    if not sources:
        print(
            "No sources configured. Add them under 'sources:' in config.yaml, "
            "or pass a URL: python app.py scan \"<yandex-url>\" --local-review",
            file=sys.stderr,
        )
    return [source.url for source in sources]


def command_run(args: argparse.Namespace, config: AppConfig) -> int:
    """The whole local loop in one command: scan every source, learn, publish.

    Each stage is the same code the individual commands use, so a full run and
    a hand-driven sequence produce identical results.
    """
    urls = _configured_sources(config)
    if not urls:
        return 2

    output_dir = args.output_dir or config.output_dir
    cache_dir = config.cache.directory
    cache_dir.mkdir(parents=True, exist_ok=True)

    scanned: list[ScannedSource] = []
    for url in urls:
        LOG.info("Scanning %s", url)
        print(f"\n=== scan: {url} ===")
        source = YandexDiskStorage(
            YandexDiskConfig(public_url=YandexDiskStorage.public_key_from_url(url)),
            cache_dir,
        )
        scanned.append(
            _run_local_review(source, config, output_dir=output_dir, verbose=args.verbose)
        )

    if not args.skip_learn:
        print("\n=== learn ===")
        learn_args = argparse.Namespace(
            source=output_dir, dry_run=False, verbose=args.verbose
        )
        command_learn(learn_args, config)

    # Only once every earlier stage succeeded: portable state must never
    # describe an archive that was not fully processed.
    #
    # Published *before* the dashboard, because the dashboard reads it. Source
    # description coverage — which folders have a description document, and
    # which rows that document mentions — lives only here, so rendering first
    # would report this run's archive from the previous run's observations.
    print("\n=== portable state ===")
    code = _publish_portable_state(scanned, config, output_dir, args.verbose)
    if code != 0:
        return code

    if not args.skip_dashboard:
        print("\n=== dashboard ===")
        dashboard_args = argparse.Namespace(
            source=output_dir, output=None, verbose=args.verbose
        )
        command_dashboard(dashboard_args, config)

    print(f"\n=== done: {len(scanned)} source(s) scanned ===")
    return 0


def _publish_portable_state(
    scanned: list[ScannedSource],
    config: AppConfig,
    output_dir: Path,
    verbose: bool,
) -> int:
    """Refresh the portable snapshot so a clean machine could take over."""
    machine = load_machine_identity()
    commit = app_commit()
    portable = PortableStateStore(output_dir / STATE_DIRECTORY)
    dictionary_store = DictionaryStore()
    dictionary_store.initialize()

    started = portable.read_generation()
    snapshot = build_portable_snapshot(
        scanned=scanned,
        dictionary=dictionary_store,
        previous=portable.load(),
        machine=machine,
        run_id=RUN_ID,
        commit=commit,
        catalog_workbook=output_dir / CATALOG_FILENAME,
    )

    try:
        generation, written = publish_snapshot(
            portable, snapshot, machine, RUN_ID, started, commit
        )
    except StateConflictError as error:
        print("REMOTE STATE CHANGED DURING RUN")
        print(f"  {error}")
        print("  Local work was not lost and nothing was overwritten.")
        return 5

    print(f"  location: {portable.root}")
    print(f"  machine: {machine.label}  app commit: {commit}")
    if written:
        print(f"  started generation: {started}  wrote generation: {generation}")
    else:
        print(f"  generation: {generation} (unchanged — nothing to publish)")
    print(f"  sources: {len(snapshot.sources)}")
    for source in snapshot.sources.values():
        described = sum(1 for item in source.items.values() if item.description_hash)
        absent = sum(1 for item in source.items.values() if item.was_absent)
        files = sum(
            1 for item in source.source_items.values() if not item.is_directory
        )
        print(
            f"    [{source.display_name}] source files: {files}  "
            f"row states persisted: {len(source.items)}  "
            f"described-absent: {absent}  "
            f"with description hash: {described}"
        )
    return 0


def _observation_to_item(record: dict) -> RemoteSourceItem:
    """Turn a stored source-item row back into a provider item."""
    from photoarchive.portable.provenance import parse_timestamp

    relative_path = str(record.get("relative_path", ""))
    return RemoteSourceItem(
        name=relative_path.rsplit("/", 1)[-1],
        relative_path=relative_path,
        is_directory=bool(record.get("is_directory")),
        remote_id=record.get("remote_id"),
        size=record.get("size"),
        modified_at=parse_timestamp(record.get("modified_at")),
        content_hash=record.get("content_hash"),
    )


def command_resolve_conflicts(args: argparse.Namespace, config: AppConfig) -> int:
    """Validate and apply a merge workbook a person resolved in Excel.

    Google Drive transport is not implemented yet, so this validates the
    resolution and reports what it would apply. It deliberately stops short of
    claiming a sync happened: ``last_common_hash`` and the semantic baseline
    may only advance after a real transfer.
    """
    path = args.merge_workbook
    if not path.exists():
        print(f"No such merge workbook: {path}", file=sys.stderr)
        return 2

    sheet = read_conflict_workbook(path)
    unresolved = sheet.missing()

    print(f"Merge workbook: {path}")
    for label in ("artifact path", "conflict run id", "created at (UTC)", "machine"):
        if sheet.provenance.get(label):
            print(f"  {label}: {sheet.provenance[label]}")
    print(f"  conflicts: {len(sheet.conflicts)}")
    print(f"  resolved:  {len(sheet.conflicts) - len(unresolved)}")

    if unresolved:
        print()
        print("INCOMPLETE — every conflict needs an explicit choice")
        for conflict in unresolved[:20]:
            print(f"  - {conflict.label or conflict.record_id}: {conflict.field_name}")
        if len(unresolved) > 20:
            print(f"  … and {len(unresolved) - 20} more")
        print()
        print("Set Resolution Choice to LOCAL, DRIVE, BASE or CUSTOM on every row")
        print("(CUSTOM also needs a Custom Value), save, then run this again.")
        return 6

    print()
    print("All conflicts have an explicit resolution:")
    for conflict in sheet.conflicts[:20]:
        resolution = sheet.resolutions[conflict.key]
        print(
            f"  {conflict.label or conflict.record_id}.{conflict.field_name}"
            f" -> {resolution.choice}"
            + (f" ({resolution.custom_value})" if resolution.custom_value else "")
        )

    if args.check:
        return 0

    print()
    print("Google Drive transport is not implemented yet, so the resolution was")
    print("validated but not synchronised. The canonical workbooks and the sync")
    print("baseline are unchanged; nothing was overwritten.")
    return 3


def command_bootstrap(args: argparse.Namespace, config: AppConfig) -> int:
    """Rebuild disposable local state from the portable archive state.

    Safe to run repeatedly, and safe to run on a machine that already has a
    healthy database: entities and rows are restored under their original ids.
    """
    machine = load_machine_identity(label=args.machine_label)
    commit = app_commit()
    portable = PortableStateStore(args.archive / STATE_DIRECTORY)

    state = StateRepository()
    dictionary_store = DictionaryStore()

    started = portable.read_generation()
    result = bootstrap_state(portable, state, dictionary_store)

    print(f"Run: {RUN_ID}")
    print(f"Machine: {machine.label} ({machine.machine_id[:8]})")
    print(f"App commit: {commit}")
    print()
    print("Portable state:")
    print(f"  location: {portable.root}")
    print(f"  generation: {result.generation}")
    if result.machines:
        print(f"  machines on record: {', '.join(sorted(result.machines))}")
    print()
    print("Restored:")
    print(f"  source roots: {result.source_roots}")
    print(f"  items: {result.items}  with Drive ids: {result.drive_ids}"
          f"  already built: {result.built_items}")
    counts = result.catalog_counts
    print(f"  dictionary: people {counts.get('people', 0)}, "
          f"places {counts.get('places', 0)}, tags {counts.get('tags', 0)}, "
          f"aliases {counts.get('aliases', 0)}, evidence {counts.get('evidence', 0)}")

    if not args.publish:
        if not result.restored_anything:
            print()
            print("No portable state found. Run with --publish to create the first"
                  " generation from local state.")
        return 0

    # Rebuild the snapshot from what the local database actually knows, rather
    # than republishing whatever the old generation happened to contain — which
    # is how an empty "sources": {} would otherwise persist forever.
    scanned = []
    for root in state.list_source_roots():
        with state.connect() as connection:
            row = connection.execute(
                "SELECT id FROM source_roots WHERE identity = ?", (root.identity,)
            ).fetchone()
        items = []
        if row is not None:
            items = [
                _observation_to_item(record)
                for record in state.load_source_items(int(row["id"]))
            ]
        scanned.append(scanned_from_state(state, root, items))

    if not scanned:
        print()
        print("No source roots in the local database. Run 'python app.py run' first"
              " so portable state can describe a real archive.")
        return 2

    snapshot = build_portable_snapshot(
        scanned=scanned,
        dictionary=dictionary_store,
        previous=portable.load(),
        machine=machine,
        run_id=RUN_ID,
        commit=commit,
    )

    try:
        generation, _written = publish_snapshot(
            portable, snapshot, machine, RUN_ID, started, commit
        )
    except StateConflictError as error:
        print()
        print("REMOTE STATE CHANGED DURING RUN")
        print(f"  {error}")
        print("  Local work was not lost and nothing was overwritten.")
        return 5

    print()
    print("Portable state:")
    print(f"  started generation: {started}")
    print(f"  wrote generation: {generation}")
    return 0


def command_learn(args: argparse.Namespace, config: AppConfig) -> int:
    """Learn dictionaries from the human-entered values in review workbooks.

    Order matters: catalog edits are imported *before* learning, so a person's
    curation in ``catalog.xlsx`` is never overwritten by stale SQLite, and the
    refreshed catalog is written last.
    """
    workbooks = read_review_workbooks(args.source)
    rows = [row for workbook in workbooks for row in workbook.rows]
    failed = [workbook for workbook in workbooks if workbook.error]

    LOG.info("Discovered %s review workbook(s) under %s", len(workbooks), args.source)
    for workbook in workbooks:
        LOG.debug("  %s -> %s rows%s", workbook.path, len(workbook.rows),
                  f" (ERROR: {workbook.error})" if workbook.error else "")

    if args.dry_run:
        # Learn into a throwaway copy of the real dictionary, so the proposal
        # is exactly what a real run would do, with nothing persisted.
        with tempfile.TemporaryDirectory() as scratch:
            sandbox = Path(scratch) / "dictionary.sqlite"
            if DictionaryStore().path.exists():
                shutil.copy(DictionaryStore().path, sandbox)
            store = DictionaryStore(sandbox)
            store.initialize()
            import_outcome = import_catalog(store, args.source / CATALOG_FILENAME)
            outcome = learn_from_rows(store, rows, LearningContext(run_id="dry-run"))
            counts = store.load()
        print(_render_learn(workbooks, rows, failed, import_outcome, outcome, dry_run=True))
        print(
            f"\nwould result in: people {len(counts.people)}, "
            f"places {len(counts.places)}, tags {len(counts.tags)}"
        )
        print("(dry run: nothing was written)")
        return 0

    store = DictionaryStore()
    store.initialize()

    import_outcome = import_catalog(store, args.source / CATALOG_FILENAME, run_id=RUN_ID)
    LOG.info("Imported catalog edits: %s change(s)", import_outcome.changed)

    outcome = learn_from_rows(
        store, rows, LearningContext(source_folder=str(args.source), run_id=RUN_ID)
    )

    catalog_path, catalog_counts = CatalogService().export(store, args.source)
    print(_render_learn(workbooks, rows, failed, import_outcome, outcome, dry_run=False))
    print()
    print(f"catalog workbook: {catalog_path}")
    print(
        f"catalog entries: people {catalog_counts.people}, "
        f"places {catalog_counts.places}, tags {catalog_counts.tags}, "
        f"candidate aliases {catalog_counts.candidate_aliases}, "
        f"candidate coordinates {catalog_counts.candidate_coordinates}"
    )
    return 0


def _render_learn(workbooks, rows, failed, import_outcome, outcome, *, dry_run: bool) -> str:
    contributing = len(outcome.contributing_rows)
    lines = [
        "Review workbooks" + (" (dry run)" if dry_run else ""),
        "----------------",
        f"Workbooks discovered: {len(workbooks)}",
        f"Workbooks unreadable: {len(failed)}",
        f"Rows inspected: {len(rows)}",
        f"Rows contributing human metadata: {contributing}",
        f"Rows skipped (nothing to learn / ERROR / SKIP): {len(outcome.skipped_rows)}",
        "",
        "Catalog import",
        "--------------",
        f"Entities renamed: {len(import_outcome.entities_renamed)}",
        f"Entities added: {len(import_outcome.entities_added)}",
        f"Confirmed aliases imported: {len(import_outcome.aliases_confirmed)}",
        f"Candidates promoted: {len(import_outcome.promotions)}",
        f"Aliases rejected: {len(import_outcome.aliases_rejected)}",
        f"Duplicate entities merged: {len(import_outcome.merged)}",
        f"LatLon edited: {len(import_outcome.latlon_updated)}",
        f"LatLon promoted from candidate: {len(import_outcome.latlon_promoted)}",
        f"Invalid edits rejected: {len(import_outcome.invalid)}",
        "",
        render_learn_report(outcome),
    ]
    for path in [workbook.path for workbook in workbooks]:
        LOG.debug("workbook input: %s", path)
    for problem in import_outcome.invalid:
        lines.append(f"  ! {problem}")
    # An alias in two columns at once: reported, never silently resolved.
    for collision in import_outcome.collisions:
        lines.append(f"  ! listed as both rejected and allowed, rejection kept: {collision}")
    return "\n".join(lines)


def command_build(args: argparse.Namespace, config: AppConfig) -> int:
    raise NotImplementedError(
        "'build' is not implemented yet: it will write EXIF/IPTC/XMP into processed copies."
    )


def command_publish(args: argparse.Namespace, config: AppConfig) -> int:
    raise NotImplementedError(
        "'publish' is not implemented yet: it will upload built photos to Google Photos."
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    log_path, run_id = configure_logging(
        verbose=args.verbose, log_dir=args.log_dir, run_id=RUN_ID
    )
    LOG.debug("Parsed arguments: %s", vars(args))

    try:
        config = AppConfig.load(args.config)
    except ConfigError as error:
        print(f"Configuration error: {error}", file=sys.stderr)
        return 2

    try:
        exit_code = args.handler(args, config)
        LOG.info("Run %s finished with exit code %s", run_id, exit_code)
        print(f"\ndetailed log: {log_path}")
        return exit_code
    except StorageError as error:
        LOG.exception("Storage failure")
        print(f"Storage error: {error}", file=sys.stderr)
        print(f"detailed log: {log_path}", file=sys.stderr)
        return 4
    except NotImplementedError as error:
        LOG.info("Command is not implemented: %s", error)
        print(f"Not implemented: {error}", file=sys.stderr)
        return 3
    except Exception:
        # The traceback belongs in the log file, where it can be diagnosed.
        LOG.exception("Unexpected failure during run %s", run_id)
        print(f"Unexpected error; see {log_path}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
