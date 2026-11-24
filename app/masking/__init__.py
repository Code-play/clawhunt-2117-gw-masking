"""Pre-invocation masking: placeholders, encrypted mapping table, restoration."""

from .crypto import decrypt_mapping, encrypt_mapping, resolve_key
from .errors import (
    InvalidMaskingInput,
    MappingDecryptError,
    MappingNotFoundError,
    MaskingError,
    MaskKeyError,
    PlaceholderCollisionError,
)
from .placeholders import (
    SYSTEM_NOTE,
    TOKEN_PATTERN,
    TOKEN_RE,
    PlaceholderAllocator,
    format_token,
    restore_text,
    validate_request_id,
)
from .recognizers import EntityMatch, Recognizer, find_entities, is_valid_id_checksum
from .service import MaskResult, MaskingService
from .store import MAPPING_KEY_PREFIX, RedisMappingStore, mapping_key

__all__ = [
    "MAPPING_KEY_PREFIX",
    "SYSTEM_NOTE",
    "TOKEN_PATTERN",
    "TOKEN_RE",
    "EntityMatch",
    "InvalidMaskingInput",
    "MaskKeyError",
    "MaskResult",
    "MaskingError",
    "MaskingService",
    "MappingDecryptError",
    "MappingNotFoundError",
    "PlaceholderAllocator",
    "PlaceholderCollisionError",
    "RedisMappingStore",
    "Recognizer",
    "decrypt_mapping",
    "encrypt_mapping",
    "find_entities",
    "format_token",
    "is_valid_id_checksum",
    "mapping_key",
    "resolve_key",
    "restore_text",
    "validate_request_id",
]
