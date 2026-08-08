"""Proposing structured metadata for a photo.

Input signals, in roughly decreasing trustworthiness:

* the source description fragment attributed to the photo;
* existing image metadata (EXIF), when present and plausible;
* the photo filename;
* folder names along the relative path (e.g. ``1990 / Valaam``);
* archive-wide catalog knowledge (known people, places, tags).

The output is a :class:`~photoarchive.models.MetadataProposal` — a *proposal
only*. Anything derived from path context must be listed in
``inferred_fields`` so the review workbook can mark it as needing confirmation.
Nothing here is ever treated as approved without human review.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from photoarchive.models import MetadataProposal


@dataclass(frozen=True, slots=True)
class ProposalContext:
    """Everything known about one photo before a human looks at it."""

    filename: str
    source_path: str
    folder_names: tuple[str, ...] = ()
    source_description: str | None = None
    shared_description: str | None = None
    existing_metadata: dict[str, Any] = field(default_factory=dict)
    catalog: Any | None = None


class MetadataProposer(Protocol):
    """Strategy for proposing metadata from available context."""

    def propose(self, context: ProposalContext) -> MetadataProposal:
        """Return a review-required metadata proposal for one photo."""
        ...


class HeuristicMetadataProposer:
    """Baseline proposer driven by path context and description text.

    TODO: implement — recognise four-digit years and season/month words in
    folder names, resolve place and people names against the catalog, and mark
    every path-derived value in ``inferred_fields``.
    """

    def propose(self, context: ProposalContext) -> MetadataProposal:
        raise NotImplementedError("HeuristicMetadataProposer.propose is not implemented yet")
