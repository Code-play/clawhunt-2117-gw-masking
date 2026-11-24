"""Gateway masking tables: feature flags and the security audit trail.

Revision ID: 0001_gw_masking
Revises:
Create Date: 2026-11-24
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_gw_masking"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.create_table(
        "gw_feature_flag",
        sa.Column("id", UUID, nullable=False),
        sa.Column("org_id", UUID, nullable=False),
        sa.Column("site_id", UUID, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", UUID, nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("flag_key", sa.String(length=64), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("updated_by", sa.String(length=128), nullable=True),
        sa.Column("reason", sa.String(length=512), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_gw_feature_flag"),
    )
    op.create_index("ix_gw_feature_flag_org_id", "gw_feature_flag", ["org_id"])
    op.create_index("ix_gw_feature_flag_site_id", "gw_feature_flag", ["site_id"])
    op.create_index("ix_gw_feature_flag_org_key", "gw_feature_flag", ["org_id", "flag_key"])
    op.create_index(
        "uq_gw_feature_flag_org_scope",
        "gw_feature_flag",
        ["org_id", "flag_key"],
        unique=True,
        postgresql_where=sa.text("site_id IS NULL"),
    )
    op.create_index(
        "uq_gw_feature_flag_site_scope",
        "gw_feature_flag",
        ["org_id", "site_id", "flag_key"],
        unique=True,
        postgresql_where=sa.text("site_id IS NOT NULL"),
    )

    op.create_table(
        "gw_audit_log",
        sa.Column("id", UUID, nullable=False),
        sa.Column("org_id", UUID, nullable=False),
        sa.Column("site_id", UUID, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", UUID, nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("entity_type", sa.String(length=64), nullable=False),
        sa.Column("entity_id", sa.String(length=64), nullable=True),
        sa.Column("actor_id", sa.String(length=128), nullable=False),
        sa.Column("detail", JSONB, nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_gw_audit_log"),
    )
    op.create_index("ix_gw_audit_log_org_id", "gw_audit_log", ["org_id"])
    op.create_index("ix_gw_audit_log_site_id", "gw_audit_log", ["site_id"])
    op.create_index("ix_gw_audit_log_org_created", "gw_audit_log", ["org_id", "created_at"])
    op.create_index("ix_gw_audit_log_action_created", "gw_audit_log", ["action", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_gw_audit_log_action_created", table_name="gw_audit_log")
    op.drop_index("ix_gw_audit_log_org_created", table_name="gw_audit_log")
    op.drop_index("ix_gw_audit_log_site_id", table_name="gw_audit_log")
    op.drop_index("ix_gw_audit_log_org_id", table_name="gw_audit_log")
    op.drop_table("gw_audit_log")

    op.drop_index("uq_gw_feature_flag_site_scope", table_name="gw_feature_flag")
    op.drop_index("uq_gw_feature_flag_org_scope", table_name="gw_feature_flag")
    op.drop_index("ix_gw_feature_flag_org_key", table_name="gw_feature_flag")
    op.drop_index("ix_gw_feature_flag_site_id", table_name="gw_feature_flag")
    op.drop_index("ix_gw_feature_flag_org_id", table_name="gw_feature_flag")
    op.drop_table("gw_feature_flag")
