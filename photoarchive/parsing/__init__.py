"""Turning source text and path context into reviewable metadata proposals."""

from photoarchive.parsing.descriptions import DescriptionParser, PlainTextDescriptionParser
from photoarchive.parsing.metadata import MetadataProposer, ProposalContext

__all__ = [
    "DescriptionParser",
    "MetadataProposer",
    "PlainTextDescriptionParser",
    "ProposalContext",
]
