"""Application factory for the gateway masking service.

The factory accepts injected infrastructure (Redis client, SQLAlchemy engine,
httpx transport) so the same code builds the production app and the test app
without a module-level singleton.  All configuration comes from
:func:`app.config.load_settings`.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from ..audit import UnsafeAuditDetail
from ..config import Settings, load_settings
from ..db import make_engine, make_session_factory
from ..gateway import ChatGateway, VendorCallError, VendorClient
from ..masking import (
    InvalidMaskingInput,
    MaskKeyError,
    MaskingService,
    MappingDecryptError,
    MappingNotFoundError,
    PlaceholderCollisionError,
    RedisMappingStore,
)
from .routes import router

logger = logging.getLogger("app")

DEFAULT_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"


@dataclass
class AppContext:
    """Infrastructure shared by the request handlers."""

    settings: Settings
    masking: MaskingService
    store: RedisMappingStore
    engine: Engine
    session_factory: sessionmaker[Session]
    gateway: ChatGateway


def configure_logging(level: str | None = None) -> None:
    """Install a metadata-only log format.

    Masking code logs counts and identifiers, never message content, so the
    format only needs the standard fields.  A level can be raised with
    ``GW_LOG_LEVEL`` for troubleshooting without changing the code.
    """

    if logging.getLogger().handlers:
        logging.getLogger().setLevel(level or os.environ.get("GW_LOG_LEVEL", "INFO"))
        return
    logging.basicConfig(
        level=level or os.environ.get("GW_LOG_LEVEL", "INFO"),
        format=DEFAULT_LOG_FORMAT,
    )


def _error_response(status_code: int, message: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"detail": {"error": message}})


def _install_exception_handlers(app: FastAPI) -> None:
    async def masking_not_found(_request: Request, _exc: Exception) -> JSONResponse:
        return _error_response(410, "mapping not found or already restored")

    async def masking_bad_input(_request: Request, exc: Exception) -> JSONResponse:
        return _error_response(422, str(exc))

    async def masking_integrity(_request: Request, _exc: Exception) -> JSONResponse:
        return _error_response(500, "mapping cannot be authenticated")

    async def masking_unavailable(_request: Request, _exc: Exception) -> JSONResponse:
        return _error_response(503, "masking is not configured correctly")

    async def vendor_failed(_request: Request, exc: Exception) -> JSONResponse:
        return _error_response(502, str(exc))

    async def unsafe_audit(_request: Request, _exc: Exception) -> JSONResponse:
        return _error_response(500, "audit payload rejected")

    app.add_exception_handler(MappingNotFoundError, masking_not_found)  # type: ignore[arg-type]
    app.add_exception_handler(InvalidMaskingInput, masking_bad_input)  # type: ignore[arg-type]
    app.add_exception_handler(PlaceholderCollisionError, masking_bad_input)  # type: ignore[arg-type]
    app.add_exception_handler(MappingDecryptError, masking_integrity)  # type: ignore[arg-type]
    app.add_exception_handler(MaskKeyError, masking_unavailable)  # type: ignore[arg-type]
    app.add_exception_handler(VendorCallError, vendor_failed)  # type: ignore[arg-type]
    app.add_exception_handler(UnsafeAuditDetail, unsafe_audit)  # type: ignore[arg-type]


def create_app(
    *,
    settings: Settings | None = None,
    redis_client=None,  # type: ignore[no-untyped-def]
    engine: Engine | None = None,
    vendor_transport=None,  # type: ignore[no-untyped-def]
) -> FastAPI:
    """Build the FastAPI application."""

    resolved = settings or load_settings()
    configure_logging()
    if redis_client is None:
        from redis import Redis

        redis_client = Redis.from_url(resolved.redis_url, decode_responses=False)

    store = RedisMappingStore(
        redis_client,
        mask_key=resolved.mask_key,
        ttl_seconds=resolved.mask_ttl_seconds,
    )
    masking = MaskingService(store)
    resolved_engine = engine or make_engine(resolved.database_url)
    factory = make_session_factory(resolved_engine)
    vendor = VendorClient(
        resolved.vendor_base_url,
        timeout_seconds=resolved.vendor_timeout_seconds,
        transport=vendor_transport,
    )
    context = AppContext(
        settings=resolved,
        masking=masking,
        store=store,
        engine=resolved_engine,
        session_factory=factory,
        gateway=ChatGateway(masking, vendor),
    )

    app = FastAPI(
        title="Member Space Platform · Gateway Masking",
        version="0.1.0",
        description="Pre-invocation masking with request-level encrypted mapping table",
    )
    app.state.context = context
    app.include_router(router)
    _install_exception_handlers(app)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    logger.info("gateway masking app created settings=%s", resolved.redacted())
    return app
