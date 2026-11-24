"""Entity recognisers for the pre-invocation masking pass.

The recognisers are deliberately pattern based: they run before any vendor
call, they must never call a network service, and they must work on purely
fictional data.  A validator (for example an ML/NER recogniser or an internal
ID registry) plugs in later by implementing :class:`Recognizer` and passing it
to :class:`app.masking.service.MaskingService`; the README documents that
integration point.

Masking is privacy-first: an 18-character ID-shaped value is masked even when
its checksum is wrong, because the gateway cannot know whether the value is a
typo in an otherwise real record.  The checksum helper is exported only for
callers that need to *classify* a value, never to decide whether to mask it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Protocol

from .placeholders import TYPE_EMAIL, TYPE_IDNO, TYPE_PHONE

EMAIL_RE = re.compile(
    r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9](?:[A-Za-z0-9\-]*[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9\-]*[A-Za-z0-9])?)+"
)
# 18 characters: 17 digits and a digit/X checksum, on digit boundaries.
IDNO_RE = re.compile(r"(?<![\dXx])[1-9]\d{16}[\dXx](?![\dXx])")
# Mainland mobile numbers, optionally with a +86 country prefix and optional
# single separators between the 3-4-4 groups.
PHONE_RE = re.compile(r"(?<!\d)(?:\+?86[-\s]?)?1[3-9]\d(?:[-\s]?\d{4}){2}(?!\d)")

# Priority order: an e-mail address may embed a phone-shaped local part, and an
# ID card contains long digit runs, so wider/structured patterns win first.
_PRIORITY: tuple[tuple[str, re.Pattern[str]], ...] = (
    (TYPE_EMAIL, EMAIL_RE),
    (TYPE_IDNO, IDNO_RE),
    (TYPE_PHONE, PHONE_RE),
)

_ID_WEIGHTS = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
_ID_CHECK_CODES = "10X98765432"


class Recognizer(Protocol):
    """Recogniser interface documented as the integration point for other tickets."""

    def find(self, text: str) -> list["EntityMatch"]:  # pragma: no cover - protocol
        ...


@dataclass(frozen=True)
class EntityMatch:
    """One occurrence of a sensitive value in a text."""

    start: int
    end: int
    kind: str
    value: str


def is_valid_id_checksum(value: str) -> bool:
    """Return whether ``value`` passes the GB 11643 mod-11 checksum.

    Classification only: :func:`find_entities` does not use it, so a mistyped
    ID still gets masked.
    """

    if len(value) != 18:
        return False
    body, check = value[:17], value[17].upper()
    if not body.isdigit() or check not in _ID_CHECK_CODES:
        return False
    total = sum(int(digit) * weight for digit, weight in zip(body, _ID_WEIGHTS))
    return check == _ID_CHECK_CODES[total % 11]


def _overlaps(span: tuple[int, int], taken: list[tuple[int, int]]) -> bool:
    start, end = span
    return any(start < other_end and other_start < end for other_start, other_end in taken)


def find_entities(text: str, recognizers: Iterable[Recognizer] | None = None) -> list[EntityMatch]:
    """Return non-overlapping matches, ordered by position.

    ``recognizers`` extends (it does not replace) the built-in pattern set.
    """

    matches: list[EntityMatch] = []
    taken: list[tuple[int, int]] = []
    for kind, pattern in _PRIORITY:
        for found in pattern.finditer(text):
            span = (found.start(), found.end())
            if _overlaps(span, taken):
                continue
            taken.append(span)
            matches.append(EntityMatch(found.start(), found.end(), kind, found.group(0)))
    for recognizer in recognizers or ():
        for found in recognizer.find(text):
            span = (found.start, found.end)
            if _overlaps(span, taken):
                continue
            taken.append(span)
            matches.append(found)
    matches.sort(key=lambda item: item.start)
    return matches
