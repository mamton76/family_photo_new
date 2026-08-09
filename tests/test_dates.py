"""Archival date parsing and the Google-Photos-compatible timestamp derived from it.

Deliberately narrow in scope: this tests the pure contract in
``photoarchive.dates`` only. No ExifTool, no build pipeline — those do not
exist yet.
"""

from __future__ import annotations

import pytest

from photoarchive.dates import (
    DATE_COMPATIBILITY_POLICY_VERSION,
    XMP_ARCHIVE_DATE,
    XMP_ARCHIVE_DATE_PRECISION,
    XMP_COMPATIBILITY_DATE_SYNTHETIC,
    XMP_STANDARD_DATE_CREATED,
    ArchiveDate,
    InvalidArchiveDate,
    SUPPORTED_PRECISIONS,
    authoritative_xmp_fields,
    derive_compatibility_timestamp,
    experimental_standard_date_created,
    try_derive_compatibility_timestamp,
    xmp_archive_date_text,
)
from photoarchive.models import DatePrecision
from photoarchive.portable import fingerprint as fingerprint_module
from photoarchive.review.model import ReviewRow

# -- Parsing: exactly one precision per accepted shape -----------------------


def test_year_only_parses_as_year_precision() -> None:
    date = ArchiveDate.parse("1979")

    assert date.precision is DatePrecision.YEAR
    assert date.year == 1979
    assert date.month is None and date.day is None
    assert date.text() == "1979"


def test_year_month_parses_as_month_precision() -> None:
    date = ArchiveDate.parse("1979-05")

    assert date.precision is DatePrecision.MONTH
    assert (date.year, date.month) == (1979, 5)
    assert date.day is None
    assert date.text() == "1979-05"


def test_full_date_parses_as_day_precision() -> None:
    date = ArchiveDate.parse("1979-05-17")

    assert date.precision is DatePrecision.DAY
    assert (date.year, date.month, date.day) == (1979, 5, 17)
    assert date.text() == "1979-05-17"


def test_date_and_time_parses_as_datetime_precision() -> None:
    date = ArchiveDate.parse("1979-05-17 14:30")

    assert date.precision is DatePrecision.DATETIME
    assert (date.hour, date.minute, date.second) == (14, 30, 0)
    # Seconds are canonicalised even when the input omitted them.
    assert date.text() == "1979-05-17 14:30:00"


def test_date_and_time_with_seconds_parses_as_datetime_precision() -> None:
    date = ArchiveDate.parse("1979-05-17 14:30:45")

    assert date.precision is DatePrecision.DATETIME
    assert date.second == 45
    assert date.text() == "1979-05-17 14:30:45"


def test_omitted_and_explicit_zero_seconds_canonicalise_identically() -> None:
    assert ArchiveDate.parse("1979-05-17 14:30").text() == ArchiveDate.parse(
        "1979-05-17 14:30:00"
    ).text()


@pytest.mark.parametrize(
    "text", ["1979", "1979-05", "1979-05-17", "1979-05-17 14:30:45"]
)
def test_canonical_text_round_trips(text: str) -> None:
    assert ArchiveDate.parse(text).text() == text


# -- Strict rejection ----------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        "not a date",
        "05/06/79",  # locale-ambiguous — never guessed at
        "1979/05/17",
        "79-05-17",
        "1979-5-17",  # month must be zero-padded
        "1979-13",  # no month 13
        "1979-02-30",  # no Feb 30
        "1979-05-17 25:00",  # no hour 25
        "1979-05-17 14:60",  # no minute 60
        "1979-05-17T14:30",  # T separator is not an accepted archival form
    ],
)
def test_malformed_or_ambiguous_dates_are_rejected(text: str) -> None:
    with pytest.raises(InvalidArchiveDate):
        ArchiveDate.parse(text)


def test_none_is_rejected_not_treated_as_blank_precision() -> None:
    with pytest.raises(InvalidArchiveDate):
        ArchiveDate.parse(None)  # type: ignore[arg-type]


