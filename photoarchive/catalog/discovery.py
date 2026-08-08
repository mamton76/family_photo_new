"""Finding and reading the review workbooks that ``learn`` learns from."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from photoarchive.review.excel import REVIEW_FILENAME, ReviewWorkbookService
from photoarchive.review.model import ReviewRow

LOG = logging.getLogger(__name__)

DEFAULT_REVIEW_DIR = Path("./review-output")


@dataclass(slots=True)
class DiscoveredWorkbook:
    """One review workbook and the rows read from it."""

    path: Path
    rows: list[ReviewRow] = field(default_factory=list)
    error: str | None = None

    @property
    def folder_label(self) -> str:
        """The archive folder this workbook belongs to."""
        return str(self.path.parent)


def discover_review_workbooks(root: Path | str = DEFAULT_REVIEW_DIR) -> list[Path]:
    """Find every ``review.xlsx`` below ``root``, in a stable order.

    Excel lock files (``~$review.xlsx``, written while a workbook is open) are
    skipped: they are not readable workbooks.
    """
    root = Path(root)
    if not root.exists():
        return []
    return sorted(
        path
        for path in root.rglob(REVIEW_FILENAME)
        if not path.name.startswith("~$")
    )


def read_review_workbooks(root: Path | str = DEFAULT_REVIEW_DIR) -> list[DiscoveredWorkbook]:
    """Read every discovered workbook, tolerating individual failures."""
    service = ReviewWorkbookService()
    results: list[DiscoveredWorkbook] = []

    for path in discover_review_workbooks(root):
        try:
            rows = list(service.read(path).values())
        except Exception as error:  # noqa: BLE001 - one bad file is not fatal
            LOG.warning("Could not read %s: %s", path, error)
            results.append(DiscoveredWorkbook(path=path, error=str(error)))
            continue
        LOG.debug("Read %s rows from %s", len(rows), path)
        results.append(DiscoveredWorkbook(path=path, rows=rows))

    return results
