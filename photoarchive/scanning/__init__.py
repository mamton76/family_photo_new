"""Scan orchestration and cloud-free helpers."""

from photoarchive.scanning.matcher import DescriptionMatcher, match_fragments_to_photos
from photoarchive.scanning.report import (
    DryRunReport,
    FolderReport,
    build_dry_run_report,
    render_dry_run_report,
)
from photoarchive.scanning.scanner import (
    FolderScanPlan,
    Scanner,
    destination_path,
    is_description_document,
    plan_folders,
    sanitize_folder_name,
)

__all__ = [
    "DescriptionMatcher",
    "DryRunReport",
    "FolderReport",
    "FolderScanPlan",
    "Scanner",
    "build_dry_run_report",
    "destination_path",
    "is_description_document",
    "match_fragments_to_photos",
    "plan_folders",
    "render_dry_run_report",
    "sanitize_folder_name",
]
