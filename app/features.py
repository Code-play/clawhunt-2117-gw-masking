"""Gateway feature flags.

``mask_enabled`` is enabled by default: a tenant without a row still gets
masking.  Disabling it is a privileged operation (the API requires the gateway
administrator role) and every change is written to ``gw_audit_log``.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import audit
from .models import FeatureFlag

MASK_FLAG = "mask_enabled"


class FeatureFlagService:
    """Read and change gateway feature flags for one tenant scope."""

    def __init__(
        self,
        session: Session,
        *,
        default_enabled: bool = True,
        default_flag: str = MASK_FLAG,
    ) -> None:
        self._session = session
        self._default_enabled = default_enabled
        self._default_flag = default_flag

    # ------------------------------------------------------------------ read

    def get_effective(
        self,
        org_id: uuid.UUID,
        site_id: uuid.UUID | None = None,
        flag_key: str | None = None,
    ) -> bool:
        """Site override wins, then the org-wide row, then the configured default."""

        key = flag_key or self._default_flag
        row = self._find(org_id, site_id, key)
        if row is not None:
            return bool(row.enabled)
        if site_id is not None:
            org_row = self._find(org_id, None, key)
            if org_row is not None:
                return bool(org_row.enabled)
        return self._default_enabled

    def masking_enabled(self, org_id: uuid.UUID, site_id: uuid.UUID | None = None) -> bool:
        return self.get_effective(org_id, site_id, MASK_FLAG)

    def describe(self, org_id: uuid.UUID, site_id: uuid.UUID | None = None) -> dict[str, Any]:
        row = self._find(org_id, site_id, self._default_flag)
        return {
            "flag_key": self._default_flag,
            "enabled": self.masking_enabled(org_id, site_id),
            "explicit": row is not None,
            "updated_at": row.updated_at.isoformat() if row and row.updated_at else None,
            "updated_by": row.updated_by if row else None,
            "reason": row.reason if row else None,
        }

    # ----------------------------------------------------------------- write

    def set_flag(
        self,
        *,
        org_id: uuid.UUID,
        site_id: uuid.UUID | None,
        enabled: bool,
        actor_id: str,
        reason: str | None = None,
        flag_key: str | None = None,
    ) -> tuple[FeatureFlag, Any]:
        """Persist a flag change and write the matching audit row."""

        key = flag_key or self._default_flag
        previous = self.get_effective(org_id, site_id, key)
        now = datetime.now(timezone.utc)
        row = self._find(org_id, site_id, key, include_deleted=True)
        if row is None:
            row = FeatureFlag(
                org_id=org_id,
                site_id=site_id,
                flag_key=key,
                enabled=enabled,
                updated_by=actor_id,
                reason=reason,
                updated_at=now,
            )
            self._session.add(row)
        else:
            row.enabled = enabled
            row.updated_by = actor_id
            row.reason = reason
            row.deleted_at = None
            row.updated_at = now
        self._session.flush()

        entry = audit.record(
            self._session,
            org_id=org_id,
            site_id=site_id,
            actor_id=actor_id,
            action=audit.ACTION_FEATURE_FLAG_UPDATED,
            entity_type="feature_flag",
            entity_id=str(row.id),
            detail={
                "flag_key": key,
                "previous": previous,
                "enabled": enabled,
                "reason": reason,
            },
        )
        return row, entry

    # --------------------------------------------------------------- helpers

    def _find(
        self,
        org_id: uuid.UUID,
        site_id: uuid.UUID | None,
        flag_key: str,
        *,
        include_deleted: bool = False,
    ) -> FeatureFlag | None:
        stmt = select(FeatureFlag).where(
            FeatureFlag.org_id == org_id,
            FeatureFlag.site_id.is_(site_id) if site_id is None else FeatureFlag.site_id == site_id,
            FeatureFlag.flag_key == flag_key,
        )
        if not include_deleted:
            stmt = stmt.where(FeatureFlag.deleted_at.is_(None))
        return self._session.execute(stmt).scalar_one_or_none()
