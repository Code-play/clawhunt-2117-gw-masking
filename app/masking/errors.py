"""Masking domain errors.

None of these errors may carry original values: they are raised while the
gateway is holding personal data and their text can end up in a log line or an
HTTP problem document.
"""

from __future__ import annotations


class MaskingError(RuntimeError):
    """Base class for masking failures."""


class MaskKeyError(MaskingError):
    """``GW_MASK_KEY`` is missing or not usable as key material."""


class MappingDecryptError(MaskingError):
    """The stored mapping could not be authenticated/decrypted with the key."""


class MappingNotFoundError(MaskingError):
    """No mapping is stored for the request (expired, already restored, or unknown)."""


class PlaceholderCollisionError(MaskingError):
    """The input already contains reserved placeholder syntax."""


class InvalidMaskingInput(MaskingError):
    """The masking input does not match the documented message shape."""
