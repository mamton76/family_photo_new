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
import sys
from pathlib import Path

from photoarchive.config import AppConfig, ConfigError
from photoarchive.models import RemoteSourceItem
from photoarchive.parsing.docx import extract_paragraphs
from photoarchive.scanning.report import build_dry_run_report, render_dry_run_report
from photoarchive.scanning.scanner import Scanner, destination_path
from photoarchive.state import StateRepository
from photoarchive.storage.base import StorageError
from photoarchive.storage.google_drive import GoogleDriveConfig, GoogleDriveStorage
from photoarchive.storage.yandex import YandexDiskConfig, YandexDiskStorage

LOG = logging.getLogger("photoarchive")


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
    scan.set_defaults(handler=command_scan)

    learn = subparsers.add_parser(
        "learn", parents=[common], help="Fold approved review rows into catalog.xlsx (not implemented)."
    )
    learn.set_defaults(handler=command_learn)

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


def command_learn(args: argparse.Namespace, config: AppConfig) -> int:
    raise NotImplementedError(
        "'learn' is not implemented yet: it will populate catalog.xlsx from approved review rows."
    )


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
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )
    # httpx logs one INFO line per request, including the full URL. That buries
    # the report under noise on a large archive, so keep it for --verbose only.
    logging.getLogger("httpx").setLevel(
        logging.INFO if args.verbose else logging.WARNING
    )

    try:
        config = AppConfig.load(args.config)
    except ConfigError as error:
        print(f"Configuration error: {error}", file=sys.stderr)
        return 2

    try:
        return args.handler(args, config)
    except StorageError as error:
        print(f"Storage error: {error}", file=sys.stderr)
        return 4
    except NotImplementedError as error:
        print(f"Not implemented: {error}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
