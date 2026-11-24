"""FastAPI dependencies: app context, sessions, principal, tenant scope."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from ..db import session_scope
from ..masking.service import MaskingService

ADMIN_ROLE = "gateway_admin"
DEFAULT_ROLE = "gateway_integration"

HEADER_ACTOR_ID = "x-gw-actor-id"
HEADER_ACTOR_ROLE = "x-gw-actor-role"
HEADER_ORG_ID = "x-org-id"
HEADER_SITE_ID = "x-site-id"


@dataclass(frozen=True)
class Principal:
    """Caller identity and tenant scope.

    Real authentication lives in the platform's access-control ticket; this
    gateway deliberately only consumes the headers that ticket will set, so the
    masking code can be integrated without a dependency on it (order: use stubs
    first and document the integration point).
    """

    actor_id: str
    role: str
    org_id: uuid.UUID | None = None
    site_id: uuid.UUID | None = None

    @property
    def is_admin(self) -> bool:
        return self.role == ADMIN_ROLE


def get_context(request: Request):  # type: ignore[no-untyped-def]
    return request.app.state.context


def get_masking(context=Depends(get_context)) -> MaskingService:
    return context.masking


def get_db_session(context=Depends(get_context)) -> Iterator[Session]:
    with session_scope(context.session_factory) as session:
        yield session


def _parse_uuid(raw: str | None, header: str) -> uuid.UUID | None:
    if raw is None or not raw.strip():
        return None
    try:
        return uuid.UUID(raw.strip())
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{header} must be a UUID",
        ) from exc


def get_principal(request: Request) -> Principal:
    actor = (request.headers.get(HEADER_ACTOR_ID) or "").strip()
    role = (request.headers.get(HEADER_ACTOR_ROLE) or DEFAULT_ROLE).strip()
    return Principal(
        actor_id=actor or "gateway-service",
        role=role,
        org_id=_parse_uuid(request.headers.get(HEADER_ORG_ID), HEADER_ORG_ID),
        site_id=_parse_uuid(request.headers.get(HEADER_SITE_ID), HEADER_SITE_ID),
    )


def require_tenant(principal: Principal = Depends(get_principal)) -> Principal:
    if principal.org_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{HEADER_ORG_ID} header is required for this endpoint",
        )
    return principal


def require_admin(principal: Principal = Depends(get_principal)) -> Principal:
    if not principal.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"{ADMIN_ROLE} role is required to change gateway feature flags",
        )
    if principal.org_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{HEADER_ORG_ID} header is required for this endpoint",
        )
    return principal
