"""Preview assets the dashboard controls, independent of any provider.

The static page embeds images this pipeline generated from locally cached
photos. That is the whole point: a dashboard whose images came from Google
thumbnail URLs would break as soon as those URLs expired or the reader was
signed out. These previews keep working offline, indefinitely.

Two sizes are produced per photo: a thumbnail for the row, and a medium
preview for the lightbox. Originals are megabytes each and are never embedded.

Assets are returned as data URIs for a self-contained file. The renderer only
ever sees :class:`PhotoPreview`, so switching to a ``review-all_files/``
directory when the archive outgrows one file is a change here, not a rewrite of
the page.
"""

from __future__ import annotations

import base64
import logging
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from photoarchive.naming import filename_stem

LOG = logging.getLogger(__name__)

THUMBNAIL_WIDTH_PX = 200
MEDIUM_WIDTH_PX = 1400

#: JPEG rather than PNG: these are photographs, and the size difference across
#: a whole archive is the difference between a usable file and an unusable one.
_THUMBNAIL_QUALITY = 78
_MEDIUM_QUALITY = 82

PHOTO_SUFFIXES = (".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp", ".bmp", ".heic")


@dataclass(frozen=True, slots=True)
class PhotoPreview:
    """Rendered preview assets for one photo."""

    thumbnail: str
    medium: str | None = None
    width: int = 0
    height: int = 0

    @property
    def has_medium(self) -> bool:
        return bool(self.medium)


class PreviewProvider:
    """Finds cached originals and renders embeddable previews from them.

    Photos are located by source-root identity plus filename stem, which is the
    same identity the scan used when caching them. Nothing here reaches the
    network.
    """

    def __init__(self, cache_dir: Path | str = Path("cache")) -> None:
        self.cache_dir = Path(cache_dir)
        self._index: dict[tuple[str, str], Path] | None = None

    def _build_index(self) -> dict[tuple[str, str], Path]:
        """Map ``(root identity, filename stem)`` to a cached original."""
        index: dict[tuple[str, str], Path] = {}
        photos_dir = self.cache_dir / "photos"
        if not photos_dir.exists():
            return index

        for identity_dir in sorted(photos_dir.iterdir()):
            if not identity_dir.is_dir():
                continue
            for path in sorted(identity_dir.rglob("*")):
                if path.is_file() and path.suffix.lower() in PHOTO_SUFFIXES:
                    index.setdefault(
                        (identity_dir.name, filename_stem(path.name).casefold()), path
                    )
        return index

    @property
    def index(self) -> dict[tuple[str, str], Path]:
        if self._index is None:
            self._index = self._build_index()
        return self._index

    def locate(self, root_identity: str, reference: str) -> Path | None:
        """Return the cached original for one row, if it was downloaded."""
        key = (root_identity, filename_stem(reference).casefold())
        found = self.index.get(key)
        if found is not None:
            return found

        # Fall back to a stem match under any root. Filenames in this archive
        # are capture timestamps, so a collision is unlikely; when one happens
        # the first root in sorted order wins, deterministically.
        stem = filename_stem(reference).casefold()
        for (identity, candidate_stem), path in sorted(self.index.items()):
            if candidate_stem == stem:
                LOG.debug(
                    "Preview for %s resolved under root %s by stem fallback",
                    reference,
                    identity,
                )
                return path
        return None

    def render(self, root_identity: str, reference: str) -> PhotoPreview | None:
        """Build thumbnail and medium previews for one photo.

        Returns ``None`` when there is no cached original — a described but
        absent photo, or one never downloaded. The renderer shows a placeholder
        rather than a broken image.
        """
        source = self.locate(root_identity, reference)
        if source is None:
            return None

        try:
            from PIL import Image, UnidentifiedImageError

            with Image.open(source) as picture:
                picture = picture.convert("RGB")
                thumbnail = _encode(picture, THUMBNAIL_WIDTH_PX, _THUMBNAIL_QUALITY)
                medium = _encode(picture, MEDIUM_WIDTH_PX, _MEDIUM_QUALITY)
                return PhotoPreview(
                    thumbnail=thumbnail,
                    medium=medium,
                    width=picture.width,
                    height=picture.height,
                )
        except (UnidentifiedImageError, OSError, ValueError) as error:
            LOG.debug("Could not render a preview for %s: %s", source.name, error)
            return None


def _encode(picture, width_px: int, quality: int) -> str:
    """Downscale and return a JPEG data URI. Never upscales."""
    ratio = min(1.0, width_px / float(picture.width))
    size = (max(1, int(picture.width * ratio)), max(1, int(picture.height * ratio)))
    resized = picture.resize(size) if ratio < 1.0 else picture

    buffer = BytesIO()
    resized.save(buffer, format="JPEG", quality=quality, optimize=True)
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"
