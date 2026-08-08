"""EXIF/IPTC/XMP writing via ExifTool (skeleton — nothing is executed yet).

Rules this module exists to enforce:

* **Original source images are never modified.** The pipeline only ever writes
  to a processed *copy* created in the local cache.
* **Processed copies receive EXIF/IPTC/XMP**: capture date and GPS in EXIF;
  caption, place, people, controlled tags, event, date precision and the
  internal archive id in IPTC/XMP.
* **ExifTool is an external executable**, not a Python dependency. It must be
  installed separately and is therefore absent from ``requirements.txt``; its
  availability has to be checked at runtime.
* Approximate dates keep their precision. A fallback date may be written to
  EXIF so viewers can sort the photo, but the original
  :class:`~photoarchive.models.DatePrecision` is always stored alongside it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from photoarchive.models import DatePrecision

EXIFTOOL_EXECUTABLE = "exiftool"


class ExifToolError(RuntimeError):
    """Raised when ExifTool is missing or fails on a file."""


@dataclass(frozen=True, slots=True)
class MetadataPayload:
    """Reviewed, approved metadata destined for one processed copy."""

    archive_id: str
    date: str | None = None
    date_precision: DatePrecision = DatePrecision.UNKNOWN
    latitude: float | None = None
    longitude: float | None = None
    place: str | None = None
    people: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    event: str | None = None
    description: str | None = None
    extra: dict[str, str] = field(default_factory=dict)


class ExifToolWriter:
    """Writes a :class:`MetadataPayload` into a processed image copy."""

    def __init__(self, executable: str = EXIFTOOL_EXECUTABLE) -> None:
        self.executable = executable

    def is_available(self) -> bool:
        """Report whether the ExifTool executable can be found.

        TODO: ``shutil.which(self.executable)``.
        """
        raise NotImplementedError("ExifToolWriter.is_available is not implemented yet")

    def build_copy(self, source: Path, destination: Path) -> Path:
        """Create the processed copy that metadata will be written into.

        The source file is opened read-only and never written back.

        TODO: copy into the cache, creating parent directories as needed.
        """
        raise NotImplementedError("ExifToolWriter.build_copy is not implemented yet")

    def write(self, processed_copy: Path, payload: MetadataPayload) -> None:
        """Write EXIF/IPTC/XMP tags into an already-created processed copy.

        TODO: build the argument list (``-EXIF:DateTimeOriginal``,
        ``-GPSLatitude``/``-GPSLongitude``, ``-IPTC:Caption-Abstract``,
        ``-XMP:PersonInImage``, ``-XMP:Subject``, ``-XMP-photoshop:City`` …)
        and invoke ExifTool with ``-overwrite_original`` on the *copy* only,
        via ``subprocess.run`` without a shell.
        """
        raise NotImplementedError("ExifToolWriter.write is not implemented yet")
