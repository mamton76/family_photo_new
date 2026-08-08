"""Learning dictionary knowledge from reviewed rows.

The rule that governs this whole module:

    **Suggestions do not teach the dictionary. Human-entered final metadata
    does.**

Only user-owned columns are read. A machine suggestion that nobody confirmed
teaches nothing, which keeps the system from believing its own guesses.

``APPROVED`` is deliberately *not* the gate. It is a publication state, and a
reviewer who typed a name into ``People`` on a row still marked ``REVIEW`` has
stated a fact just as firmly. Only ``ERROR`` and ``SKIP`` rows are ignored.
This lets the dictionary bootstrap while review is still in progress, which is
the whole point of the review → learn → rescan loop.

Promotion is equally cautious. A final value the source text does not contain
is a new canonical entity. A final value that clearly replaced one specific
phrase in the source becomes a **confirmed** alias. Anything ambiguous — two
candidate phrases, or a kinship word like ``мама`` whose meaning depends on who
is speaking — becomes a **candidate** with evidence attached, for a human to
settle.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from photoarchive.catalog.matching import find_matches, normalize
from photoarchive.catalog.models import (
    ConfidenceStatus,
    Dictionary,
    EntityType,
    Evidence,
    EvidenceReason,
)
from photoarchive.catalog.store import DictionaryStore
from photoarchive.geo import parse_latlon
from photoarchive.models import WorkflowStatus
from photoarchive.review.model import ReviewRow, split_list_field

#: Kinship words are relative to the speaker: "мама" names a different person
#: in every family album. They never become universal aliases automatically.
CONTEXT_DEPENDENT_TERMS: frozenset[str] = frozenset(
    {
        "мама", "мамы", "маме", "маму",
        "папа", "папы", "папе", "папу",
        "бабушка", "бабушки", "бабушке",
        "дедушка", "дедушки", "дедушке",
        "сестра", "брат", "тетя", "тётя", "дядя",
        "дочь", "сын", "внук", "внучка",
    }
)


@dataclass(slots=True)
class LearningOutcome:
    """What a learn pass changed, for reporting."""

    new_people: list[str] = field(default_factory=list)
    new_places: list[str] = field(default_factory=list)
    new_tags: list[str] = field(default_factory=list)
    confirmed_aliases: list[str] = field(default_factory=list)
    candidate_aliases: list[str] = field(default_factory=list)
    promoted: list[str] = field(default_factory=list)
    contributing_rows: list[str] = field(default_factory=list)
    skipped_rows: list[str] = field(default_factory=list)
    latlon_added: list[str] = field(default_factory=list)
    latlon_conflicts: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class LearningContext:
    """Where an approved row came from, recorded as provenance."""

    source_root: str | None = None
    source_folder: str | None = None
    run_id: str | None = None


#: Rows in these states are not trusted as teaching material. Everything else
#: counts: ``APPROVED`` is a publication state, not a precondition for
#: learning, and a reviewer filling in People on a ``REVIEW`` row has stated a
#: fact just as firmly.
NON_TEACHING_STATUSES = frozenset({WorkflowStatus.ERROR, WorkflowStatus.SKIP})


def is_teaching_row(row: ReviewRow) -> bool:
    """Report whether a row's human-owned fields may teach the dictionary."""
    if row.status in NON_TEACHING_STATUSES:
        return False
    return bool(
        (row.people or "").strip()
        or (row.place or "").strip()
        or (row.tags or "").strip()
        or (row.latlon or "").strip()
    )


def learn_from_rows(
    store: DictionaryStore,
    rows: list[ReviewRow],
    context: LearningContext | None = None,
) -> LearningOutcome:
    """Fold human-entered final values into the dictionary, with evidence.

    Only user-owned columns are read — ``People``, ``Place``, ``LatLon`` and
    ``Tags``. ``Suggested …`` values are ignored entirely, so the dictionary
    can never be taught by the machine's own guesses.

    Safe to run repeatedly: entities, aliases and evidence are all keyed, so an
    unchanged rerun adds nothing.
    """
    context = context or LearningContext()
    outcome = LearningOutcome()

    for row in rows:
        if not is_teaching_row(row):
            outcome.skipped_rows.append(row.reference)
            continue

        outcome.contributing_rows.append(row.reference)
        dictionary = store.load()
        _learn_people(store, dictionary, row, context, outcome)
        _learn_tags(store, dictionary, row, context, outcome)
        _learn_place(store, dictionary, row, context, outcome)

    return outcome


