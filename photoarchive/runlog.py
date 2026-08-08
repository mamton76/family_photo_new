"""Detailed per-run logging, written to a file for later diagnosis.

The console stays readable; the log file does not. Every run writes a complete
``DEBUG``-level transcript — decisions, counts, HTTP calls, stack traces — so a
problem can be diagnosed after the fact from the file alone, including by
handing it to an assistant.

Each run gets an id and a timestamped file, so runs are never mixed up and an
earlier run's evidence is never overwritten.
"""

from __future__ import annotations

import logging
import platform
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_LOG_DIR = Path("./logs")

_FILE_FORMAT = "%(asctime)s %(levelname)-7s %(name)s %(funcName)s:%(lineno)d | %(message)s"
_CONSOLE_FORMAT = "%(levelname)s %(message)s"


def new_run_id() -> str:
    """A short id identifying one run, used in logs and in evidence rows."""
    return f"{datetime.now(tz=timezone.utc):%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:6]}"


def configure_logging(
    *, verbose: bool = False, log_dir: Path | str = DEFAULT_LOG_DIR, run_id: str | None = None
) -> tuple[Path, str]:
    """Set up console and file logging. Returns the log path and run id.

    The file always records ``DEBUG``, whatever the console verbosity, because
    the whole point is to have the details available when something went wrong
    on a run nobody was watching.
    """
    run_id = run_id or new_run_id()
    directory = Path(log_dir)
    directory.mkdir(parents=True, exist_ok=True)
    log_path = directory / f"run-{run_id}.log"

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    for handler in list(root.handlers):
        root.removeHandler(handler)

    console = logging.StreamHandler(stream=sys.stderr)
    console.setLevel(logging.DEBUG if verbose else logging.INFO)
    console.setFormatter(logging.Formatter(_CONSOLE_FORMAT))
    root.addHandler(console)

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter(_FILE_FORMAT))
    root.addHandler(file_handler)

    # httpx logs one line per request: noise on the console, useful in the file.
    logging.getLogger("httpx").setLevel(logging.DEBUG if verbose else logging.INFO)
    logging.getLogger("httpx").propagate = True
    # httpcore traces every socket operation, which buries the decisions the
    # log exists to explain. Keep it unless someone is debugging the transport.
    logging.getLogger("httpcore").setLevel(
        logging.DEBUG if verbose else logging.WARNING
    )
    for handler in root.handlers:
        if isinstance(handler, logging.StreamHandler) and not isinstance(
            handler, logging.FileHandler
        ):
            handler.addFilter(_ConsoleNoiseFilter(verbose=verbose))

    _log_environment(run_id, log_path)
    return log_path, run_id


class _ConsoleNoiseFilter(logging.Filter):
    """Keeps per-request HTTP chatter out of the console, not out of the file."""

    def __init__(self, *, verbose: bool) -> None:
        super().__init__()
        self.verbose = verbose

    def filter(self, record: logging.LogRecord) -> bool:
        if self.verbose:
            return True
        return not record.name.startswith("httpx")


def _log_environment(run_id: str, log_path: Path) -> None:
    log = logging.getLogger("photoarchive.run")
    log.info("Run %s starting; detailed log at %s", run_id, log_path)
    log.debug("Command line: %s", " ".join(sys.argv))
    log.debug("Python %s on %s", sys.version.split()[0], platform.platform())
    log.debug("Working directory: %s", Path.cwd())


def log_summary(title: str, values: dict[str, object]) -> None:
    """Record a labelled block of counters, one per line, for easy diffing."""
    log = logging.getLogger("photoarchive.run")
    log.info("%s", title)
    for key, value in values.items():
        log.info("  %s: %s", key, value)
