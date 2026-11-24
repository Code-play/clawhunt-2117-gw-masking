"""SQLAlchemy models for gateway masking state.

Two tables live in the gateway schema:

``gw_feature_flag``
    One row per ``(org_id, site_id, flag_key)``.  ``mask_enabled`` defaults to
    enabled when no row exists, so a fresh deployment never ships with masking
    turned off.

``gw_audit_log``
    Append-only audit trail for security-relevant gateway actions.  ``detail``
    is metadata only and is validated to contain no recognisable personal data
    (see :mod:`app.audit`).

Both tables follow the platform conventions: UUID primary key, ``org_id`` /
``site_id`` tenancy columns, ``created_at`` / ``created_by`` / ``updated_at`` /
``deleted_at`` (soft delete).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Index, String, Uuid, func, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Declarative base for gateway tables."""


class StandardColumns:
    """Tenancy and lifecycle columns required on every business table."""

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    site_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class FeatureFlag(StandardColumns, Base):
    """Effective gateway feature flag for one tenant scope."""

    __tablename__ = "gw_feature_flag"
    __table_args__ = (
        # PostgreSQL treats NULLs as distinct in a plain unique constraint, so the
        # org-wide row (site_id IS NULL) and the site rows are protected by two
        # partial unique indexes instead.
        Index(
            "uq_gw_feature_flag_org_scope",
            "org_id",
            "flag_key",
            unique=True,
            postgresql_where=text("site_id IS NULL"),
            sqlite_where=text("site_id IS NULL"),
        ),
        Index(
            "uq_gw_feature_flag_site_scope",
            "org_id",
            "site_id",
            "flag_key",
            unique=True,
            postgresql_where=text("site_id IS NOT NULL"),
            sqlite_where=text("site_id IS NOT NULL"),
        ),
        Index("ix_gw_feature_flag_org_key", "org_id", "flag_key"),
    )

    flag_key: Mapped[str] = mapped_column(String(64), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    updated_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    reason: Mapped[str | None] = mapped_column(String(512), nullable=True)


class AuditLog(StandardColumns, Base):
    """Append-only audit entry; ``detail`` carries metadata only."""

    __tablename__ = "gw_audit_log"
    __table_args__ = (
        Index("ix_gw_audit_log_org_created", "org_id", "created_at"),
        Index("ix_gw_audit_log_action_created", "action", "created_at"),
    )

    action: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    actor_id: Mapped[str] = mapped_column(String(128), nullable=False)
    detail: Mapped[dict | None] = mapped_column(JSON, nullable=True)
