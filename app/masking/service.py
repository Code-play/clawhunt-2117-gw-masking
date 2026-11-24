"""``MaskingService.mask(messages, request_id)`` and the restoration side.

The service is the single place that decides what leaves the gateway: it walks
every role of ``messages``, replaces recognised values with request-scoped
placeholders, seals the mapping table into Redis (TTL 600 s), appends the fixed
placeholder note to the system message, and reports how many entities were
masked.  Only masked text is handed to the vendor client.

Nothing in this module logs message content, recognised values, placeholders or
the mapping table.  Log lines carry the request id, the hit count and the
distinct value count only.
"""

from __future__ import annotations

import copy
import logging
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from .errors import InvalidMaskingInput, MappingNotFoundError
from .placeholders import (
    SYSTEM_NOTE,
    PlaceholderAllocator,
    assert_no_reserved_tokens,
    restore_text,
    validate_request_id,
)
from .recognizers import EntityMatch, Recognizer, find_entities
from .store import RedisMappingStore

logger = logging.getLogger("app.masking")

Message = dict[str, Any]


@dataclass(frozen=True)
class MaskResult:
    """Outcome of one masking pass."""

    request_id: str
    messages: list[Message]
    masked_entity_count: int
    distinct_values: int
    placeholder_kinds: dict[str, str] = field(default_factory=dict)

    def header_value(self) -> int:
        """Value for ``x_gw.masked_entity_count``."""

        return self.masked_entity_count

    def safe_summary(self) -> dict[str, Any]:
        """Summary with no originals and no placeholder values."""

        return {
            "request_id": self.request_id,
            "masked_entity_count": self.masked_entity_count,
            "distinct_values": self.distinct_values,
            "placeholder_kinds": dict(self.placeholder_kinds),
        }


def _validate_messages(messages: object) -> list[Message]:
    if not isinstance(messages, Sequence) or isinstance(messages, (str, bytes)) or not messages:
        raise InvalidMaskingInput("messages must be a non-empty list of chat messages")
    cleaned: list[Message] = []
    for index, message in enumerate(messages):
        if not isinstance(message, dict):
            raise InvalidMaskingInput(f"messages[{index}] must be an object")
        role = message.get("role")
        if not isinstance(role, str) or not role.strip():
            raise InvalidMaskingInput(f"messages[{index}].role must be a non-empty string")
        cleaned.append(copy.deepcopy(message))
    return cleaned


def _text_slots(message: Message) -> list[tuple[Any, str]]:
    """Enumerate the text slots of one message as ``(key or index, text)``.

    Supports the OpenAI chat shape: ``content`` is either a string or a list of
    parts such as ``{"type": "text", "text": "..."}``.  Non-text parts are
    passed through untouched.
    """

    slots: list[tuple[Any, str]] = []
    content = message.get("content")
    if isinstance(content, str):
        slots.append(("content", content))
    elif isinstance(content, list):
        for part_index, part in enumerate(content):
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                slots.append((("part", part_index), part["text"]))
    return slots


