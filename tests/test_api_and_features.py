"""HTTP surface: masking, restoration, feature flag and the gateway call path."""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from app.api.main import create_app
from app.config import Settings
from app.models import AuditLog

from .conftest import EMAIL_A, IDNO_A, PHONE_A, PHONE_B, SAMPLE_MESSAGES, CapturingVendor

ORG_ID = uuid.UUID("0b6f7c1e-1a2b-4c3d-8e9f-0a1b2c3d4e5f")
SITE_ID = uuid.UUID("5d2a8b3c-4d5e-4f60-8a71-2b3c4d5e6f70")


def tenant_headers(
    *, role: str = "gateway_integration", actor: str = "svc-gateway"
) -> dict[str, str]:
    return {
        "X-Org-Id": str(ORG_ID),
        "X-Site-Id": str(SITE_ID),
        "X-GW-Actor-Id": actor,
        "X-GW-Actor-Role": role,
    }


@pytest.fixture()
def org_id() -> uuid.UUID:
    return ORG_ID


@pytest.fixture()
def client(
    settings: Settings,
    redis_client,
    engine: Engine,
    vendor: CapturingVendor,
) -> Iterator[TestClient]:
    app = create_app(
        settings=settings,
        redis_client=redis_client,
        engine=engine,
        vendor_transport=vendor.transport,
    )
    with TestClient(app) as test_client:
        yield test_client


def test_mask_endpoint_returns_placeholders_and_count(client: TestClient) -> None:
    response = client.post("/api/v1/gw/mask", json={"messages": SAMPLE_MESSAGES})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["masked_entity_count"] == 4
    assert body["distinct_values"] == 4
    tokens = {item["placeholder"]: item["type"] for item in body["placeholders"]}
    assert tokens["[[PHONE_1]]"] == "PHONE"
    assert tokens["[[EMAIL_1]]"] == "EMAIL"
    assert tokens["[[IDNO_1]]"] == "IDNO"
    text = str(body["masked_messages"])
    for secret in (PHONE_A, PHONE_B, EMAIL_A, IDNO_A):
        assert secret not in text
    assert body["masked_messages"][0]["role"] == "system"
    assert "占位符" in body["masked_messages"][0]["content"]


def test_mask_rejects_reserved_tokens(client: TestClient) -> None:
    response = client.post(
        "/api/v1/gw/mask",
        json={"messages": [{"role": "user", "content": "literal [[PHONE_1]]"}]},
    )

    assert response.status_code == 422
    assert PHONE_A not in response.text


def test_restore_endpoint_deletes_mapping(client: TestClient) -> None:
    masked = client.post(
        "/api/v1/gw/mask",
        json={"messages": [{"role": "user", "content": PHONE_A}], "request_id": "req-api-restore"},
    ).json()
    assert masked["masked_messages"][-1]["content"] == "[[PHONE_1]]"

    restored = client.post(
        "/api/v1/gw/mask/restore",
        json={"request_id": "req-api-restore", "text": "回拨 [[PHONE_1]]"},
    )
    assert restored.status_code == 200
    assert restored.json()["text"] == f"回拨 {PHONE_A}"

    again = client.post(
        "/api/v1/gw/mask/restore",
        json={"request_id": "req-api-restore", "text": "回拨 [[PHONE_1]]"},
    )
    assert again.status_code == 410


def test_restore_messages_endpoint(client: TestClient) -> None:
    client.post(
        "/api/v1/gw/mask",
        json={"messages": [{"role": "user", "content": EMAIL_A}], "request_id": "req-api-msgs"},
    )

    response = client.post(
        "/api/v1/gw/mask/restore-messages",
        json={"request_id": "req-api-msgs", "messages": [{"role": "assistant", "content": "ok [[EMAIL_1]]"}]},
    )

    assert response.status_code == 200
    assert response.json()["messages"][0]["content"] == f"ok {EMAIL_A}"


def test_unknown_feature_flag_is_404(client: TestClient) -> None:
    response = client.get("/api/v1/gw/features/other_flag", headers=tenant_headers())

    assert response.status_code == 404


