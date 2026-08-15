"""Turning description documents into structured, per-photo entries.

The real archive format, observed in the public source folders, is a DOCX
whose paragraphs look like this::

    Ф-ТоняМам-76-83-разное                     <- title (preamble)

    20200512_150241   1979. Тоня Мамаева (3г). Дома на Днепропетровской. - нет фото

    20200512_150442   1979 февраль 19. Тоня Мамаева (2.5г)-справа. Сережа Мамаев
    (6.5лет). Настя Платова (4г)-в центре.     <- continuation of the entry above
    Валерий Мамаев (39лет).  - нет фото        <- continuation of the entry above

    Далее Аня или Тоня ????????                <- section divider

    20200512_153447   1976 апрель. ...         <- inherits the section above

Three separate things are kept apart:

* the **photo reference** that starts an entry;
* the **description** text of that entry;
* the **section context** it sits under, which is inherited from the last
  ``Далее …`` divider and is *never* merged into the description;
* **source notes** such as ``нет фото``, which the archive owner confirmed
  means *the original paper photo has been lost* — not that the digital file
  is missing. The note is lifted out of the description and preserved
  verbatim.

No metadata is inferred here: people, places, dates, ages and tags stay as
source text. In particular, ages such as ``(2.5г)`` are never turned into
birth years.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol

from photoarchive.models import RemoteSourceItem, WorkflowStatus
from photoarchive.naming import filename_stem, split_fragments

#: Paragraphs starting with this word are section dividers, not descriptions.
#: Detection is deliberately conservative: only this explicit marker counts,
#: so an ordinary continuation paragraph is never mistaken for a heading.
SECTION_PREFIXES: tuple[str, ...] = ("далее",)

class SourceNoteKind(str, Enum):
    """What a source note *means*, which is not the same as what it says.

    Coverage reporting asks whether a photo has any real description behind it,
    and a note only counts if it carries description. ``нет фото`` states the
    condition of the source — the paper original is lost — and says nothing
    about the photograph, so it must never make a row look described.
    """

    #: A fact about the source material itself, not about the photo.
    SOURCE_STATE = "SOURCE_STATE"
    #: Genuine historical content: provenance, dating, attribution.
    DESCRIPTIVE = "DESCRIPTIVE"


#: Source notes lifted out of description text and preserved verbatim, each
#: with its meaning. A mapping rather than a list on purpose: a new pattern
#: cannot be added without deciding whether it describes the photograph, and so
#: cannot silently start counting as a description.
SOURCE_NOTE_PATTERNS: dict[str, SourceNoteKind] = {
    "нет фото": SourceNoteKind.SOURCE_STATE,
}

_SOURCE_NOTE_RE = re.compile(
    r"\s*[-–—]?\s*(?P<note>" + "|".join(SOURCE_NOTE_PATTERNS) + r")\s*",
    re.IGNORECASE,
)


def source_note_kind(note: str) -> SourceNoteKind:
    """Classify one extracted note. Unknown text is never assumed descriptive."""
    return SOURCE_NOTE_PATTERNS.get(note.strip().casefold(), SourceNoteKind.SOURCE_STATE)

#: The leading token of a paragraph — the only candidate for a reference.
_TOKEN_RE = re.compile(r"^(?P<token>[A-Za-z0-9][A-Za-z0-9_.\-]*)(?=\s|$)")

#: Trailing punctuation trimmed from a candidate token ("1979." -> "1979").
_TOKEN_TRAILING = ".,;:"

_EDGE_CHARACTERS = " \t-–—:;.,)]}([{«»\"'`"


@dataclass(frozen=True, slots=True)
class DescriptionEntry:
    """One photo's description, assembled from one or more paragraphs."""

    reference: str
    paragraphs: tuple[str, ...] = ()
    text: str = ""
    section_context: str | None = None
    #: Every occurrence, duplicates included — raw extraction is not lossy.
    source_notes: tuple[str, ...] = ()

    @property
    def display_source_notes(self) -> tuple[str, ...]:
        """Identical notes collapsed for display, original order preserved.

        One source entry repeating "нет фото" twice is one fact to a reader,
        so reporting shows it once while :attr:`source_notes` keeps both.
        """
        return deduplicate(self.source_notes)


