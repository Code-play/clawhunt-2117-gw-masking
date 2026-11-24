"""Placeholder vocabulary and the per-request allocator.

Placeholders are ``[[TYPE_SEQ]]`` (for example ``[[PHONE_1]]``).  Within one
request an identical value always gets the identical placeholder, and the
sequence number grows in order of first appearance (per type).  The format is
deliberately reserved: input that already contains one of these tokens is
rejected, because a token produced by the gateway and a token typed by the user
are indistinguishable when the response is restored.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .errors import InvalidMaskingInput, PlaceholderCollisionError

TYPE_PHONE = "PHONE"
TYPE_IDNO = "IDNO"
TYPE_EMAIL = "EMAIL"
TYPES: tuple[str, ...] = (TYPE_PHONE, TYPE_IDNO, TYPE_EMAIL)

#: Documented placeholder grammar.  ``TYPES`` is the closed set of type names.
TOKEN_PATTERN = r"\[\[(PHONE|IDNO|EMAIL)_(\d{1,4})\]\]"
TOKEN_RE = re.compile(TOKEN_PATTERN)

#: Appended to the end of the system message (order text, verbatim).
SYSTEM_NOTE = "文中 [[类型_序号]] 为占位符，请原样保留，不要改写或推测"

REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")
MAX_TOKEN_SEQUENCE = 9999


def format_token(kind: str, sequence: int) -> str:
    """Render a placeholder for ``kind`` and 1-based ``sequence``."""

    if kind not in TYPES:
        raise InvalidMaskingInput(f"unknown placeholder type {kind!r}")
    if not 1 <= sequence <= MAX_TOKEN_SEQUENCE:
        raise InvalidMaskingInput("placeholder sequence out of range")
    return f"[[{kind}_{sequence}]]"


def validate_request_id(request_id: object) -> str:
    """Return a safe ``request_id`` or raise.

    The id becomes part of a Redis key (``gw:mask:{request_id}``) and of the
    AES-GCM associated data, so it is restricted to an explicit safe alphabet.
    """

    if not isinstance(request_id, str) or not REQUEST_ID_RE.match(request_id):
        raise InvalidMaskingInput(
            "request_id must be 8-128 characters of [A-Za-z0-9._:-], starting alphanumeric"
        )
    return request_id


def find_reserved_tokens(text: str) -> list[str]:
    """Return placeholder-shaped tokens already present in ``text``."""

    return [match.group(0) for match in TOKEN_RE.finditer(text)]


def assert_no_reserved_tokens(texts: list[str]) -> None:
    """Reject input that already contains placeholder syntax (collision safety)."""

    for text in texts:
        if TOKEN_RE.search(text):
            raise PlaceholderCollisionError(
                "input already contains reserved placeholder syntax [[TYPE_SEQ]]; "
                "masking refuses it so that restoration cannot corrupt user text"
            )


@dataclass
class PlaceholderAllocator:
    """Request-scoped value -> placeholder assignment."""

    _by_value: dict[tuple[str, str], str] = field(default_factory=dict)
    _sequences: dict[str, int] = field(default_factory=lambda: {kind: 0 for kind in TYPES})
    _values: dict[str, str] = field(default_factory=dict)
    hits: int = 0

    def allocate(self, kind: str, value: str) -> str:
        """Return the placeholder for ``value``, creating it on first sight."""

        if kind not in TYPES:
            raise InvalidMaskingInput(f"unknown placeholder type {kind!r}")
        cache_key = (kind, value)
        existing = self._by_value.get(cache_key)
        if existing is not None:
            return existing
        self._sequences[kind] += 1
        token = format_token(kind, self._sequences[kind])
        self._by_value[cache_key] = token
        self._values[token] = value
        return token

    def count_hit(self) -> None:
        """Count one replaced occurrence (a repeated value still counts)."""

        self.hits += 1

    @property
    def distinct_values(self) -> int:
        return len(self._values)

    @property
    def kinds(self) -> dict[str, str]:
        """Placeholder -> entity type (never the masked value)."""

        return {placeholder: kind for (kind, _value), placeholder in self._by_value.items()}

    def mapping_payload(self, request_id: str) -> dict[str, object]:
        """Serialisable mapping table stored encrypted under ``gw:mask:{request_id}``."""

        return {
            "request_id": request_id,
            "placeholders": dict(self._values),
            "sequences": dict(self._sequences),
            "distinct_values": self.distinct_values,
            "hits": self.hits,
        }


def restore_text(text: str, placeholders: dict[str, str]) -> str:
    """Replace known placeholders with their original values.

    Unknown ``[[TYPE_SEQ]]`` tokens are left untouched: they were not produced
    for this request and replacing them would corrupt the text.
    """

    if not placeholders:
        return text

    def _replace(match: re.Match[str]) -> str:
        return placeholders.get(match.group(0), match.group(0))

    return TOKEN_RE.sub(_replace, text)
