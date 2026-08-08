"""Canonical coordinates, map links and coordinate comparison.

One combined text field carries coordinates everywhere in this project::

    55.712345, 37.623456

Latitude first, longitude second, decimal degrees, ``.`` as the decimal
separator and ``, `` as the delimiter. Stored and displayed as text so that a
spreadsheet locale can never reinterpret it as two numbers or a date.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

#: Decimal places kept when formatting: ~0.1 m, far beyond archive precision.
COORDINATE_PRECISION = 6

#: Two coordinates further apart than this are treated as a genuine conflict
#: rather than a rounding difference.
CONFLICT_THRESHOLD_METRES = 250.0

_EARTH_RADIUS_M = 6_371_000.0

_NUMBER = r"[-+]?\d+(?:\.\d+)?"
_LATLON_RE = re.compile(rf"^\s*(?P<lat>{_NUMBER})\s*,\s*(?P<lon>{_NUMBER})\s*$")

#: Coordinate shapes Google Maps URLs use, most explicit first.
_MAP_LINK_PATTERNS = (
    re.compile(rf"[?&]query=(?P<lat>{_NUMBER})%2C\s*(?P<lon>{_NUMBER})"),
    re.compile(rf"[?&]query=(?P<lat>{_NUMBER})\s*,\s*(?P<lon>{_NUMBER})"),
    re.compile(rf"[?&]q=(?P<lat>{_NUMBER})\s*,\s*(?P<lon>{_NUMBER})"),
    re.compile(rf"[?&]ll=(?P<lat>{_NUMBER})\s*,\s*(?P<lon>{_NUMBER})"),
    re.compile(rf"@(?P<lat>{_NUMBER})\s*,\s*(?P<lon>{_NUMBER})"),
    re.compile(rf"!3d(?P<lat>{_NUMBER})!4d(?P<lon>{_NUMBER})"),
)


@dataclass(frozen=True, slots=True)
class LatLon:
    """A geographic point in decimal degrees."""

    latitude: float
    longitude: float

    def __str__(self) -> str:
        return self.format()

    def format(self) -> str:
        """Render in the canonical ``lat, lon`` text form."""
        return (
            f"{self.latitude:.{COORDINATE_PRECISION}f}, "
            f"{self.longitude:.{COORDINATE_PRECISION}f}"
        )

    @property
    def map_url(self) -> str:
        """A Google Maps link opening exactly this point."""
        return (
            "https://www.google.com/maps/search/?api=1&query="
            f"{self.latitude:.{COORDINATE_PRECISION}f},"
            f"{self.longitude:.{COORDINATE_PRECISION}f}"
        )


def is_valid(latitude: float, longitude: float) -> bool:
    """Report whether a pair falls inside the valid coordinate ranges."""
    return -90.0 <= latitude <= 90.0 and -180.0 <= longitude <= 180.0


def parse_latlon(text: str | None) -> LatLon | None:
    """Parse the canonical ``lat, lon`` text form.

    Returns ``None`` for anything that is not confidently a coordinate pair —
    a half-typed cell must never become a silent guess.
    """
    if not text:
        return None
    match = _LATLON_RE.match(str(text))
    if not match:
        return None

    latitude = float(match.group("lat"))
    longitude = float(match.group("lon"))
    if not is_valid(latitude, longitude):
        return None
    return LatLon(latitude=latitude, longitude=longitude)


def format_latlon(latitude: float, longitude: float) -> str:
    """Format a coordinate pair in the canonical text form."""
    return LatLon(latitude=latitude, longitude=longitude).format()


def map_url(point: LatLon) -> str:
    """Return the Google Maps URL for a point."""
    return point.map_url


def parse_map_link(url: str | None) -> LatLon | None:
    """Extract coordinates from a Google Maps URL, when unambiguous.

    Only explicit coordinate parameters are honoured. A link that merely names
    a place — ``/maps/place/Валаам`` — carries no coordinates we can trust, so
    it yields ``None`` and the caller leaves the final value untouched.
    """
    if not url:
        return None
    text = str(url).strip()
    if not text:
        return None

    for pattern in _MAP_LINK_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        latitude = float(match.group("lat"))
        longitude = float(match.group("lon"))
        if is_valid(latitude, longitude):
            return LatLon(latitude=latitude, longitude=longitude)
    return None


def distance_metres(first: LatLon, second: LatLon) -> float:
    """Great-circle distance between two points, in metres."""
    lat1, lon1 = math.radians(first.latitude), math.radians(first.longitude)
    lat2, lon2 = math.radians(second.latitude), math.radians(second.longitude)
    delta_lat, delta_lon = lat2 - lat1, lon2 - lon1

    inner = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(delta_lon / 2) ** 2
    )
    return 2 * _EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(inner)))


def is_materially_different(
    first: LatLon, second: LatLon, threshold_metres: float = CONFLICT_THRESHOLD_METRES
) -> bool:
    """Report whether two coordinates disagree beyond rounding noise."""
    return distance_metres(first, second) > threshold_metres