@dataclass(frozen=True, slots=True)
class ParsedDescriptionDocument:
    """Everything one description document contained."""

    entries: tuple[DescriptionEntry, ...] = ()
    preamble: tuple[str, ...] = ()
    section_dividers: tuple[str, ...] = ()

    @property
    def source_note_count(self) -> int:
        """Raw occurrences, duplicates included."""
        return sum(len(entry.source_notes) for entry in self.entries)

    @property
    def display_source_note_count(self) -> int:
        """Occurrences as a reader would count them: one per distinct note."""
        return sum(len(entry.display_source_notes) for entry in self.entries)


@dataclass(frozen=True, slots=True)
class ReconciledEntry:
    """A description entry paired with the photo it refers to, if present."""

    entry: DescriptionEntry
    photo: RemoteSourceItem | None = None

    @property
    def present(self) -> bool:
        return self.photo is not None

    @property
    def status(self) -> WorkflowStatus:
        """``NEW`` when the photo is here, ``DESCRIBED_ABSENT`` when it is not.

        ``DESCRIBED_ABSENT`` means the document describes a photo that is not
        in *this* source folder. It is not ``SOURCE_MISSING``, which is
        reserved for a photo the pipeline saw before and that later vanished.
        """
        return WorkflowStatus.NEW if self.present else WorkflowStatus.DESCRIBED_ABSENT


@dataclass(frozen=True, slots=True)
class Reconciliation:
    """The result of matching description entries against present photos."""

    entries: tuple[ReconciledEntry, ...] = ()
    undescribed_photos: tuple[RemoteSourceItem, ...] = ()

    @property
    def present_and_described(self) -> tuple[ReconciledEntry, ...]:
        return tuple(item for item in self.entries if item.present)

    @property
    def described_but_absent(self) -> tuple[ReconciledEntry, ...]:
        return tuple(item for item in self.entries if not item.present)


def deduplicate(values: Sequence[str]) -> tuple[str, ...]:
    """Drop repeated values while preserving first-seen order."""
    seen: set[str] = set()
    unique: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            unique.append(value)
    return tuple(unique)


def is_section_divider(paragraph: str) -> bool:
    """Report whether a paragraph is a section/comment divider."""
    stripped = paragraph.strip().casefold()
    return any(stripped.startswith(prefix) for prefix in SECTION_PREFIXES)


@dataclass(frozen=True, slots=True)
class ReferenceContext:
    """What counts as a photo reference *in one particular folder*.

    Reference detection is contextual rather than one broad regex, because
    ``020`` is a photo reference in a folder of ``018.jpg``/``019.jpg`` while
    ``1979`` is just a year in a sentence. Three signals, strongest first:

    1. ``names``/``stems`` — the photos physically present. A paragraph
       opening with one of them is unambiguously a new entry.
    2. filename-shaped tokens (a digit plus a letter or underscore, e.g.
       ``IMG_0042``, ``DSC_1201``, ``20200512_150241``). These stay valid for
       photos the document describes but the folder does not contain.
    3. ``numeric_widths`` — digit counts already proven to be reference
       shapes here. A bare ``020`` is a reference only when a 3-digit
       reference style is established; ``1979`` is not, unless the folder
       genuinely uses 4-digit numeric filenames.

    When none of these fire, the paragraph is treated as continuation text.
    Missing a reference merely merges text into the previous entry; inventing
    one fabricates a photo that never existed.
    """

    names: frozenset[str] = frozenset()
    stems: frozenset[str] = frozenset()
    numeric_widths: frozenset[int] = frozenset()


def leading_token(paragraph: str) -> str | None:
    """Return the first whitespace-delimited token, minus trailing punctuation."""
    match = _TOKEN_RE.match(paragraph.strip())
    if not match:
        return None
    return match.group("token").rstrip(_TOKEN_TRAILING) or None


