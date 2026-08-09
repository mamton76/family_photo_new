"""Where a photo can be opened, as distinct from what the dashboard displays.

Two concepts are deliberately kept apart:

* the **preview** the dashboard renders — an asset this pipeline generates and
  controls (see :mod:`photoarchive.dashboard.preview`);
* the **destinations** a reader can navigate to — Yandex, Google Drive, Google
  Photos.

Provider URLs are navigation targets, never ``<img src>`` for a static page.
Google's thumbnail links expire and need live authorization; a dashboard built
on them would quietly rot into broken images. Keeping the two apart means the
page still works offline months later, while new destinations can appear as
soon as the pipeline learns them.

Destinations accumulate rather than replace one another. Publishing to Google
Photos makes it the *primary* place to look, but the Drive copy and the Yandex
original remain worth reaching, so their links stay.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

#: Ordered best-first. Google Photos wins once published because that is where
#: browsing, albums and search live; Drive is the processed archive; Yandex is
#: provenance.
DESTINATION_PRIORITY: tuple[str, ...] = ("photos", "drive", "yandex")


@dataclass(frozen=True, slots=True)
class PhotoDestinations:
    """Everywhere one photo currently exists, as far as the pipeline knows.

    Drive and Photos fields are unpopulated until those phases are built; the
    renderer simply omits links it has no data for, so they light up later
    without touching the review workbooks.
    """

    #: Public Yandex URL. ``yandex_is_folder`` records whether this addresses
    #: the containing folder or the photo itself, so the label can be honest.
    yandex_url: str | None = None
    yandex_is_folder: bool = True

    google_drive_file_id: str | None = None
    google_drive_view_url: str | None = None

    google_photos_media_id: str | None = None
    google_photos_product_url: str | None = None
    published_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class DestinationLink:
    """One rendered navigation button."""

    kind: str
    label: str
    url: str
    primary: bool = False


def links_for(destinations: PhotoDestinations) -> list[DestinationLink]:
    """Return every available destination, best-first, with one marked primary.

    A destination with no URL produces no button — the dashboard never shows a
    dead control.
    """
    links: list[DestinationLink] = []

    if destinations.google_photos_product_url:
        links.append(
            DestinationLink(
                kind="photos",
                label="Open in Google Photos",
                url=destinations.google_photos_product_url,
            )
        )

    if destinations.google_drive_view_url:
        links.append(
            DestinationLink(
                kind="drive",
                label="Open in Google Drive",
                url=destinations.google_drive_view_url,
            )
        )

    if destinations.yandex_url:
        links.append(
            DestinationLink(
                kind="yandex",
                label=(
                    "Source folder in Yandex Disk"
                    if destinations.yandex_is_folder
                    else "Source photo in Yandex Disk"
                ),
                url=destinations.yandex_url,
            )
        )

    links.sort(key=lambda link: DESTINATION_PRIORITY.index(link.kind))
    if links:
        first = links[0]
        links[0] = DestinationLink(
            kind=first.kind, label=first.label, url=first.url, primary=True
        )
    return links


def primary_link(destinations: PhotoDestinations) -> DestinationLink | None:
    """The single best place to send a reader, or ``None`` if nowhere is known."""
    links = links_for(destinations)
    return links[0] if links else None
