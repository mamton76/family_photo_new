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

DEFAULT_DESCRIPTION_PATTERNS: tuple[str, ...] = ("описание.txt", "description.txt")


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
    """How per-folder description files are found and applied.

    ``scope`` is ``current_folder``: a description file describes only the
    photos directly contained in its own folder, not those in subfolders.
    """

    patterns: tuple[str, ...] = DEFAULT_DESCRIPTION_PATTERNS
    scope: str = "current_folder"


@dataclass(frozen=True, slots=True)
class AppConfig:
    google_drive: GoogleDriveConfig
    cache: CacheConfig = field(default_factory=CacheConfig)
    review: ReviewConfig = field(default_factory=ReviewConfig)
    catalog: CatalogConfig = field(default_factory=CatalogConfig)
    descriptions: DescriptionsConfig = field(default_factory=DescriptionsConfig)

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

        patterns = descriptions.get("patterns") or list(DEFAULT_DESCRIPTION_PATTERNS)
        if isinstance(patterns, str):
            patterns = [patterns]

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
                patterns=tuple(str(p) for p in patterns),
                scope=str(descriptions.get("scope", "current_folder")),
            ),
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


def _section(data: dict[str, Any], key: str) -> dict[str, Any]:
    value = data.get(key) or {}
    if not isinstance(value, dict):
        raise ConfigError(f"Configuration section '{key}' must be a mapping")
    return value
