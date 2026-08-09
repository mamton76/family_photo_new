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
        help="Public Yandex Disk folder URL, e.g. https://disk.yandex.ru/d/<id>",
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
        scanner.scan(source_root)
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
) -> int:
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
    state.register_source_root(source_root)
    dictionary_store = DictionaryStore()
    dictionary_store.initialize()
    dictionary = dictionary_store.load()

    results = []
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
    return 0


def command_dashboard(args: argparse.Namespace, config: AppConfig) -> int:
    """Generate the archive-wide dashboard. Reads only; writes one HTML file."""
    output = args.output or (args.source / DASHBOARD_FILENAME)

    state = StateRepository()
    state.initialize()
    source_roots = state.list_source_roots()

    aggregate = collect_review_rows(args.source, source_roots)
    previews = PreviewProvider(config.cache.directory)
    path = write_dashboard(aggregate, output, previews)

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
