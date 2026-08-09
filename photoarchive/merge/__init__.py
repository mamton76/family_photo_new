"""Semantic three-way merge for the workbooks people edit.

The file-level rule decides *whether* a merge is needed; this package decides
*what* it means. Two people editing different columns of the same row merge
automatically, and only a genuine disagreement about one value reaches a human
— in Excel, not in JSON or a Git merge tool.

Canonical ``review.xlsx`` and ``catalog.xlsx`` stay untouched until a merge is
either automatic or explicitly resolved.
"""

from photoarchive.merge.apply import (
    ApplyOutcome,
    ApplyStatus,
    archive_merge_workbook,
    resolve,
    resolve_from_workbook,
)
from photoarchive.merge.baseline import (
    ARTIFACT_CATALOG,
    ARTIFACT_REVIEW,
    BASELINE_SCHEMA_VERSION,
    CATALOG_HUMAN_FIELDS,
    REVIEW_HUMAN_FIELDS,
    REVIEW_MACHINE_FIELDS,
    SemanticBaseline,
    SemanticRecord,
    baseline_from_catalog,
    baseline_from_review_rows,
)
from photoarchive.merge.semantic import (
    normalize_for_comparison,
    semantic_baselines_equal,
    semantic_equal,
)
from photoarchive.merge.threeway import (
    ConflictKind,
    FieldConflict,
    MergeResult,
    merge,
    merge_first_sync,
)
from photoarchive.merge.workbook import (
    CONFLICT_DIRECTORY,
    NO_COMMON_BASELINE,
    RESOLUTIONS,
    RESOLUTIONS_FIRST_SYNC,
    ConflictProvenance,
    Resolution,
    ResolutionSheet,
    conflict_workbook_path,
    read_conflict_workbook,
    write_conflict_workbook,
)

__all__ = [
    "ARTIFACT_CATALOG",
    "ARTIFACT_REVIEW",
    "ApplyOutcome",
    "ApplyStatus",
    "BASELINE_SCHEMA_VERSION",
    "CATALOG_HUMAN_FIELDS",
    "CONFLICT_DIRECTORY",
    "ConflictKind",
    "ConflictProvenance",
    "FieldConflict",
    "MergeResult",
    "NO_COMMON_BASELINE",
    "REVIEW_HUMAN_FIELDS",
    "REVIEW_MACHINE_FIELDS",
    "RESOLUTIONS",
    "RESOLUTIONS_FIRST_SYNC",
    "Resolution",
    "ResolutionSheet",
    "SemanticBaseline",
    "SemanticRecord",
    "archive_merge_workbook",
    "baseline_from_catalog",
    "baseline_from_review_rows",
    "conflict_workbook_path",
    "merge",
    "merge_first_sync",
    "normalize_for_comparison",
    "read_conflict_workbook",
    "resolve",
    "resolve_from_workbook",
    "semantic_baselines_equal",
    "semantic_equal",
    "write_conflict_workbook",
]
