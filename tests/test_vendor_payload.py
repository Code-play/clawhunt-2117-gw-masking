"""Acceptance criterion 3: the vendor request carries no original values."""

from __future__ import annotations

import json

import httpx
import pytest

from app.gateway import ChatGateway, VendorCallError, VendorClient
from app.masking import MaskingService, mapping_key

from .conftest import EMAIL_A, IDNO_A, PHONE_A, PHONE_B, SAMPLE_MESSAGES, CapturingVendor


def test_criterion_3_vendor_request_contains_no_originals(
    gateway: ChatGateway, vendor: CapturingVendor, redis_client
) -> None:
    reply = gateway.complete(SAMPLE_MESSAGES, "req-criterion-3")

    assert len(vendor.bodies) == 1
    sent = vendor.bodies[0].decode("utf-8")
    for secret in (PHONE_A, PHONE_B, EMAIL_A, IDNO_A):
        assert secret not in sent, "an original value reached the vendor request body"

    payload = json.loads(sent)
    user_content = payload["messages"][-1]["content"]
    assert "[[PHONE_1]]" in user_content
    assert "[[PHONE_2]]" in user_content
    assert "[[EMAIL_1]]" in user_content
    assert "[[IDNO_1]]" in user_content

    request = vendor.requests[0]
    assert request.headers["x-gw-request-id"] == "req-criterion-3"
    assert reply.masked_entity_count == 4
    assert reply.masked is True
    assert redis_client.get(mapping_key("req-criterion-3")) is None


def test_vendor_payload_only_has_masked_text_even_for_system_role(
    gateway: ChatGateway, vendor: CapturingVendor
) -> None:
    messages = [
        {"role": "system", "content": f"紧急联系人 {PHONE_A}"},
        {"role": "user", "content": "hi"},
    ]

    gateway.complete(messages, "req-system-role")

    sent = vendor.bodies[0].decode("utf-8")
    assert PHONE_A not in sent
    assert "[[PHONE_1]]" in sent


def test_vendor_response_placeholders_are_restored(gateway: ChatGateway, vendor: CapturingVendor) -> None:
    reply = gateway.complete(SAMPLE_MESSAGES, "req-restore-vendor")

    assert PHONE_A in reply.content
    assert "[[PHONE_1]]" not in reply.content
    assert reply.content.startswith("已收到")


def test_mapping_is_discarded_when_the_vendor_fails(masking: MaskingService, redis_client) -> None:
    def failing_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": "upstream unavailable"})

    client = VendorClient("http://vendor.test/v1", transport=httpx.MockTransport(failing_handler))
    gateway = ChatGateway(masking, client)
    try:
        with pytest.raises(VendorCallError):
            gateway.complete(SAMPLE_MESSAGES, "req-vendor-fail")
    finally:
        client.close()

    assert redis_client.get(mapping_key("req-vendor-fail")) is None


def test_vendor_client_ignores_a_hostile_proxy_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """A malformed NO_PROXY entry must not break gateway start-up or vendor calls."""

    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:7890")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:7890")
    monkeypatch.setenv("NO_PROXY", "localhost,[::1]")

    client = VendorClient("http://vendor.test/v1", transport=httpx.MockTransport(lambda request: httpx.Response(200, json={})))
    try:
        assert client is not None
    finally:
        client.close()


def test_bypass_sends_original_text_when_masking_is_disabled(
    gateway: ChatGateway, vendor: CapturingVendor, redis_client
) -> None:
    reply = gateway.complete(SAMPLE_MESSAGES, "req-bypass", masking_enabled=False)

    sent = vendor.bodies[0].decode("utf-8")
    assert PHONE_A in sent
    assert reply.masked is False
    assert reply.masked_entity_count == 0
    assert redis_client.get(mapping_key("req-bypass")) is None
    assert reply.response_headers["x-gw-masked-entity-count"] == "0"
