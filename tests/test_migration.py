"""Alembic migration: offline DDL review plus a real upgrade/downgrade round trip.

The offline test always runs (it renders PostgreSQL DDL without a database).
The round-trip test runs when ``GW_TEST_DATABASE_URL`` points at PostgreSQL 16,
which is how the delivery verification exercises it.
"""

from __future__ import annotations

import io
import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

ROOT = Path(__file__).resolve().parents[1]
PG_URL = "postgresql+psycopg://gw:gw@127.0.0.1:5432/gw"


def _config(url: str) -> Config:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", url)
    return config


def test_offline_upgrade_renders_tables_indexes_and_partial_where(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GW_DATABASE_URL", PG_URL)
    config = _config(PG_URL)
    buffer = io.StringIO()
    config.output_buffer = buffer  # type: ignore[assignment]

    command.upgrade(config, "head", sql=True)
    ddl = buffer.getvalue()

    assert "CREATE TABLE gw_feature_flag" in ddl
    assert "CREATE TABLE gw_audit_log" in ddl
    assert "CREATE UNIQUE INDEX uq_gw_feature_flag_org_scope" in ddl
    assert "WHERE site_id IS NULL" in ddl
    assert "CREATE UNIQUE INDEX uq_gw_feature_flag_site_scope" in ddl
    assert "WHERE site_id IS NOT NULL" in ddl
    assert "CREATE INDEX ix_gw_audit_log_org_created" in ddl
    assert "JSONB" in ddl


def test_offline_downgrade_drops_everything_it_created(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GW_DATABASE_URL", PG_URL)
    config = _config(PG_URL)
    buffer = io.StringIO()
    config.output_buffer = buffer  # type: ignore[assignment]

    command.downgrade(config, "head:base", sql=True)
    ddl = buffer.getvalue()

    assert "DROP TABLE gw_audit_log" in ddl
    assert "DROP TABLE gw_feature_flag" in ddl
    assert "DROP INDEX uq_gw_feature_flag_org_scope" in ddl


@pytest.mark.skipif(
    not os.environ.get("GW_TEST_DATABASE_URL"),
    reason="set GW_TEST_DATABASE_URL to run the real upgrade/downgrade round trip",
)
def test_real_upgrade_downgrade_round_trip(monkeypatch: pytest.MonkeyPatch) -> None:
    url = os.environ["GW_TEST_DATABASE_URL"]
    monkeypatch.setenv("GW_DATABASE_URL", url)
    config = _config(url)
    engine = create_engine(url)

    try:
        command.upgrade(config, "head")
        inspector = inspect(engine)
        tables = set(inspector.get_table_names())
        assert {"gw_feature_flag", "gw_audit_log"} <= tables

        flag_indexes = {index["name"] for index in inspector.get_indexes("gw_feature_flag")}
        assert "uq_gw_feature_flag_org_scope" in flag_indexes
        assert "uq_gw_feature_flag_site_scope" in flag_indexes
        assert "ix_gw_feature_flag_org_key" in flag_indexes

        audit_columns = {column["name"] for column in inspector.get_columns("gw_audit_log")}
        assert {"id", "org_id", "site_id", "created_at", "created_by", "updated_at", "deleted_at"} <= audit_columns
        assert {"action", "entity_type", "entity_id", "actor_id", "detail"} <= audit_columns

        command.downgrade(config, "base")
        remaining = set(inspect(engine).get_table_names())
        assert not ({"gw_feature_flag", "gw_audit_log"} & remaining)
    finally:
        engine.dispose()
        command.upgrade(config, "head")  # leave the scratch database migrated
