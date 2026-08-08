"""Canonical coordinate formatting, parsing and map links."""

from __future__ import annotations

import pytest

from photoarchive.geo import (
    LatLon,
    distance_metres,
    format_latlon,
    is_materially_different,
    parse_latlon,
    parse_map_link,
)


def test_canonical_format_is_lat_then_lon() -> None:
    assert format_latlon(55.712345, 37.623456) == "55.712345, 37.623456"


def test_format_pads_to_six_decimals() -> None:
    assert LatLon(55.5, -37.25).format() == "55.500000, -37.250000"


def test_parse_round_trips_the_canonical_form() -> None:
    point = parse_latlon("55.712345, 37.623456")

    assert point is not None
    assert point.latitude == pytest.approx(55.712345)
    assert point.longitude == pytest.approx(37.623456)
    assert point.format() == "55.712345, 37.623456"


def test_parse_tolerates_whitespace_and_signs() -> None:
    assert parse_latlon("  -55.5 ,  +37.25 ") is not None


@pytest.mark.parametrize(
    "text", ["", None, "not coordinates", "55.7", "55.7; 37.6", "abc, def", "1,2,3"]
)
def test_parse_rejects_anything_uncertain(text) -> None:
    assert parse_latlon(text) is None


def test_parse_rejects_out_of_range_values() -> None:
    assert parse_latlon("95.0, 37.6") is None
    assert parse_latlon("55.7, 200.0") is None


def test_map_url_points_at_the_coordinate() -> None:
    url = LatLon(55.712345, 37.623456).map_url

    assert url.startswith("https://www.google.com/maps/")
    assert "55.712345,37.623456" in url


@pytest.mark.parametrize(
    "url",
    [
        "https://www.google.com/maps/search/?api=1&query=55.712345,37.623456",
        "https://maps.google.com/?q=55.712345,37.623456",
        "https://www.google.com/maps/@55.712345,37.623456,15z",
        "https://www.google.com/maps/place/X/data=!3d55.712345!4d37.623456",
        "https://www.google.com/maps/search/?api=1&query=55.712345%2C37.623456",
    ],
)
def test_map_links_with_explicit_coordinates_are_parsed(url: str) -> None:
    point = parse_map_link(url)

    assert point is not None
    assert point.format() == "55.712345, 37.623456"


@pytest.mark.parametrize(
    "url",
    [
        "",
        None,
        "https://www.google.com/maps/place/Валаам",
        "https://example.com/",
        "just some text",
    ],
)
def test_unparseable_links_yield_nothing(url) -> None:
    assert parse_map_link(url) is None


def test_distance_between_identical_points_is_zero() -> None:
    point = LatLon(55.712345, 37.623456)

    assert distance_metres(point, point) == pytest.approx(0.0, abs=1e-6)


def test_rounding_noise_is_not_a_conflict() -> None:
    first = LatLon(55.712345, 37.623456)
    second = LatLon(55.712351, 37.623460)

    assert not is_materially_different(first, second)


def test_a_different_place_is_a_conflict() -> None:
    moscow = LatLon(55.751244, 37.618423)
    saint_petersburg = LatLon(59.934280, 30.335099)

    assert is_materially_different(moscow, saint_petersburg)
