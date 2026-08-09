"""Archival dates, and the deterministic timestamp consumers like Google Photos need.

Two different concepts, kept apart on purpose:

``ArchiveDate``
    The truth as actually known from the family archive — a value plus its
    precision. ``1979`` never becomes ``1979-01-01``; an unknown month, day or
    time is never invented.

``CompatibilityTimestamp``
    A full timestamp derived *for consumers that require one* (Google Photos'
    ``EXIF:DateTimeOriginal`` above all). It is a display aid, never a claim
    about what is actually known, and it never overwrites or replaces the
    archival date.

This module is pure and has no dependency on ExifTool, the filesystem or any
consumer's SDK: :mod:`photoarchive.metadata` and the (not yet implemented)
build step are the only things that should ever call ExifTool, and they must
go through :func:`derive_compatibility_timestamp` rather than deriving a
fallback date themselves.

Compatibility-timestamp policy
===============================

====================  =========================================  =========
precision              EXIF:DateTimeOriginal                      synthetic
====================  =========================================  =========
DATETIME               the known time, verbatim                   False
DAY                    known date, **noon** (12:00:00)             True
MONTH                  known year/month, **day 15**, noon          True
YEAR                   known year, **July 1**, noon                True
====================  =========================================  =========

Noon, not midnight, for DAY: a classic EXIF ``DateTimeOriginal`` string carries
no timezone, so whatever a downstream consumer assumes, noon keeps the instant
far from either local midnight — the case most likely to round the date onto
the wrong calendar day.

Day 15 for MONTH, and July 1 for YEAR, are fixed conventions, not computed
midpoints (e.g. not month-length-aware) — deliberately, so the mapping never
changes shape based on which month or a leap year is involved. July 1 rather
than January 1 specifically avoids systematically pushing every
year-only-dated photo to the start of the year.

**Supported precisions, named explicitly — never by enum declaration order:**
``DATETIME``, ``DAY``, ``MONTH``, ``YEAR``. **Unsupported:** ``SEASON`` and
``UNKNOWN`` — :func:`derive_compatibility_timestamp` raises for those rather
than guessing a policy.

Unsupported precision at build time
=====================================

``derive_compatibility_timestamp`` raising for ``SEASON``/``UNKNOWN`` is a
statement about *this pure function*, not about whether a photo build may
succeed. The future build orchestrator must treat "no compatibility-timestamp
policy for this precision" as **non-fatal**:

* the archival metadata (:func:`authoritative_xmp_fields` — the archival date
  text and its precision) is still written;
* ``EXIF:DateTimeOriginal`` is simply left absent — never invented from a
  season or an unknown date;
* the photo build may still succeed;
* a warning/diagnostic is recorded (always for ``SEASON``, since a real season
  was stated and only the timestamp policy is missing; only when useful for
  ``UNKNOWN``, since there may be nothing more to say).

:func:`try_derive_compatibility_timestamp` is the non-raising shape a build
orchestrator should call: ``None`` for an unsupported precision, never an
exception the orchestrator has to catch to keep going.

``DATE_COMPATIBILITY_POLICY_VERSION`` exists so a later change to any of the
conventions above can force a rebuild: it must be folded into the build
fingerprint (see :func:`photoarchive.portable.fingerprint.build_fingerprint`)
alongside the archival date's value and precision, never left implicit.

EXIF tag group
===============

The compatibility timestamp goes in ``EXIF:DateTimeOriginal`` — always
addressed through its EXIF group. A manual Google Photos test found that a
tag written into ``IFD0`` (e.g. bare ``DateTime`` from a naive writer) is
silently ignored by Google Photos, even though many EXIF viewers still show
it; only a correctly-grouped ``ExifIFD:DateTimeOriginal`` was honoured. The
build step must write and then re-read the tag with ExifTool (or another
standards-correct tool) and check the *group*, not just that some tag by that
name exists somewhere in the file.

``EXIF:DateTimeDigitized`` is a separate fact — when the file was scanned or
digitised, not when the photograph was taken — and is written only when that
is actually known. It is **never** auto-populated from the capture-date
compatibility timestamp merely to satisfy a consumer; see
``EXIF_DATE_TIME_DIGITIZED`` below. The manual Google Photos test that
populated both ``DateTimeOriginal`` and ``DateTimeDigitized`` in its control
file did so only to exercise ingestion; that is not the production semantic
policy, and nothing here should be read as endorsing copying one into the
other by default. If real-world testing later shows Google Photos needs both
populated to ingest reliably, that must become a named, documented exception —
not a silent default.

XMP archival-date mapping
===========================

The compatibility timestamp is deliberately *not* the only surviving record of
what precision was actually known: a synthetic EXIF timestamp is
indistinguishable from a real one to a naive reader. So the archival value and
its precision are also written to XMP, in a small custom namespace kept
separate from any standard field whose semantics might differ from ours. These
three properties (:func:`authoritative_xmp_fields`) are the **authoritative**
archival record, required by the contract, and available for *every*
precision including ``SEASON``/``UNKNOWN`` (which simply have no third
property — there is no compatibility timestamp to describe as synthetic or not):

* ``XMP-archive:ArchiveDate`` — the canonical archival text, exactly as
  :meth:`ArchiveDate.text` renders it (``1979``, ``1979-05``, …).
* ``XMP-archive:ArchiveDatePrecision`` — the :class:`~photoarchive.models.DatePrecision`
  value, as its lower-case text (``"year"``, ``"month"``, ``"day"``,
  ``"datetime"``, ``"season"``, ``"unknown"``).
* ``XMP-archive:CompatibilityDateSynthetic`` — ``"True"``/``"False"``,
  present only when a compatibility timestamp was actually derived.

**Standard-field mirror — optional, experimental, not a production
requirement.** As a best-effort convenience for generic XMP-aware tools that
do not know this custom namespace, :func:`experimental_standard_date_created`
proposes the same value for the *standard* ``XMP-photoshop:DateCreated``
field, because the XMP ``Date`` data type explicitly permits
year-only/year-month/year-month-day truncation — unlike EXIF's fixed
``DateTimeOriginal`` string. This mirror is **not** authoritative and **must
not be treated as required for correctness**: a consumer that pads or
reinterprets a truncated XMP date must not be trusted over the
``XMP-archive:*`` properties above. Whether real-world tools actually honour a
truncated ``photoshop:DateCreated`` — for the ``YEAR``/``MONTH`` truncated
forms especially — rather than silently discarding or padding it, has not
been verified against a real file, an eventual real ExifTool round trip, or
Google Photos ingestion; it remains an explicit open question, tracked in the
project TODO, not something this pass resolves. A full-precision ``DAY``/
``DATETIME`` value is an ordinary, unambiguous ISO date and *may* prove safe
to mirror once that verification happens — but the authoritative
``XMP-archive:*`` properties are written regardless of what is decided about
this mirror.

Custom XMP namespace: future ExifTool round-trip contract
=============================================================

Not implemented in this pass — no ExifTool config exists yet, and none is
written here — but the acceptance contract for when it is implemented must be
explicit, so ``XMP-archive:*`` never stays merely a conceptual label:

* **Namespace** — ``XMP_ARCHIVE_NAMESPACE_URI`` /
  ``XMP_ARCHIVE_NAMESPACE_PREFIX`` below are stable and versioned. Per XMP/RDF
  convention the URI does not need to resolve to a fetchable document; it only
  has to uniquely and stably identify the schema.
* **Property names** — exactly ``ArchiveDate``, ``ArchiveDatePrecision``,
  ``CompatibilityDateSynthetic``, fixed strings, never derived dynamically.
* **Types/serialization** — all three are XMP simple (string) properties.
  ``ArchiveDate`` is :meth:`ArchiveDate.text`, verbatim. ``ArchiveDatePrecision``
  is the precision's lower-case enum value, verbatim. ``CompatibilityDateSynthetic``
  is exactly the literal string ``"True"`` or ``"False"`` (XMP Basic
  convention for a boolean), and is omitted entirely rather than written
  as some placeholder when no compatibility timestamp exists.
* **Deterministic writing** — every value is derived solely from the
  ``ArchiveDate``/``CompatibilityTimestamp`` being built; no runtime
  timestamp, machine id or run id is ever part of it, matching
  ``DATE_COMPATIBILITY_POLICY_VERSION``'s own rule.
* **Re-read validation (acceptance test)** — once ExifTool integration
  exists, the build must prove, for at least ``YEAR``, ``MONTH``, ``DAY``,
  ``DATETIME``, ``SEASON`` and ``UNKNOWN`` (where applicable to each): write
  with ExifTool, re-read with ExifTool, and every property written reproduces
  exactly — including that ``CompatibilityDateSynthetic`` is genuinely absent,
  not merely empty, for ``SEASON``/``UNKNOWN``.
* **ExifTool config dependency** — ExifTool cannot write an arbitrary
  ``-XMP-archive:ArchiveDate=...`` tag without a user-defined tag config
  (``-config`` / ``.ExifTool_config``) that declares this namespace and its
  properties first. That config does not exist yet; writing it is future
  build-implementation work, tracked in ``mds/todo.md``, not something this
  pass creates.

Timezone policy
=================

No timezone is ever invented. A classic EXIF ``DateTimeOriginal`` string
carries none; if an explicit offset field is written in the future, it must
hold a genuinely known offset, never a guessed or assumed one. The noon
convention above exists specifically to blunt timezone-boundary surprises
without pretending to know a timezone.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from photoarchive.models import DatePrecision

#: Bumped whenever the compatibility-timestamp derivation policy changes in a
#: way that would change previously-derived output (e.g. the YEAR midpoint
#: moving off July 1). Must be folded into the build fingerprint so a policy
#: change alone forces affected files to rebuild — see the module docstring.
#: Never include a runtime timestamp, machine id or run id here or near it.
DATE_COMPATIBILITY_POLICY_VERSION = 1

#: Precisions with a defined compatibility-timestamp policy, in the order the
#: module docstring's table lists them.
SUPPORTED_PRECISIONS: tuple[DatePrecision, ...] = (
    DatePrecision.YEAR,
    DatePrecision.MONTH,
    DatePrecision.DAY,
    DatePrecision.DATETIME,
)

_MONTH_MIDPOINT_DAY = 15
_YEAR_MIDPOINT_MONTH = 7
_YEAR_MIDPOINT_DAY = 1
_NOON_HOUR = 12

_YEAR_RE = re.compile(r"^(\d{4})$")
_MONTH_RE = re.compile(r"^(\d{4})-(\d{2})$")
_DAY_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
#: Seconds are optional on input; :meth:`ArchiveDate.text` always renders them,
#: so two inputs differing only by an omitted ``:00`` canonicalise identically.
_DATETIME_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2})(?::(\d{2}))?$")


class InvalidArchiveDate(ValueError):
    """Raised when archival date text cannot be parsed unambiguously.

    Raised, never guessed around: a locale-ambiguous form like ``05/06/79``
    matches none of the accepted shapes and must be rejected, not interpreted.
    """


@dataclass(frozen=True, slots=True)
class ArchiveDate:
    """A capture date known exactly as precisely as the archive knows it.

    Construct only through :meth:`parse`, so the stored components and the
    canonical text form (:meth:`text`) can never drift apart. Never carries a
    timezone — see the module docstring.
    """

    year: int
    month: int | None = None
    day: int | None = None
    hour: int | None = None
    minute: int | None = None
    second: int | None = None
    precision: DatePrecision = DatePrecision.YEAR

    def text(self) -> str:
        """The canonical partial-ISO text review.xlsx's ``Date`` column already uses.

        ``SEASON``/``UNKNOWN`` are not produced by :meth:`parse` (there is no
        accepted input shape for them yet — see the module docstring), but the
        dataclass does not forbid constructing one directly, and
        :func:`authoritative_xmp_fields` must still have *something* honest to
        write for them. ``year`` is the one field guaranteed present, so it is
        all that is rendered; a season name or any other richer representation
        is an open question this pass does not resolve.
        """
        if self.precision is DatePrecision.YEAR:
            return f"{self.year:04d}"
        if self.precision is DatePrecision.MONTH:
            return f"{self.year:04d}-{self.month:02d}"
        if self.precision is DatePrecision.DAY:
            return f"{self.year:04d}-{self.month:02d}-{self.day:02d}"
        if self.precision is DatePrecision.DATETIME:
            return (
                f"{self.year:04d}-{self.month:02d}-{self.day:02d} "
                f"{self.hour:02d}:{self.minute:02d}:{self.second:02d}"
            )
        return f"{self.year:04d}"

    @classmethod
    def parse(cls, text: str) -> ArchiveDate:
        """Parse the canonical archival text form, strictly.

        Accepts exactly ``YYYY``, ``YYYY-MM``, ``YYYY-MM-DD``,
        ``YYYY-MM-DD HH:MM`` and ``YYYY-MM-DD HH:MM:SS`` — each mapping to
        exactly one :class:`~photoarchive.models.DatePrecision`. Anything else,
        including a locale-ambiguous form such as ``05/06/79``, is rejected
        with :class:`InvalidArchiveDate` rather than guessed at.
        """
        raw = "" if text is None else str(text).strip()
        if not raw:
            raise InvalidArchiveDate("archival date text is blank")

        match = _DATETIME_RE.match(raw)
        if match:
            year, month, day, hour, minute, second = match.groups()
            return cls._validated(
                raw, DatePrecision.DATETIME,
                int(year), int(month), int(day),
                int(hour), int(minute), int(second) if second is not None else 0,
            )

        match = _DAY_RE.match(raw)
        if match:
            year, month, day = (int(part) for part in match.groups())
            return cls._validated(raw, DatePrecision.DAY, year, month, day)

        match = _MONTH_RE.match(raw)
        if match:
            year, month = (int(part) for part in match.groups())
            return cls._validated(raw, DatePrecision.MONTH, year, month)

        match = _YEAR_RE.match(raw)
        if match:
            return cls._validated(raw, DatePrecision.YEAR, int(match.group(1)))

        raise InvalidArchiveDate(
            f"{raw!r} is not a recognised archival date; expected YYYY, YYYY-MM, "
            "YYYY-MM-DD, YYYY-MM-DD HH:MM or YYYY-MM-DD HH:MM:SS"
        )

    @classmethod
    def _validated(
        cls,
        raw: str,
        precision: DatePrecision,
        year: int,
        month: int | None = None,
        day: int | None = None,
        hour: int | None = None,
        minute: int | None = None,
        second: int | None = None,
    ) -> ArchiveDate:
        try:
            datetime(year, month or 1, day or 1, hour or 0, minute or 0, second or 0)
        except ValueError as error:
            raise InvalidArchiveDate(
                f"{raw!r} is not a valid calendar date/time: {error}"
            ) from error
        return cls(
            year=year, month=month, day=day,
            hour=hour, minute=minute, second=second, precision=precision,
        )


@dataclass(frozen=True, slots=True)
class CompatibilityTimestamp:
    """A full timestamp derived for a consumer that requires one.

    ``synthetic`` is ``True`` whenever any component was invented rather than
    known — see the module docstring's policy table.
    """

    value: datetime
    synthetic: bool

    def exif_text(self) -> str:
        """The ``EXIF:DateTimeOriginal`` text form: ``YYYY:MM:DD HH:MM:SS``."""
        return self.value.strftime("%Y:%m:%d %H:%M:%S")


def derive_compatibility_timestamp(date: ArchiveDate) -> CompatibilityTimestamp:
    """Derive the deterministic compatibility timestamp for one archival date.

    Pure and total over :data:`SUPPORTED_PRECISIONS`; raises :class:`ValueError`
    for a precision with no defined policy (``SEASON``, ``UNKNOWN``) rather
    than inventing one silently.
    """
    if date.precision is DatePrecision.DATETIME:
        return CompatibilityTimestamp(
            value=datetime(
                date.year, date.month, date.day,
                date.hour, date.minute, date.second or 0,
            ),
            synthetic=False,
        )
    if date.precision is DatePrecision.DAY:
        return CompatibilityTimestamp(
            value=datetime(date.year, date.month, date.day, _NOON_HOUR, 0, 0),
            synthetic=True,
        )
    if date.precision is DatePrecision.MONTH:
        return CompatibilityTimestamp(
            value=datetime(date.year, date.month, _MONTH_MIDPOINT_DAY, _NOON_HOUR, 0, 0),
            synthetic=True,
        )
    if date.precision is DatePrecision.YEAR:
        return CompatibilityTimestamp(
            value=datetime(
                date.year, _YEAR_MIDPOINT_MONTH, _YEAR_MIDPOINT_DAY, _NOON_HOUR, 0, 0
            ),
            synthetic=True,
        )
    raise ValueError(
        f"no compatibility-timestamp policy is defined for precision {date.precision!r} "
        "(SEASON and UNKNOWN are out of scope for this contract)"
    )


def try_derive_compatibility_timestamp(date: ArchiveDate) -> CompatibilityTimestamp | None:
    """The non-fatal shape a build orchestrator should call.

    Same as :func:`derive_compatibility_timestamp` for a supported precision;
    ``None`` — never an exception — for ``SEASON``/``UNKNOWN``. A build must
    be able to proceed without a compatibility timestamp (see the module
    docstring's "Unsupported precision at build time"); only a caller that
    specifically wants "unsupported precision" to be its own error should call
    :func:`derive_compatibility_timestamp` directly instead.
    """
    if date.precision not in SUPPORTED_PRECISIONS:
        return None
    return derive_compatibility_timestamp(date)


# -- EXIF / XMP tag policy (field names only — no ExifTool invocation here) --

#: The only EXIF tag the compatibility timestamp is written to and verified
#: against. Always addressed through its EXIF group, never bare or via
#: ``IFD0:`` — see the module docstring for why that distinction is load-bearing.
EXIF_DATE_TIME_ORIGINAL = "EXIF:DateTimeOriginal"

#: When the file was actually scanned/digitised — only ever written if that is
#: genuinely known. Never auto-derived from the historical capture date, and
#: never copied from the ``EXIF:DateTimeOriginal`` compatibility timestamp —
#: the manual Google Photos test that populated both was an ingestion
#: experiment, not production policy. See the module docstring.
EXIF_DATE_TIME_DIGITIZED = "EXIF:DateTimeDigitized"

#: Stable, versioned identity for the custom XMP schema below. Does not need
#: to resolve to anything fetchable — an XMP namespace URI only has to be a
#: stable, unique identifier for the schema. See the module docstring's
#: "Custom XMP namespace: future ExifTool round-trip contract".
XMP_ARCHIVE_NAMESPACE_URI = "http://schemas.family-photo-archive.local/archive/1.0/"
XMP_ARCHIVE_NAMESPACE_PREFIX = "archive"

#: Custom namespace: the authoritative archival record, independent of
#: whatever a consumer coerced the compatibility timestamp into. Available for
#: every precision, including ``SEASON``/``UNKNOWN`` — see
#: :func:`authoritative_xmp_fields`.
XMP_ARCHIVE_DATE = "XMP-archive:ArchiveDate"
XMP_ARCHIVE_DATE_PRECISION = "XMP-archive:ArchiveDatePrecision"
XMP_COMPATIBILITY_DATE_SYNTHETIC = "XMP-archive:CompatibilityDateSynthetic"

#: Optional, experimental standard-field mirror — **not** authoritative and
#: **not** required for correctness. See the module docstring's "XMP
#: archival-date mapping" section: whether real tools honour a truncated
#: value here is an unresolved, explicitly open question, not a production
#: requirement this pass settles.
XMP_STANDARD_DATE_CREATED = "XMP-photoshop:DateCreated"


def xmp_archive_date_text(date: ArchiveDate) -> str:
    """ISO-8601 text for the XMP ``Date`` fields (``T`` separator, unlike :meth:`ArchiveDate.text`)."""
    if date.precision is DatePrecision.DATETIME:
        return (
            f"{date.year:04d}-{date.month:02d}-{date.day:02d}"
            f"T{date.hour:02d}:{date.minute:02d}:{date.second:02d}"
        )
    return date.text()  # YEAR / MONTH / DAY are already valid ISO-8601 truncations


def authoritative_xmp_fields(date: ArchiveDate) -> dict[str, str]:
    """The archival XMP properties a build must always write.

    Authoritative and required, for *every* precision — including
    ``SEASON``/``UNKNOWN``, which is exactly what makes "no compatibility
    timestamp" non-fatal at build time (see the module docstring): the
    archive's own record of what it actually knows never depends on whether a
    ``EXIF:DateTimeOriginal`` could be derived. ``XMP_COMPATIBILITY_DATE_SYNTHETIC``
    is present only when a compatibility timestamp was actually derived —
    never written as a placeholder when there is nothing to describe.
    """
    fields = {
        XMP_ARCHIVE_DATE: date.text(),
        XMP_ARCHIVE_DATE_PRECISION: date.precision.value,
    }
    timestamp = try_derive_compatibility_timestamp(date)
    if timestamp is not None:
        fields[XMP_COMPATIBILITY_DATE_SYNTHETIC] = "True" if timestamp.synthetic else "False"
    return fields


def experimental_standard_date_created(date: ArchiveDate) -> str | None:
    """The optional ``XMP-photoshop:DateCreated`` mirror — never required.

    Kept as a separate, clearly-optional entry point from
    :func:`authoritative_xmp_fields` on purpose: a caller must opt in to
    writing this experimental mirror rather than getting it "for free" as
    part of the authoritative archival record. Returns ``None`` for
    ``SEASON``/``UNKNOWN`` — a season or an unknown date has no sensible
    ISO-8601 value to propose here.
    """
    if date.precision in (DatePrecision.SEASON, DatePrecision.UNKNOWN):
        return None
    return xmp_archive_date_text(date)
