"""Storing semantic baselines beside the rest of the portable state.

One small JSON file per synced editable workbook, under
``_archive_state/artifact_baselines/``. Written atomically like everything
else in portable state, and pointed at from the artifact's
:class:`~photoarchive.portable.models.ArtifactSyncState`.

A baseline is written **only** when both sides genuinely agree — that is, after
a successful transfer or an applied merge. Writing one after a purely local
generation would claim an agreement that never happened, and the next merge
would silently drop the other machine's edits.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from photoarchive.merge.baseline import SemanticBaseline
from photoarchive.portable.models import BASELINES_DIRECTORY

LOG = logging.getLogger(__name__)


def baseline_relative_path(artifact: str, scope: str = "") -> str:
    """Where one artifact's baseline lives, relative to the state directory."""
    safe = "".join(
        "_" if character in '/\\:*?"<>|' else character for character in scope
    ).strip()
    name = f"{safe}__{artifact}.json" if safe else f"{artifact}.json"
    return f"{BASELINES_DIRECTORY}/{name}"


class BaselineStore:
    """Reads and writes semantic baselines under a portable state root."""

    def __init__(self, state_root: Path | str) -> None:
        self.root = Path(state_root)

    def path_for(self, relative: str) -> Path:
        return self.root / relative

    def load(self, relative: str | None) -> SemanticBaseline | None:
        """Return a stored baseline, or ``None`` when there is not one yet."""
        if not relative:
            return None
        path = self.path_for(relative)
        if not path.exists():
            return None
        try:
            return SemanticBaseline.from_dict(
                json.loads(path.read_text(encoding="utf-8"))
            )
        except (ValueError, OSError) as error:
            # An unreadable baseline is not a licence to guess: the caller
            # falls back to treating this as "never synchronised".
            LOG.warning("Ignoring unreadable semantic baseline %s: %s", path, error)
            return None

    def save(self, relative: str, baseline: SemanticBaseline) -> Path:
        """Write a baseline atomically and return its path."""
        path = self.path_for(relative)
        path.parent.mkdir(parents=True, exist_ok=True)

        text = json.dumps(baseline.as_dict(), indent=2, ensure_ascii=False) + "\n"
        json.loads(text)  # never publish state we cannot read back

        temporary = path.with_name(f"{path.name}.tmp")
        with temporary.open("w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        return path
