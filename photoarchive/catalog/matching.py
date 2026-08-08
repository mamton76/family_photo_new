"""Matching raw description text against the dictionaries.

The rules, in order of strength:

1. an exact canonical phrase (``Антонина Мамаева``);
2. an exact **confirmed** alias (``Тоня Мамаева``, ``Тоня``);
3. a **candidate** alias — reported as weak evidence only, never turned into a
   suggestion.

Longer phrases beat shorter ones, so ``Тоня Мамаева`` wins over the bare alias
``Тоня`` and the shorter one is not also reported inside the same span. Nothing
here does fuzzy resolution: an unrecognised name stays unrecognised, because an
empty suggestion is cheaper to fix than a wrong one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from photoarchive.catalog.models import ConfidenceStatus, Dictionary, EntityType

#: Word characters for boundary checks, including Cyrillic.
_WORD_RE = re.compile(r"\w", re.UNICODE)


@dataclass(frozen=True, slots=True)
class PhraseMatch:
    """One dictionary phrase found in a piece of text."""

    entity_type: EntityType
    canonical: str
    matched_text: str
    status: ConfidenceStatus
    start: int
    end: int

    @property
    def is_confirmed(self) -> bool:
        return self.status is ConfidenceStatus.CONFIRMED


def normalize(text: str) -> str:
    """Casefold and collapse whitespace for tolerant comparison."""
    return " ".join(text.casefold().split())


def _phrases_for(dictionary: Dictionary, entity_type: EntityType):
    """Yield ``(phrase, canonical, status)`` for one dictionary."""
    if entity_type is EntityType.PERSON:
        entities = [(person.canonical_name, person.aliases) for person in dictionary.people]
    elif entity_type is EntityType.PLACE:
        entities = [(place.canonical_place, place.aliases) for place in dictionary.places]
    else:
        entities = [(tag.canonical_tag, tag.aliases) for tag in dictionary.tags]

    for canonical, aliases in entities:
        # The canonical spelling is itself the strongest phrase.
        yield canonical, canonical, ConfidenceStatus.CONFIRMED
        for alias in aliases:
            if alias.status is not ConfidenceStatus.REJECTED:
                yield alias.text, canonical, alias.status


def find_matches(
    text: str, dictionary: Dictionary, entity_type: EntityType
) -> list[PhraseMatch]:
    """Find every non-overlapping dictionary phrase in ``text``.

    Longer phrases are tried first, and a span already claimed by a longer
    match is not re-reported, so ``Тоня Мамаева`` does not also yield ``Тоня``.
    """
    if not text:
        return []

    haystack = text.casefold()
    claimed: list[tuple[int, int]] = []
    matches: list[PhraseMatch] = []

    candidates = sorted(
        _phrases_for(dictionary, entity_type),
        key=lambda item: (-len(item[0]), item[0]),
    )

    for phrase, canonical, status in candidates:
        needle = phrase.casefold().strip()
        if not needle:
            continue

        start = haystack.find(needle)
        while start != -1:
            end = start + len(needle)
            if _has_word_boundaries(haystack, start, end) and not _overlaps(
                claimed, start, end
            ):
                claimed.append((start, end))
                matches.append(
                    PhraseMatch(
                        entity_type=entity_type,
                        canonical=canonical,
                        matched_text=text[start:end],
                        status=status,
                        start=start,
                        end=end,
                    )
                )
            start = haystack.find(needle, start + 1)

    matches.sort(key=lambda match: match.start)
    return matches


def confirmed_canonicals(matches: list[PhraseMatch]) -> list[str]:
    """Canonical values from confirmed matches only, deduplicated in order."""
    seen: set[str] = set()
    result: list[str] = []
    for match in matches:
        if not match.is_confirmed or match.canonical in seen:
            continue
        seen.add(match.canonical)
        result.append(match.canonical)
    return result


def candidate_matches(matches: list[PhraseMatch]) -> list[PhraseMatch]:
    """Matches that are hints only and must not become suggestions."""
    return [match for match in matches if match.status is ConfidenceStatus.CANDIDATE]


def _has_word_boundaries(text: str, start: int, end: int) -> bool:
    before = text[start - 1] if start > 0 else ""
    after = text[end] if end < len(text) else ""
    return not (_WORD_RE.match(before) or _WORD_RE.match(after))


def _overlaps(claimed: list[tuple[int, int]], start: int, end: int) -> bool:
    return any(start < other_end and end > other_start for other_start, other_end in claimed)
