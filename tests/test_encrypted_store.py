"""Acceptance criterion 2: the stored mapping is ciphertext and restores only with the key."""

from __future__ import annotations

import pytest

from app.masking import (
    MappingDecryptError,
    MaskingService,
    decrypt_mapping,
    mapping_key,
    resolve_key,
)
from app.masking.errors import MappingNotFoundError
from app.masking.store import RedisMappingStore

from .conftest import EMAIL_A, IDNO_A, PHONE_A, TEST_MASK_KEY


def _mask_sample(masking: MaskingService, request_id: str) -> str:
    masking.mask(
        [
            {
                "role": "user",
                "content": f"联系 {PHONE_A} / {EMAIL_A} / 证件 {IDNO_A}",
            }
        ],
        request_id,
    )
    return request_id


def test_criterion_2_redis_value_is_not_readable_but_decrypts(
    masking: MaskingService, redis_client
) -> None:
    request_id = _mask_sample(masking, "req-criterion-2")

    key = mapping_key(request_id)
    raw = redis_client.get(key)
    assert raw is not None, "the mapping must be stored in Redis"
    blob = raw.decode("ascii")

    for secret in (PHONE_A, EMAIL_A, IDNO_A):
        assert secret not in blob, "the stored value must not expose the original"
    assert "placeholders" not in blob, "the mapping table structure must not be readable"

    decrypted = decrypt_mapping(resolve_key(TEST_MASK_KEY), request_id, blob)
    assert decrypted["placeholders"]["[[PHONE_1]]"] == PHONE_A
    assert decrypted["placeholders"]["[[EMAIL_1]]"] == EMAIL_A
    assert decrypted["placeholders"]["[[IDNO_1]]"] == IDNO_A


def test_restore_replaces_tokens_with_originals(masking: MaskingService, redis_client) -> None:
    request_id = _mask_sample(masking, "req-restore")
    masked = masking.mask([{"role": "user", "content": f"tel {PHONE_A}"}], request_id + "-2")

    restored = masking.restore_text(request_id, f"请回拨 [[PHONE_1]]，邮箱 [[EMAIL_1]]")

    assert restored == f"请回拨 {PHONE_A}，邮箱 {EMAIL_A}"
    assert masked.masked_entity_count == 1


def test_mapping_is_deleted_after_restoration(masking: MaskingService, redis_client) -> None:
    request_id = _mask_sample(masking, "req-delete-after")
    key = mapping_key(request_id)
    assert redis_client.get(key) is not None

    masking.restore_text(request_id, "[[PHONE_1]]")

    assert redis_client.get(key) is None
    with pytest.raises(MappingNotFoundError):
        masking.restore_text(request_id, "[[PHONE_1]]")


def test_stored_mapping_expires_after_six_hundred_seconds(
    masking: MaskingService, redis_client, store: RedisMappingStore
) -> None:
    request_id = _mask_sample(masking, "req-ttl-600")

    assert store.ttl_seconds == 600
    ttl = redis_client.ttl(mapping_key(request_id))
    assert 0 < ttl <= 600


def test_wrong_key_cannot_open_the_mapping(masking: MaskingService, redis_client) -> None:
    request_id = _mask_sample(masking, "req-wrong-key")
    blob = redis_client.get(mapping_key(request_id)).decode("ascii")
    other_key = resolve_key("another-test-passphrase-of-32-chars")

    with pytest.raises(MappingDecryptError):
        decrypt_mapping(other_key, request_id, blob)


def test_ciphertext_is_bound_to_the_request_id(masking: MaskingService, redis_client) -> None:
    request_id = _mask_sample(masking, "req-bound-a")
    blob = redis_client.get(mapping_key(request_id)).decode("ascii")

    with pytest.raises(MappingDecryptError):
        decrypt_mapping(resolve_key(TEST_MASK_KEY), "req-bound-b", blob)


def test_tampered_ciphertext_is_rejected(masking: MaskingService, redis_client) -> None:
    request_id = _mask_sample(masking, "req-tampered")
    blob = redis_client.get(mapping_key(request_id)).decode("ascii")
    position = len(blob) // 2
    marker = "A" if blob[position] != "A" else "B"
    tampered = blob[:position] + marker + blob[position + 1 :]

    with pytest.raises(MappingDecryptError):
        decrypt_mapping(resolve_key(TEST_MASK_KEY), request_id, tampered)


def test_store_requires_a_key() -> None:
    from app.masking import MaskKeyError, RedisMappingStore

    with pytest.raises(MaskKeyError):
        RedisMappingStore(None, mask_key="")  # type: ignore[arg-type]


def test_short_passphrase_is_rejected() -> None:
    from app.masking import MaskKeyError

    with pytest.raises(MaskKeyError):
        resolve_key("too-short")