def known_photo_reference(token: str, context: ReferenceContext) -> bool:
    """True when the token names a photo that is present in the folder."""
    folded = token.casefold()
    return folded in context.names or folded in context.stems


def filename_like_reference(token: str) -> bool:
    """True for filename-shaped tokens: a digit plus a letter or underscore.

    Accepts ``20200512_150241``, ``IMG_0042``, ``DSC0042`` and ``020.jpg``,
    and rejects prose openers such as ``1979`` or ``Валерий``.
    """
    if not any(character.isdigit() for character in token):
        return False
    return any(character.isalpha() or character == "_" for character in token)


def numeric_reference(token: str, context: ReferenceContext) -> bool:
    """True for a bare number whose width matches an established style."""
    return token.isdigit() and len(token) in context.numeric_widths


def build_reference_context(
    photo_names: Iterable[str] = (), extra_references: Iterable[str] = ()
) -> ReferenceContext:
    """Derive the folder's reference vocabulary from its photos.

    ``extra_references`` carries references already proven strong inside the
    document itself — for instance ``020.jpg`` written in the text when no
    such photo is present — so a purely numeric style can be established from
    the document as well as from the folder.
    """
    names: set[str] = set()
    stems: set[str] = set()
    widths: set[int] = set()

    for name in photo_names:
        folded = name.casefold()
        stem = filename_stem(folded)
        names.add(folded)
        stems.add(stem)
        if stem.isdigit():
            widths.add(len(stem))

    for reference in extra_references:
        stem = filename_stem(reference.casefold())
        if stem.isdigit():
            widths.add(len(stem))

    return ReferenceContext(
        names=frozenset(names), stems=frozenset(stems), numeric_widths=frozenset(widths)
    )


def infer_reference_style(
    paragraphs: Sequence[str], photo_names: Iterable[str] = ()
) -> ReferenceContext:
    """Two-pass context building: strong references first, numeric style after.

    The first pass records only references that need no numeric evidence —
    present photos and filename-shaped tokens. Their stems then establish
    which bare-number widths are legitimate in the second pass.
    """
    base = build_reference_context(photo_names)

    strong: list[str] = []
    for paragraph in paragraphs:
        if is_section_divider(paragraph):
            continue
        token = leading_token(paragraph)
        if token and (known_photo_reference(token, base) or filename_like_reference(token)):
            strong.append(token)

    return build_reference_context(photo_names, strong)


def is_entry_start(paragraph: str, context: ReferenceContext) -> str | None:
    """Return the reference this paragraph opens with, or ``None``."""
    token = leading_token(paragraph)
    if token is None:
        return None
    if known_photo_reference(token, context):
        return token
    if filename_like_reference(token):
        return token
    if numeric_reference(token, context):
        return token
    return None


def extract_source_notes(text: str) -> tuple[str, tuple[str, ...]]:
    """Split a paragraph into clean text and the source notes it carried."""
    notes: list[str] = []

    def _collect(match: re.Match[str]) -> str:
        notes.append(match.group("note").strip().casefold())
        return " "

    cleaned = _SOURCE_NOTE_RE.sub(_collect, text)
    return cleaned.strip().strip(" \t"), tuple(notes)