class MaskingService:
    """Mask chat messages before they are sent to the model vendor."""

    def __init__(
        self,
        store: RedisMappingStore,
        *,
        recognizers: Iterable[Recognizer] | None = None,
    ) -> None:
        self._store = store
        self._recognizers = tuple(recognizers or ())

    # ------------------------------------------------------------------ mask

    def mask(self, messages: object, request_id: str) -> MaskResult:
        """Replace recognised values with ``[[TYPE_SEQ]]`` placeholders.

        Raises :class:`InvalidMaskingInput` for a malformed payload and
        :class:`PlaceholderCollisionError` when the input already contains
        reserved placeholder syntax.
        """

        validate_request_id(request_id)
        prepared = _validate_messages(messages)

        all_texts = [text for message in prepared for _, text in _text_slots(message)]
        assert_no_reserved_tokens(all_texts)

        allocator = PlaceholderAllocator()
        masked_messages: list[Message] = []
        for message in prepared:
            masked = self._mask_message(message, allocator)
            masked_messages.append(masked)

        masked_messages = self._append_system_note(masked_messages)
        self._store.save(request_id, allocator.mapping_payload(request_id))

        result = MaskResult(
            request_id=request_id,
            messages=masked_messages,
            masked_entity_count=allocator.hits,
            distinct_values=allocator.distinct_values,
            placeholder_kinds=_placeholder_kinds(allocator),
        )
        logger.debug(
            "masking applied request_id=%s masked_entity_count=%d distinct_values=%d",
            request_id,
            result.masked_entity_count,
            result.distinct_values,
        )
        return result

    def _mask_message(self, message: Message, allocator: PlaceholderAllocator) -> Message:
        content = message.get("content")
        if isinstance(content, str):
            message["content"] = self._mask_text(content, allocator)
        elif isinstance(content, list):
            new_parts: list[Any] = []
            for part in content:
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    part = dict(part)
                    part["text"] = self._mask_text(part["text"], allocator)
                new_parts.append(part)
            message["content"] = new_parts
        return message

    def _mask_text(self, text: str, allocator: PlaceholderAllocator) -> str:
        matches: list[EntityMatch] = find_entities(text, self._recognizers)
        if not matches:
            return text
        chunks: list[str] = []
        cursor = 0
        for match in matches:
            chunks.append(text[cursor : match.start])
            token = allocator.allocate(match.kind, match.value)
            allocator.count_hit()
            chunks.append(token)
            cursor = match.end
        chunks.append(text[cursor:])
        return "".join(chunks)

    @staticmethod
    def _append_system_note(messages: list[Message]) -> list[Message]:
        """Append the fixed note to the system message, creating one if absent."""

        for message in reversed(messages):
            if message.get("role") == "system":
                content = message.get("content")
                if isinstance(content, str):
                    body = content.rstrip()
                    message["content"] = f"{body}\n\n{SYSTEM_NOTE}" if body else SYSTEM_NOTE
                elif isinstance(content, list):
                    message["content"] = [*content, {"type": "text", "text": SYSTEM_NOTE}]
                else:
                    message["content"] = SYSTEM_NOTE
                return messages
        messages.insert(0, {"role": "system", "content": SYSTEM_NOTE})
        return messages

    # --------------------------------------------------------------- restore

    def restore_text(self, request_id: str, text: str) -> str:
        """Restore placeholders in ``text``, then delete the mapping."""

        validate_request_id(request_id)
        if not isinstance(text, str):
            raise InvalidMaskingInput("text must be a string")
        mapping = self._store.load(request_id)
        placeholders = _placeholders_of(mapping)
        restored = restore_text(text, placeholders)
        self._store.delete(request_id)
        logger.debug("mapping restored and deleted request_id=%s", request_id)
        return restored

    def restore_messages(self, request_id: str, messages: object) -> list[Message]:
        """Restore placeholders in every text slot, then delete the mapping."""

        validate_request_id(request_id)
        prepared = _validate_messages(messages)
        mapping = self._store.load(request_id)
        placeholders = _placeholders_of(mapping)
        restored: list[Message] = []
        for message in prepared:
            content = message.get("content")
            if isinstance(content, str):
                message["content"] = restore_text(content, placeholders)
            elif isinstance(content, list):
                new_parts: list[Any] = []
                for part in content:
                    if isinstance(part, dict) and isinstance(part.get("text"), str):
                        part = dict(part)
                        part["text"] = restore_text(part["text"], placeholders)
                    new_parts.append(part)
                message["content"] = new_parts
            restored.append(message)
        self._store.delete(request_id)
        logger.debug("mapping restored and deleted request_id=%s", request_id)
        return restored

    def discard(self, request_id: str) -> bool:
        """Drop a mapping without restoring (for aborted calls)."""

        validate_request_id(request_id)
        return self._store.delete(request_id)


def _placeholders_of(mapping: dict[str, Any]) -> dict[str, str]:
    raw = mapping.get("placeholders")
    if not isinstance(raw, dict):
        raise MappingNotFoundError("stored mapping has no placeholder table")
    return {str(token): str(value) for token, value in raw.items() if isinstance(token, str)}


def _placeholder_kinds(allocator: PlaceholderAllocator) -> dict[str, str]:
    """Map placeholder -> entity type, without exposing the masked values."""

    return allocator.kinds
