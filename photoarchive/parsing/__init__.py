"""Turning source documents and path context into reviewable data."""

from photoarchive.parsing.descriptions import (
    DescriptionEntry,
    ParsedDescriptionDocument,
    Reconciliation,
    parse_description_document,
    reconcile_entries,
)
from photoarchive.parsing.docx import DocxError, extract_paragraphs
from photoarchive.parsing.metadata import MetadataProposer, ProposalContext

__all__ = [
    "DescriptionEntry",
    "DocxError",
    "MetadataProposer",
    "ParsedDescriptionDocument",
    "ProposalContext",
    "Reconciliation",
    "extract_paragraphs",
    "parse_description_document",
    "reconcile_entries",
]
