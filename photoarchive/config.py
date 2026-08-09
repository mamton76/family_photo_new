"""Typed configuration loading.

Configuration holds deployment facts (Google Drive root folder, cache
location, workbook names); business logic must read them from here rather than
hard-coding them. The Yandex source URL is *not* configuration: it is supplied
to the CLI at runtime.

Secrets (OAuth tokens, credentials, API keys) never belong in this file or in
``config.yaml``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

DEFAULT_CONFIG_PATH = Path("config.yaml")
EXAMPLE_CONFIG_PATH = Path("config.example.yaml")


class ConfigError(RuntimeError):
    """Raised when configuration is missing or malformed."""


@dataclass(frozen=True, slots=True)
class GoogleDriveConfig:
    """Destination root under which the whole processed archive is created."""

    root_folder_id: str


@dataclass(frozen=True, slots=True)
class CacheConfig:
    """Local scratch space. Cloud storage stays the source of truth."""

    directory: Path = Path("./cache")
    cleanup: bool = True


@dataclass(frozen=True, slots=True)
class ReviewConfig:
    filename: str = "review.xlsx"
    preview_width_px: int = 180


@dataclass(frozen=True, slots=True)
class CatalogConfig:
    filename: str = "catalog.xlsx"


@dataclass(frozen=True, slots=True)
class DescriptionsConfig:
    """How description documents are applied.

    ``scope`` is ``current_folder``: a description document describes only the
    photos directly contained in its own folder, not those in subfolders.

    There is deliberately no filename-pattern setting. Discovery is
    DOCX-only and not configurable: exactly one ``.docx`` in a folder is the
    description, and every other non-photo file is reported as a diagnostic
    without being parsed.
    """

    scope: str = "current_folder"


@dataclass(frozen=True, slots=True)
class SourceEntry:
    """One Yandex Disk share the pipeline should process.

    Listing sources in configuration is what lets the whole archive be
    processed with a single command instead of one invocation per folder.

    ``label`` is a note for the person reading the file — the authoritative
    display name always comes from Yandex itself, so a label here can never
    put photos in the wrong destination folder.
    """

    url: str
    label: str | None = None
    enabled: bool = True


@dataclass(frozen=True, slots=True)
class AppConfig:
    google_drive: GoogleDriveConfig
    cache: CacheConfig = field(default_factory=CacheConfig)
    review: ReviewConfig = field(default_factory=ReviewConfig)
    catalog: CatalogConfig = field(default_factory=CatalogConfig)
    descriptions: DescriptionsConfig = field(default_factory=DescriptionsConfig)
    #: Yandex shares to process. Empty means "pass a URL on the command line".
    sources: tuple[SourceEntry, ...] = ()
    #: Where generated workbooks, the dashboard and portable state live.
    output_dir: Path = Path("./review-output")

    @property
    def enabled_sources(self) -> tuple[SourceEntry, ...]:
        return tuple(source for source in self.sources if source.enabled)

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> AppConfig:
        """Build a config from an already-parsed mapping."""
        drive = _section(data, "google_drive")
        root_folder_id = drive.get("root_folder_id")
        if not root_folder_id:
            raise ConfigError("google_drive.root_folder_id is required")

        cache = _section(data, "cache")
        review = _section(data, "review")
        catalog = _section(data, "catalog")
        descriptions = _section(data, "descriptions")

        return cls(
            google_drive=GoogleDriveConfig(root_folder_id=str(root_folder_id)),
            cache=CacheConfig(
                directory=Path(str(cache.get("directory", "./cache"))),
                cleanup=bool(cache.get("cleanup", True)),
            ),
            review=ReviewConfig(
                filename=str(review.get("filename", "review.xlsx")),
                preview_width_px=int(review.get("preview_width_px", 180)),
            ),
            catalog=CatalogConfig(filename=str(catalog.get("filename", "catalog.xlsx"))),
            descriptions=DescriptionsConfig(
                scope=str(descriptions.get("scope", "current_folder")),
            ),
            sources=_parse_sources(data.get("sources")),
            output_dir=Path(str(data.get("output_dir", "./review-output"))),
        )

    @classmethod
    def load(cls, path: Path | str | None = None) -> AppConfig:
        """Load configuration from a YAML file.

        Falls back to ``config.example.yaml`` only when no explicit path was
        given and ``config.yaml`` does not exist, so a fresh clone can be run
        without copying the example first.
        """
        candidate = Path(path) if path is not None else DEFAULT_CONFIG_PATH
        if not candidate.exists():
            if path is not None:
                raise ConfigError(f"Configuration file not found: {candidate}")
            if not EXAMPLE_CONFIG_PATH.exists():
                raise ConfigError(
                    f"No {DEFAULT_CONFIG_PATH} and no {EXAMPLE_CONFIG_PATH} found"
                )
            candidate = EXAMPLE_CONFIG_PATH

        raw = yaml.safe_load(candidate.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, dict):
            raise ConfigError(f"{candidate} must contain a YAML mapping")
        return cls.from_mapping(raw)


def _parse_sources(raw: Any) -> tuple[SourceEntry, ...]:
    """Read the ``sources`` list, accepting bare URLs or full entries.

    Both spellings work, because a one-line URL is all most entries need::

        sources:
          - "https://disk.yandex.ru/d/abc"
          - url: "https://disk.yandex.ru/d/def"
            label: "School years"
            enabled: false
    """
    if raw is None:
        return ()
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        raise ConfigError("Configuration section 'sources' must be a list")

    entries: list[SourceEntry] = []
    for index, item in enumerate(raw, start=1):
        if isinstance(item, str):
            url = item.strip()
            if url:
                entries.append(SourceEntry(url=url))
            continue
        if not isinstance(item, dict):
            raise ConfigError(f"sources[{index}] must be a URL string or a mapping")

        url = str(item.get("url", "")).strip()
        if not url:
            raise ConfigError(f"sources[{index}] has no url")
        label = item.get("label")
        entries.append(
            SourceEntry(
                url=url,
                label=str(label).strip() if label else None,
                enabled=bool(item.get("enabled", True)),
            )
        )
    return tuple(entries)


def _section(data: dict[str, Any], key: str) -> dict[str, Any]:
    value = data.get(key) or {}
    if not isinstance(value, dict):
        raise ConfigError(f"Configuration section '{key}' must be a mapping")
    return value
