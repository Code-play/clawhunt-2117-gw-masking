"""Shared fixtures.

The suite runs against ``fakeredis`` by default and against a real Redis when
``GW_TEST_REDIS_URL`` is set, so the same assertions prove both the logic and
the wire format.  Database-backed tests use SQLite unless
``GW_TEST_DATABASE_URL`` points at PostgreSQL; the Alembic migration itself is
exercised against the configured database (see ``test_migration.py``).
"""

from __future__ import annotations

import base64
import logging
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import Engine

from app.config import Settings
from app.db import make_engine
from app.gateway import ChatGateway, VendorClient
from app.masking import MaskingService, RedisMappingStore
from app.models import Base

# Fictional values only (order requirement: no real personal data).
PHONE_A = "13812345678"
PHONE_B = "13900001111"
EMAIL_A = "zhang.san@example.com"
IDNO_A = "11010519491231002X"

TEST_MASK_KEY = base64.b64encode(bytes(range(32))).decode("ascii")

SAMPLE_MESSAGES: list[dict[str, Any]] = [
    {"role": "system", "content": "You are the member-space assistant."},
    {
        "role": "user",
        "content": (
            f"我的手机是 {PHONE_A}，同事 {PHONE_B}，邮箱 {EMAIL_A}，"
            f"身份证 {IDNO_A}。请先把资料整理成一段摘要。"
        ),
    },
]


class CapturingVendor:
    """httpx transport that records the exact request sent to the vendor."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.bodies: list[bytes] = []
        self.response_content = "已收到，稍后回拨 [[PHONE_1]]。"

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        self.bodies.append(request.content)
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-test",
                "choices": [{"message": {"role": "assistant", "content": self.response_content}}],
            },
        )

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)


@pytest.fixture(autouse=True)
def _mask_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GW_MASK_KEY", TEST_MASK_KEY)
    monkeypatch.setenv("GW_MASK_TTL_SECONDS", "600")
    monkeypatch.setenv("GW_MASK_ENABLED_DEFAULT", "true")
    monkeypatch.setenv("GW_ENV", "test")


@pytest.fixture()
def mask_key() -> str:
    return TEST_MASK_KEY


@pytest.fixture()
def redis_client():
    """Redis client: real instance when configured, fakeredis otherwise."""

    url = os.environ.get("GW_TEST_REDIS_URL")
    if url:
        from redis import Redis

        client = Redis.from_url(url, decode_responses=False)
        client.flushdb()
        yield client
        client.flushdb()
        client.close()
        return

    fakeredis = pytest.importorskip("fakeredis")
    client = fakeredis.FakeRedis(decode_responses=False)
    yield client
    client.flushall()
    client.close()


@pytest.fixture()
def store(redis_client, mask_key: str) -> RedisMappingStore:
    return RedisMappingStore(redis_client, mask_key=mask_key, ttl_seconds=600)


@pytest.fixture()
def masking(store: RedisMappingStore) -> MaskingService:
    return MaskingService(store)


@pytest.fixture()
def vendor() -> CapturingVendor:
    return CapturingVendor()


@pytest.fixture()
def gateway(masking: MaskingService, vendor: CapturingVendor) -> Iterator[ChatGateway]:
    client = VendorClient("http://vendor.test/v1", transport=vendor.transport)
    yield ChatGateway(masking, client)
    client.close()


@pytest.fixture()
def engine(tmp_path: Path) -> Iterator[Engine]:
    created = make_engine(f"sqlite:///{(tmp_path / 'gateway.db').as_posix()}")
    Base.metadata.create_all(created)
    yield created
    created.dispose()


@pytest.fixture()
def settings(engine: Engine, mask_key: str) -> Settings:
    return Settings(
        mask_key=mask_key,
        mask_ttl_seconds=600,
        mask_enabled_default=True,
        database_url=str(engine.url),
        vendor_base_url="http://vendor.test/v1",
        environment="test",
    )


@pytest.fixture()
def log_capture(caplog: pytest.LogCaptureFixture) -> pytest.LogCaptureFixture:
    """Capture every log record at DEBUG level, including library loggers."""

    caplog.set_level(logging.DEBUG)
    return caplog


def assert_no_secret_in_log(log_text: str, secrets: list[str]) -> None:
    for secret in secrets:
        assert secret not in log_text, "a masked original value reached the application log"


def assert_no_secret_in_text(text: str, secrets: list[str]) -> None:
    for secret in secrets:
        assert secret not in text, "a masked original value reached the vendor payload"