def _learn_people(store, dictionary, row, context, outcome) -> None:
    for name in split_list_field(row.people):
        entity_id, created = _ensure_person(store, dictionary, name)
        if created:
            outcome.new_people.append(name)
            store.record_evidence(
                _evidence(
                    EntityType.PERSON, name, EvidenceReason.APPROVED_VS_SOURCE, row,
                    context, status=ConfidenceStatus.CONFIRMED,
                )
            )
        _learn_alias(
            store, dictionary, EntityType.PERSON, entity_id, name, row, context, outcome
        )


def _learn_tags(store, dictionary, row, context, outcome) -> None:
    for raw_tag in split_list_field(row.tags):
        tag = _resolve_canonical(dictionary, EntityType.TAG, raw_tag) or raw_tag
        existing = {entry.canonical_tag.casefold() for entry in dictionary.tags}
        entity_id = store.add_tag(tag)
        if tag.casefold() not in existing:
            outcome.new_tags.append(tag)
            store.record_evidence(
                _evidence(
                    EntityType.TAG, tag, EvidenceReason.APPROVED_VS_SOURCE, row,
                    context, status=ConfidenceStatus.CONFIRMED,
                )
            )
        _learn_alias(
            store, dictionary, EntityType.TAG, entity_id, tag, row, context, outcome
        )


def _learn_place(store, dictionary, row, context, outcome) -> None:
    raw_place = (row.place or "").strip()
    if not raw_place:
        return

    # A value that is already a confirmed alias belongs to that place — it must
    # not come back as a second canonical entity. Without this, merging two
    # spellings in catalog.xlsx would be undone by the next learn, because the
    # review rows still carry the alias spelling.
    place = _resolve_canonical(dictionary, EntityType.PLACE, raw_place) or raw_place

    existing = {entry.canonical_place.casefold() for entry in dictionary.places}
    place_id = store.add_place(place)
    if place.casefold() not in existing:
        outcome.new_places.append(place)
        store.record_evidence(
            _evidence(
                EntityType.PLACE, place, EvidenceReason.APPROVED_VS_SOURCE, row,
                context, status=ConfidenceStatus.CONFIRMED,
            )
        )

    _learn_alias(
        store, dictionary, EntityType.PLACE, place_id, place, row, context, outcome
    )

    point = parse_latlon(row.latlon)
    if point is None:
        return

    result = store.propose_place_latlon(place_id, point)
    conflicted = result == "conflict"
    reason = (
        EvidenceReason.COORDINATE_CONFLICT if conflicted
        else EvidenceReason.APPROVED_COORDINATES
    )
    store.record_evidence(
        _evidence(
            EntityType.PLACE, place, reason, row, context,
            status=ConfidenceStatus.CANDIDATE if conflicted else ConfidenceStatus.CONFIRMED,
            latlon=point,
        )
    )
    if conflicted:
        outcome.latlon_conflicts.append(place)
    elif result == "added":
        # "unchanged" is not news: re-reading a row is not a new coordinate.
        outcome.latlon_added.append(place)


def _learn_alias(
    store, dictionary, entity_type, entity_id, canonical, row, context, outcome
) -> None:
    """Decide whether the source text taught us a new spelling of ``canonical``.

    A confirmed alias needs an unambiguous mapping: the approved value is
    absent from the source text, and exactly one unclaimed phrase there could
    plausibly have produced it. Otherwise it is a candidate.
    """
    source = (row.source_description or "").strip()
    if not source:
        return

    normalized_source = normalize(source)
    if normalize(canonical) in normalized_source:
        # The source already says it plainly; nothing new to learn.
        return

    phrases = _unclaimed_phrases(source, dictionary, entity_type)
    if not phrases:
        return

    if len(phrases) == 1 and not _is_context_dependent(phrases[0]):
        store.add_alias(entity_type, entity_id, phrases[0], ConfidenceStatus.CONFIRMED)
        outcome.confirmed_aliases.append(f"{phrases[0]} -> {canonical}")
        store.record_evidence(
            _evidence(
                entity_type, canonical, EvidenceReason.APPROVED_VS_SOURCE, row, context,
                status=ConfidenceStatus.CONFIRMED, candidate_text=phrases[0],
            )
        )
        return

    for phrase in phrases:
        store.add_alias(entity_type, entity_id, phrase, ConfidenceStatus.CANDIDATE)
        outcome.candidate_aliases.append(f"{phrase} -> {canonical}")
        store.record_evidence(
            _evidence(
                entity_type, canonical, EvidenceReason.FUZZY_CANDIDATE, row, context,
                status=ConfidenceStatus.CANDIDATE, candidate_text=phrase,
            )
        )


