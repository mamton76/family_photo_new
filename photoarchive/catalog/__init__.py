"""Archive-wide knowledge base: dictionaries, matching, learning, catalog.xlsx."""

from photoarchive.catalog.learning import LearningContext, LearningOutcome, learn_from_rows
from photoarchive.catalog.matching import find_matches
from photoarchive.catalog.models import (
    ConfidenceStatus,
    Dictionary,
    EntityType,
    Evidence,
    EvidenceReason,
)
from photoarchive.catalog.service import CATALOG_SHEETS, CatalogService
from photoarchive.catalog.store import DictionaryStore

__all__ = [
    "CATALOG_SHEETS",
    "CatalogService",
    "ConfidenceStatus",
    "Dictionary",
    "DictionaryStore",
    "EntityType",
    "Evidence",
    "EvidenceReason",
    "LearningContext",
    "LearningOutcome",
    "find_matches",
    "learn_from_rows",
]
