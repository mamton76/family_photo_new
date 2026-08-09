"""The generated archive-wide dashboard, ``review-all.html``.

Read-only by design: per-folder ``review.xlsx`` remains the editable review
surface and ``catalog.xlsx`` the editable dictionary. This package only reads.
"""

from photoarchive.dashboard.aggregate import Aggregate, FolderGroup, collect
from photoarchive.dashboard.html import (
    DASHBOARD_FILENAME,
    render_dashboard,
    write_dashboard,
)
from photoarchive.dashboard.links import (
    DestinationLink,
    PhotoDestinations,
    links_for,
    primary_link,
)
from photoarchive.dashboard.preview import PhotoPreview, PreviewProvider

__all__ = [
    "Aggregate",
    "DASHBOARD_FILENAME",
    "DestinationLink",
    "FolderGroup",
    "PhotoDestinations",
    "PhotoPreview",
    "PreviewProvider",
    "collect",
    "links_for",
    "primary_link",
    "render_dashboard",
    "write_dashboard",
]