def _unclaimed_phrases(
    source: str, dictionary: Dictionary, entity_type: EntityType
) -> list[str]:
    """Phrases in the source text that no dictionary entry already explains.

    A phrase that already belongs to a *different* dictionary is excluded. A
    sentence naming a known person is not a candidate spelling of a place —
    without this, ``Тоня Мамаева (5лет)`` would be learned as an alias of
    ``Сад на Днепропетровской`` simply because it was the only unclaimed chunk.
    """
    claimed = {
        normalize(match.matched_text)
        for match in find_matches(source, dictionary, entity_type)
    }
    other_types = [item for item in EntityType if item is not entity_type]

    phrases: list[str] = []
    for chunk in _candidate_chunks(source):
        normalized = normalize(chunk)
        if not normalized or normalized in claimed:
            continue
        if any(
            find_matches(chunk, dictionary, other) for other in other_types
        ):
            continue
        phrases.append(chunk)
    return phrases


#: An alias longer than this is a sentence, not a name.
MAX_ALIAS_WORDS = 6


def _candidate_chunks(source: str) -> list[str]:
    """Split source text into sentence-ish chunks that could be an alias.

    Chunks that plainly cannot be a name are dropped rather than offered for
    review: fragments opening with a digit are dates or ages (``1979 май``,
    ``5г)-слева``), and anything longer than a short phrase is a sentence. The
    filter is about keeping the candidate list worth reading — everything that
    survives is still only a candidate.
    """
    chunks: list[str] = []
    for part in source.replace("!", ".").replace("?", ".").split("."):
        cleaned = part.strip(" \t,;:-–—()")
        if _is_plausible_alias(cleaned):
            chunks.append(cleaned)
    return chunks


def _is_plausible_alias(text: str) -> bool:
    if len(text) < 3:
        return False
    words = text.split()
    if not words or len(words) > MAX_ALIAS_WORDS:
        return False
    # A leading digit means a year, a date or an age, never a name.
    return not words[0][0].isdigit()


def _is_context_dependent(phrase: str) -> bool:
    """True when a phrase's meaning depends on who is speaking."""
    words = {word.strip(" .,;:()").casefold() for word in phrase.split()}
    return bool(words & CONTEXT_DEPENDENT_TERMS)


def _resolve_canonical(
    dictionary: Dictionary, entity_type: EntityType, value: str
) -> str | None:
    """Return the canonical value a spelling already belongs to, if any.

    Only confirmed aliases resolve. A candidate alias is still a hint, and
    must not silently absorb a value a reviewer typed.
    """
    target = normalize(value)
    if entity_type is EntityType.PERSON:
        entries = [(item.canonical_name, item.confirmed_aliases) for item in dictionary.people]
    elif entity_type is EntityType.PLACE:
        entries = [(item.canonical_place, item.confirmed_aliases) for item in dictionary.places]
    else:
        entries = [(item.canonical_tag, item.confirmed_aliases) for item in dictionary.tags]

    for canonical, aliases in entries:
        if normalize(canonical) == target:
            return canonical
        if any(normalize(alias) == target for alias in aliases):
            return canonical
    return None


def _ensure_person(store, dictionary, name: str) -> tuple[str, bool]:
    name = _resolve_canonical(dictionary, EntityType.PERSON, name) or name
    existing = {person.canonical_name.casefold() for person in dictionary.people}
    entity_id = store.add_person(name)
    return entity_id, name.casefold() not in existing


def _evidence(
    entity_type, entity_value, reason, row, context, *, status, candidate_text=None,
    latlon=None,
) -> Evidence:
    return Evidence(
        entity_type=entity_type,
        entity_value=entity_value,
        reason=reason,
        candidate_text=candidate_text,
        proposed_latlon=latlon,
        source_root=context.source_root,
        source_folder=context.source_folder,
        reference=row.reference,
        source_description=row.source_description,
        section_context=row.section_context,
        run_id=context.run_id,
        status=status,
    )
