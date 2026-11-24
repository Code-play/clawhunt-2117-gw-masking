"""Encrypted, request-scoped storage of the mapping table in Redis.

Key layout: ``gw:mask:{request_id}``.  The value is the AES-GCM envelope built
by :mod:`app.masking.crypto`, with a TTL of 600 seconds by default.  The
mapping is deleted as soon as restoration has completed, so a request id only
keeps its reversibility for the length of one gateway call.

The Redis client is injected, which keeps the store testable with ``fakeredis``
and lets the deployment reuse the platform's pooled client.
"""

from __future__ import annotations

from typing import Any, Protocol

from redis import Redis

from .crypto import decrypt_mapping, encrypt_mapping, resolve_key
from .errors import MappingNotFoundError
from .placeholders import validate_request_id

MAPPING_KEY_PREFIX = "gw:mask:"
DEFAULT_TTL_SECONDS = 600


class RedisLike(Protocol):
    """The subset of the Redis client this store needs."""

    def get(self, name: str) -> bytes | None: ...  # pragma: no cover - protocol

    def set(self, name: str, value: str, ex: int) -> object: ...  # pragma: no cover - protocol

    def delete(self, *names: str) -> int: ...  # pragma: no cover - protocol

    def ttl(self, name: str) -> int: ...  # pragma: no cover - protocol


def mapping_key(request_id: str) -> str:
    return f"{MAPPING_KEY_PREFIX}{validate_request_id(request_id)}"


class RedisMappingStore:
    """Persist and retrieve the encrypted placeholder -> original mapping."""

    def __init__(
        self,
        client: RedisLike,
        *,
        mask_key: str | None = None,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        self._client = client
        self._ttl_seconds = int(ttl_seconds)
        self._key = resolve_key(mask_key)

    @classmethod
    def from_url(cls, url: str, **kwargs: Any) -> "RedisMappingStore":
        return cls(Redis.from_url(url, decode_responses=False), **kwargs)

    @property
    def ttl_seconds(self) -> int:
        return self._ttl_seconds

    def key_for(self, request_id: str) -> str:
        return mapping_key(request_id)

    def save(self, request_id: str, mapping: dict[str, Any]) -> str:
        """Encrypt ``mapping`` and store it under ``gw:mask:{request_id}``."""

        key = mapping_key(request_id)
        envelope = encrypt_mapping(self._key, request_id, mapping)
        self._client.set(key, envelope, ex=self._ttl_seconds)
        return key

    def load(self, request_id: str) -> dict[str, Any]:
        key = mapping_key(request_id)
        envelope = self._client.get(key)
        if envelope is None:
            raise MappingNotFoundError(
                "no mapping is stored for this request (expired, already restored, or unknown)"
            )
        if isinstance(envelope, bytes):
            envelope = envelope.decode("ascii")
        return decrypt_mapping(self._key, request_id, envelope)

    def delete(self, request_id: str) -> bool:
        return bool(self._client.delete(mapping_key(request_id)))

    def ttl(self, request_id: str) -> int:
        return int(self._client.ttl(mapping_key(request_id)))

    def raw_value(self, request_id: str) -> str | None:
        """Return the stored envelope for diagnostics and operational checks.

        The value is ciphertext; this accessor exists so operators and tests can
        assert that no plaintext is stored without having to decrypt.
        """

        raw = self._client.get(mapping_key(request_id))
        if raw is None:
            return None
        return raw.decode("ascii") if isinstance(raw, bytes) else str(raw)