def parse_description_document(
    paragraphs: Iterable[str],
    photo_names: Iterable[str] = (),
) -> ParsedDescriptionDocument:
    """Group ordered paragraphs into structured description entries.

    A new entry starts at a paragraph beginning with a photo reference, as
    judged by :func:`is_entry_start` against the folder's own reference
    vocabulary. Passing ``photo_names`` — the photos physically present —
    makes detection far more reliable, especially for purely numeric
    references like ``020``.

    Any following paragraph that is neither a reference nor a section divider
    is attached to the entry currently being built.
    """
    materialized = [raw.strip() for raw in paragraphs]
    context = infer_reference_style(materialized, photo_names)

    entries: list[DescriptionEntry] = []
    preamble: list[str] = []
    dividers: list[str] = []

    section: str | None = None
    reference: str | None = None
    collected: list[str] = []

    def flush() -> None:
        nonlocal reference, collected
        if reference is not None:
            entries.append(_build_entry(reference, collected, section))
        reference, collected = None, []

    for paragraph in materialized:
        if not paragraph:
            continue

        if is_section_divider(paragraph):
            flush()
            section = paragraph
            dividers.append(paragraph)
            continue

        found = is_entry_start(paragraph, context)
        if found is not None:
            flush()
            reference = found
            remainder = paragraph[len(found) :].strip(_EDGE_CHARACTERS)
            collected = [remainder] if remainder else []
            continue

        if reference is not None:
            collected.append(paragraph)
        else:
            preamble.append(paragraph)

    flush()
    return ParsedDescriptionDocument(
        entries=tuple(entries),
        preamble=tuple(preamble),
        section_dividers=tuple(dividers),
    )


def _build_entry(
    reference: str, paragraphs: Sequence[str], section: str | None
) -> DescriptionEntry:
    cleaned_paragraphs: list[str] = []
    notes: list[str] = []

    for paragraph in paragraphs:
        cleaned, found = extract_source_notes(paragraph)
        notes.extend(found)
        if cleaned:
            cleaned_paragraphs.append(cleaned)

    return DescriptionEntry(
        reference=reference,
        paragraphs=tuple(cleaned_paragraphs),
        text=" ".join(cleaned_paragraphs).strip(),
        section_context=section,
        source_notes=tuple(notes),
    )


def reconcile_entries(
    entries: Sequence[DescriptionEntry],
    photos: Sequence[RemoteSourceItem],
) -> Reconciliation:
    """Match description entries to the photos physically present in a folder.

    A reference matches either a full filename (``IMG_001.jpg``) or a bare
    stem (``20200512_150442`` → ``20200512_150442.jpg``). Entries with no
    matching photo are kept, not dropped: they carry the description of a
    photo that lives somewhere else or has not been uploaded yet.
    """
    by_name: dict[str, RemoteSourceItem] = {}
    by_stem: dict[str, RemoteSourceItem] = {}
    for photo in photos:
        by_name.setdefault(photo.name.casefold(), photo)
        by_stem.setdefault(filename_stem(photo.name).casefold(), photo)

    reconciled: list[ReconciledEntry] = []
    matched_paths: set[str] = set()

    for entry in entries:
        key = entry.reference.casefold()
        photo = by_name.get(key) or by_stem.get(filename_stem(key))
        if photo is not None:
            matched_paths.add(photo.relative_path)
        reconciled.append(ReconciledEntry(entry=entry, photo=photo))

    undescribed = tuple(
        photo for photo in photos if photo.relative_path not in matched_paths
    )
    return Reconciliation(entries=tuple(reconciled), undescribed_photos=undescribed)


# -- Legacy plain-text helpers -------------------------------------------
# Retained for the description-hash change detection; only DOCX is parsed
# into structured entries in this phase.


@dataclass(frozen=True, slots=True)
class ParsedDescription:
    """Result of parsing one plain-text description file."""

    raw_text: str
    fragments: tuple[str, ...] = ()

    @property
    def content_hash(self) -> str:
        return description_hash(self.raw_text)


class DescriptionParser(Protocol):
    """Strategy for turning description text into per-photo candidates."""

    def parse(self, text: str) -> ParsedDescription:
        ...


class PlainTextDescriptionParser:
    """Baseline parser: one fragment per non-empty line."""

    def parse(self, text: str) -> ParsedDescription:
        return ParsedDescription(raw_text=text, fragments=tuple(split_fragments(text)))


def description_hash(text: str) -> str:
    """Hash description text so a changed description can be surfaced.

    Line endings are normalised so that a pure CRLF/LF change is not reported
    as an edit.
    """
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()