# -- Required compatibility-timestamp examples (deliverable item 13) --------


def test_year_precision_compatibility_timestamp() -> None:
    date = ArchiveDate.parse("1979")

    timestamp = derive_compatibility_timestamp(date)

    assert timestamp.exif_text() == "1979:07:01 12:00:00"
    assert timestamp.synthetic is True


def test_month_precision_compatibility_timestamp() -> None:
    date = ArchiveDate.parse("1979-05")

    timestamp = derive_compatibility_timestamp(date)

    assert timestamp.exif_text() == "1979:05:15 12:00:00"
    assert timestamp.synthetic is True


def test_day_precision_compatibility_timestamp() -> None:
    date = ArchiveDate.parse("1979-05-17")

    timestamp = derive_compatibility_timestamp(date)

    assert timestamp.exif_text() == "1979:05:17 12:00:00"
    assert timestamp.synthetic is True


def test_datetime_precision_compatibility_timestamp_is_not_synthetic() -> None:
    date = ArchiveDate.parse("1979-05-17 14:30:00")

    timestamp = derive_compatibility_timestamp(date)

    assert timestamp.exif_text() == "1979:05:17 14:30:00"
    assert timestamp.synthetic is False


# -- Policy shape: fixed conventions, not dynamic midpoints -----------------


def test_month_midpoint_is_day_15_regardless_of_month_length() -> None:
    # February (28/29 days) and July (31 days): both land on day 15.
    assert derive_compatibility_timestamp(ArchiveDate.parse("1979-02")).exif_text() == (
        "1979:02:15 12:00:00"
    )
    assert derive_compatibility_timestamp(ArchiveDate.parse("1979-07")).exif_text() == (
        "1979:07:15 12:00:00"
    )


def test_year_midpoint_is_july_first_not_january_first() -> None:
    timestamp = derive_compatibility_timestamp(ArchiveDate.parse("2000"))

    assert timestamp.exif_text().startswith("2000:07:01")


def test_day_precision_uses_noon_not_midnight() -> None:
    timestamp = derive_compatibility_timestamp(ArchiveDate.parse("1979-05-17"))

    assert timestamp.exif_text() == "1979:05:17 12:00:00"


# -- Out-of-scope precisions: no silent guess -------------------------------


@pytest.mark.parametrize("precision", [DatePrecision.SEASON, DatePrecision.UNKNOWN])
def test_precisions_without_a_defined_policy_raise(precision: DatePrecision) -> None:
    date = ArchiveDate(year=1979, precision=precision)

    with pytest.raises(ValueError, match="no compatibility-timestamp policy"):
        derive_compatibility_timestamp(date)


# -- XMP archival-date text -------------------------------------------------


def test_xmp_text_matches_archive_text_for_truncated_precisions() -> None:
    for text in ("1979", "1979-05", "1979-05-17"):
        date = ArchiveDate.parse(text)
        assert xmp_archive_date_text(date) == date.text()


def test_xmp_text_uses_t_separator_for_datetime() -> None:
    date = ArchiveDate.parse("1979-05-17 14:30:00")

    assert xmp_archive_date_text(date) == "1979-05-17T14:30:00"


# -- Supported precision set, named explicitly (deliverable item 1/7) -------


def test_supported_precisions_are_exactly_datetime_day_month_year() -> None:
    assert set(SUPPORTED_PRECISIONS) == {
        DatePrecision.DATETIME, DatePrecision.DAY, DatePrecision.MONTH, DatePrecision.YEAR,
    }
    assert DatePrecision.SEASON not in SUPPORTED_PRECISIONS
    assert DatePrecision.UNKNOWN not in SUPPORTED_PRECISIONS


# -- Non-fatal SEASON/UNKNOWN policy-level result (deliverable item 2) ------


@pytest.mark.parametrize("precision", [DatePrecision.SEASON, DatePrecision.UNKNOWN])
def test_try_derive_returns_none_instead_of_raising(precision: DatePrecision) -> None:
    date = ArchiveDate(year=1979, precision=precision)

    assert try_derive_compatibility_timestamp(date) is None


