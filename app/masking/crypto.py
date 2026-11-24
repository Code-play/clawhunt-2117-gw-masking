"""AES-GCM envelope for the request-level mapping table.

The mapping table (placeholder -> original value) is the only place where the
original values live after masking.  It is never written to Redis in clear
text: the JSON payload is sealed with AES-256-GCM before it reaches the
store, and the associated data binds the ciphertext to the request id so a
blob copied from one request cannot be replayed under another.

Key material
------------
``GW_MASK_KEY`` may be either

* a base64 (standard or URL-safe) encoded 32-byte key, or
* a passphrase of at least 16 characters, from which a 32-byte key is derived
  with HKDF-SHA256 (salt ``gw-mask-v1``, info ``gw:mask:key``).

Both forms are documented in the README; the derived form exists because
operators tend to paste a passphrase into a secret store instead of generated
bytes.  A short passphrase is rejected instead of being silently stretched.
"""

from __future__ import annotations

import base64
import binascii
import json
import os
import re
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from .errors import MappingDecryptError, MaskKeyError

KEY_BYTES = 32
NONCE_BYTES = 12
MIN_PASSPHRASE_CHARS = 16
ENVELOPE_VERSION = b"\x01"

_HKDF_SALT = b"gw-mask-v1"
_HKDF_INFO = b"gw:mask:key"
_BASE64_KEY_RE = re.compile(r"^[A-Za-z0-9+/_-]{43,44}={0,2}$")


def _base64_bytes(raw: str) -> bytes | None:
    """Decode a base64 key (standard or URL-safe) or return ``None``.

    Only strings that can actually be a 32-byte key are attempted, so a
    passphrase containing ``-`` or ``_`` is not silently read as base64.
    """

    candidate = raw.strip()
    if not _BASE64_KEY_RE.match(candidate):
        return None
    padded = candidate + "=" * (-len(candidate) % 4)
    for decoder in (base64.b64decode, base64.urlsafe_b64decode):
        try:
            if decoder is base64.b64decode:
                return decoder(padded, validate=True)
            return decoder(padded)
        except (binascii.Error, ValueError):
            continue
    return None


def derive_key_from_passphrase(passphrase: str) -> bytes:
    """Stretch a passphrase into 32 bytes with HKDF-SHA256."""

    if len(passphrase) < MIN_PASSPHRASE_CHARS:
        raise MaskKeyError(
            "GW_MASK_KEY is too short: use a base64 32-byte key or a passphrase "
            f"of at least {MIN_PASSPHRASE_CHARS} characters"
        )
    hkdf = HKDF(algorithm=hashes.SHA256(), length=KEY_BYTES, salt=_HKDF_SALT, info=_HKDF_INFO)
    return hkdf.derive(passphrase.encode("utf-8"))


def resolve_key(raw: str | None = None) -> bytes:
    """Return 32 bytes of key material for ``GW_MASK_KEY``.

    ``raw`` defaults to the environment variable, which is the only supported
    production source.  Tests pass the value explicitly.
    """

    candidate = raw if raw is not None else os.environ.get("GW_MASK_KEY")
    if candidate is None or not candidate.strip():
        raise MaskKeyError("GW_MASK_KEY is not set; the gateway must fail closed without a key")

    decoded = _base64_bytes(candidate)
    if decoded is not None and len(decoded) == KEY_BYTES:
        return decoded
    # Not a 32-byte base64 key: treat it as a passphrase (may raise if too short).
    return derive_key_from_passphrase(candidate.strip())


def associated_data(request_id: str) -> bytes:
    return b"gw:mask:" + request_id.encode("utf-8") + b":v1"


def encrypt_mapping(key: bytes, request_id: str, mapping: dict[str, Any]) -> str:
    """Seal ``mapping`` and return a transport-safe base64 envelope."""

    if len(key) != KEY_BYTES:
        raise MaskKeyError("masking key must be 32 bytes")
    payload = json.dumps(mapping, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    nonce = os.urandom(NONCE_BYTES)
    ciphertext = AESGCM(key).encrypt(nonce, payload, associated_data(request_id))
    envelope = ENVELOPE_VERSION + nonce + ciphertext
    return base64.urlsafe_b64encode(envelope).decode("ascii")


def decrypt_mapping(key: bytes, request_id: str, envelope: str) -> dict[str, Any]:
    """Open an envelope produced by :func:`encrypt_mapping`."""

    if len(key) != KEY_BYTES:
        raise MaskKeyError("masking key must be 32 bytes")
    try:
        raw = base64.urlsafe_b64decode(envelope.encode("ascii") + b"=" * (-len(envelope) % 4))
    except (binascii.Error, ValueError) as exc:
        raise MappingDecryptError("stored mapping envelope is not valid base64") from exc
    if len(raw) < 1 + NONCE_BYTES + 16 or raw[:1] != ENVELOPE_VERSION:
        raise MappingDecryptError("stored mapping envelope has an unsupported format")
    nonce = raw[1 : 1 + NONCE_BYTES]
    ciphertext = raw[1 + NONCE_BYTES :]
    try:
        payload = AESGCM(key).decrypt(nonce, ciphertext, associated_data(request_id))
    except InvalidTag as exc:
        # Wrong key, wrong request id, or tampered ciphertext: indistinguishable
        # on purpose, and the message never echoes the stored bytes.
        raise MappingDecryptError(
            "mapping cannot be authenticated with this key and request id"
        ) from exc
    try:
        mapping = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:  # pragma: no cover - defensive
        raise MappingDecryptError("decrypted mapping is not valid JSON") from exc
    if not isinstance(mapping, dict):
        raise MappingDecryptError("decrypted mapping has an unexpected shape")  # pragma: no cover
    return mapping