def test_mask_enabled_defaults_to_true(client: TestClient) -> None:
    response = client.get("/api/v1/gw/features/mask_enabled", headers=tenant_headers())

    assert response.status_code == 200
    body = response.json()
    assert body["flag_key"] == "mask_enabled"
    assert body["enabled"] is True
    assert body["explicit"] is False


def test_feature_flag_change_requires_admin(client: TestClient, engine: Engine) -> None:
    response = client.patch(
        "/api/v1/gw/features/mask_enabled",
        json={"enabled": False, "reason": "unauthorised attempt"},
        headers=tenant_headers(role="gateway_integration"),
    )

    assert response.status_code == 403
    from app.db import make_session_factory

    with make_session_factory(engine)() as session:
        assert session.query(AuditLog).count() == 0


def test_admin_can_disable_masking_and_the_change_is_audited(client: TestClient, engine: Engine) -> None:
    response = client.patch(
        "/api/v1/gw/features/mask_enabled",
        json={"enabled": False, "reason": "vendor incident drill"},
        headers=tenant_headers(role="gateway_admin", actor="gateway-admin@example.invalid"),
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["enabled"] is False
    assert body["explicit"] is True
    assert body["audit_action"] == "gw.feature_flag.updated"

    from app.db import make_session_factory
    from app.models import FeatureFlag

    with make_session_factory(engine)() as session:
        audit_row = session.get(AuditLog, uuid.UUID(body["audit_id"]))
        assert audit_row is not None
        assert audit_row.actor_id == "gateway-admin@example.invalid"
        assert audit_row.detail["enabled"] is False
        assert audit_row.detail["previous"] is True
        flag = session.query(FeatureFlag).one()
        assert flag.enabled is False
        assert flag.reason == "vendor incident drill"

    assert (
        client.get("/api/v1/gw/features/mask_enabled", headers=tenant_headers())
        .json()["enabled"]
        is False
    )


def test_chat_completions_masks_by_default(
    client: TestClient, vendor: CapturingVendor, redis_client
) -> None:
    response = client.post(
        "/api/v1/gw/chat/completions",
        json={"messages": SAMPLE_MESSAGES},
        headers=tenant_headers(),
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["x_gw"]["masked"] is True
    assert body["x_gw"]["masked_entity_count"] == 4
    assert response.headers["x-gw-masked-entity-count"] == "4"
    assert PHONE_A in body["content"]

    sent = vendor.bodies[0].decode("utf-8")
    for secret in (PHONE_A, PHONE_B, EMAIL_A, IDNO_A):
        assert secret not in sent
    assert redis_client.get(f"gw:mask:{body['request_id']}") is None


def test_chat_completions_bypasses_when_flag_disabled(
    client: TestClient, vendor: CapturingVendor
) -> None:
    disable = client.patch(
        "/api/v1/gw/features/mask_enabled",
        json={"enabled": False, "reason": "drill"},
        headers=tenant_headers(role="gateway_admin", actor="gateway-admin@example.invalid"),
    )
    assert disable.status_code == 200

    response = client.post(
        "/api/v1/gw/chat/completions",
        json={"messages": [{"role": "user", "content": f"call {PHONE_A}"}]},
        headers=tenant_headers(),
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["x_gw"]["masked"] is False
    assert body["x_gw"]["masked_entity_count"] == 0
    assert response.headers["x-gw-masked-entity-count"] == "0"
    assert PHONE_A in vendor.bodies[0].decode("utf-8")


def test_chat_completions_requires_org_header(client: TestClient) -> None:
    response = client.post(
        "/api/v1/gw/chat/completions",
        json={"messages": [{"role": "user", "content": "hi"}]},
    )

    assert response.status_code == 400
    assert "x-org-id" in response.text.lower()


def test_invalid_request_id_is_422(client: TestClient) -> None:
    response = client.post(
        "/api/v1/gw/mask",
        json={"messages": [{"role": "user", "content": "hi"}], "request_id": "bad id"},
    )

    assert response.status_code == 422


def test_healthz(client: TestClient) -> None:
    assert client.get("/healthz").json() == {"status": "ok"}