@pytest.mark.parametrize(
    "text", ["1979", "1979-05", "1979-05-17", "1979-05-17 14:30:00"]
)
def test_try_derive_matches_derive_for_supported_precisions(text: str) -> None:
    date = ArchiveDate.parse(text)

    assert try_derive_compatibility_timestamp(date) == derive_compatibility_timestamp(date)


@pytest.mark.parametrize("precision", [DatePrecision.SEASON, DatePrecision.UNKNOWN])
def test_text_renders_only_the_year_for_precisions_without_a_richer_shape(
    precision: DatePrecision,
) -> None:
    # No month/day/time field is populated for SEASON/UNKNOWN, so .text() must
    # not crash reaching for one — year is the only thing guaranteed known.
    assert ArchiveDate(year=1979, precision=precision).text() == "1979"


def test_authoritative_xmp_fields_available_for_season_and_unknown() -> None:
    # A missing compatibility timestamp must never mean a missing archival
    # record: the raw value and precision are always there to write.
    season = ArchiveDate(year=1979, precision=DatePrecision.SEASON)
    unknown = ArchiveDate(year=1979, precision=DatePrecision.UNKNOWN)

    for date in (season, unknown):
        fields = authoritative_xmp_fields(date)
        assert fields[XMP_ARCHIVE_DATE] == date.text()
        assert fields[XMP_ARCHIVE_DATE_PRECISION] == date.precision.value
        # No compatibility timestamp exists, so nothing is claimed about it —
        # not even a placeholder "unknown" value.
        assert XMP_COMPATIBILITY_DATE_SYNTHETIC not in fields


def test_authoritative_xmp_fields_include_synthetic_flag_when_derivable() -> None:
    synthetic = authoritative_xmp_fields(ArchiveDate.parse("1979"))
    real = authoritative_xmp_fields(ArchiveDate.parse("1979-05-17 14:30:00"))

    assert synthetic[XMP_COMPATIBILITY_DATE_SYNTHETIC] == "True"
    assert real[XMP_COMPATIBILITY_DATE_SYNTHETIC] == "False"


# -- Standard DateCreated mirror stays optional (deliverable item 3) --------


@pytest.mark.parametrize("precision", [DatePrecision.SEASON, DatePrecision.UNKNOWN])
def test_experimental_mirror_returns_none_for_season_and_unknown(
    precision: DatePrecision,
) -> None:
    date = ArchiveDate(year=1979, precision=precision)

    assert experimental_standard_date_created(date) is None


def test_experimental_mirror_matches_xmp_text_for_supported_precisions() -> None:
    for text in ("1979", "1979-05", "1979-05-17", "1979-05-17 14:30:00"):
        date = ArchiveDate.parse(text)
        assert experimental_standard_date_created(date) == xmp_archive_date_text(date)


def test_experimental_mirror_is_not_bundled_into_the_authoritative_fields() -> None:
    # Optional means opt-in: it must not silently ride along with the fields
    # a build is required to write.
    fields = authoritative_xmp_fields(ArchiveDate.parse("1979"))

    assert XMP_STANDARD_DATE_CREATED not in fields


# -- Build fingerprint sensitivity to the policy version --------------------


def test_fingerprint_module_reads_the_current_policy_version() -> None:
    assert fingerprint_module.DATE_COMPATIBILITY_POLICY_VERSION == (
        DATE_COMPATIBILITY_POLICY_VERSION
    )


def test_changing_the_date_policy_version_alone_changes_the_fingerprint(monkeypatch) -> None:
    row = ReviewRow(reference="020", date="1979")

    before = fingerprint_module.build_fingerprint(row, "sha256:src")
    monkeypatch.setattr(fingerprint_module, "DATE_COMPATIBILITY_POLICY_VERSION", 999)
    after = fingerprint_module.build_fingerprint(row, "sha256:src")

    assert before != after
