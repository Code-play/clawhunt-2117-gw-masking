"""Append-only audit trail for gateway security actions.

The audit trail must prove *who* disabled ``mask_enabled`` without ever storing
what was masked.  ``detail`` therefore accepts metadata only: before a row is
added the serialised detail is run through the same recognisers as the masking
pass, and a hit aborts the write.  That turns "do not write original values to
logs" into an enforced invariant instead of a review convention.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from sqlalchemy.orm import Session

from .masking.recognizers import find_entities
from .models import AuditLog

logger = logging.getLogger("app.audit")

ACTION_FEATURE_FLAG_UPDATED = "gw.feature_flag.updated"
ACTION_MASKING_BYPASSED = "gw.masking.bypassed"


class UnsafeAuditDetail(ValueError):
    """Raised when an audit detail payload contains recognisable personal data."""


def assert_safe_detail(detail: Any) -> None:
    """Reject audit metadata that contains a phone number, ID number or e-mail."""

    serialised = json.dumps(detail, ensure_ascii=False, default=str)
    if find_entities(serialised):
        raise UnsafeAuditDetail(
            "audit detail must not contain personal data; record identifiers and counts only"
        )


def record(
    session: Session,
    *,
    org_id: uuid.UUID,
    site_id: uuid.UUID | None,
    actor_id: str,
    action: str,
    entity_type: str,
    entity_id: str | None = None,
    detail: dict[str, Any] | None = None,
) -> AuditLog:
    """Add one audit row and flush it (the caller owns the transaction)."""

    if detail is not None:
        assert_safe_detail(detail)
    entry = AuditLog(
        org_id=org_id,
        site_id=site_id,
        actor_id=actor_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        detail=detail,
    )
    session.add(entry)
    session.flush()
    logger.info(
        "audit action=%s entity_type=%s entity_id=%s org_id=%s site_id=%s actor=%s",
        action,
        entity_type,
        entity_id or "-",
        org_id,
        site_id or "-",
        actor_id,
    )
    return entry
