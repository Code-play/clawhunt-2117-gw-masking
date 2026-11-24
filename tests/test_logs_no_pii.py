"""Acceptance criterion 4: application logs never contain originals or the mapping table."""

from __future__ import annotations

import logging
import uuid

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from app import audit
from app.audit import UnsafeAuditDetail
from app.db import make_session_factory
from app.features import FeatureFlagService
from app.gateway import ChatGateway
from app.masking import MaskingService, mapping_key
from app.models import AuditLog

from .conftest import EMAIL_A, IDNO_A, PHONE_A, PHONE_B, SAMPLE_MESSAGES


def _all_log_text(caplog: pytest.LogCaptureFixture) -> str:
    records = [record.getMessage() for record in caplog.records]
    return "\n".join([caplog.text, *records])


def test_criterion_4_full_flow_logs_no_originals_or_mapping(
    caplog: pytest.LogCaptureFixture,
    masking: MaskingService,
    gateway: ChatGateway,
    engine: Engine,
    redis_client,
) -> None:
    """Run mask -> vendor -> flag change -> restore and inspect every log line."""

    caplog.set_level(logging.DEBUG)
    org_id = uuid.uuid4()

    masked = masking.mask(SAMPLE_MESSAGES, "req-criterion-4")
    assert masked.masked_entity_count == 4

    gateway.complete(SAMPLE_MESSAGES, "req-criterion-4-b")
    masking.restore_text("req-criterion-4", "回拨 [[PHONE_1]]")

    factory: sessionmaker[Session] = make_session_factory(engine)
    with factory() as session:
        FeatureFlagService(session).set_flag(
            org_id=org_id,
            site_id=None,
            enabled=False,
            actor_id="gateway-admin@example.invalid",
            reason="vendor incident drill",
        )
        session.commit()

    log_text = _all_log_text(caplog)

    # Negative control: the flow really produced log records.
    assert len(caplog.records) >= 4
    assert "masking applied" in log_text

    for secret in (PHONE_A, PHONE_B, EMAIL_A, IDNO_A):
        assert secret not in log_text, "an original value reached the application log"
    assert "placeholders" not in log_text, "the mapping table structure reached the log"
    assert redis_client.get(mapping_key("req-criterion-4-b")) is None


def test_audit_log_rows_carry_metadata_only(caplog: pytest.LogCaptureFixture, engine: Engine) -> None:
    factory = make_session_factory(engine)
    org_id = uuid.uuid4()
    with factory() as session:
        row, entry = FeatureFlagService(session).set_flag(
            org_id=org_id,
            site_id=None,
            enabled=False,
            actor_id="gateway-admin@example.invalid",
            reason="scheduled maintenance",
        )
        session.commit()

        stored = session.get(AuditLog, entry.id)
        assert stored is not None
        assert stored.action == audit.ACTION_FEATURE_FLAG_UPDATED
        assert stored.detail == {
            "flag_key": "mask_enabled",
            "previous": True,
            "enabled": False,
            "reason": "scheduled maintenance",
        }
        assert row.enabled is False

    log_text = _all_log_text(caplog)
    for secret in (PHONE_A, EMAIL_A):
        assert secret not in log_text


def test_audit_detail_with_personal_data_is_refused(engine: Engine) -> None:
    """Counter-example: metadata containing personal data is an error, not a row."""

    factory = make_session_factory(engine)
    with factory() as session:
        with pytest.raises(UnsafeAuditDetail):
            audit.record(
                session,
                org_id=uuid.uuid4(),
                site_id=None,
                actor_id="gateway-admin@example.invalid",
                action="gw.test",
                entity_type="test",
                detail={"note": f"operator pasted a phone number {PHONE_A}"},
            )
        session.rollback()

        assert session.query(AuditLog).count() == 0
