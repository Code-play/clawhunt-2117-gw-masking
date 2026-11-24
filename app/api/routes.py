"""Gateway masking HTTP surface.

Endpoints
---------
``POST /api/v1/gw/mask``                  mask messages, return placeholders and hit count
``POST /api/v1/gw/mask/restore``          restore one text and delete the mapping
``POST /api/v1/gw/mask/restore-messages`` restore every text slot and delete the mapping
``GET  /api/v1/gw/features/{flag}``       effective feature flag for the tenant scope
``PATCH /api/v1/gw/features/{flag}``      administrator-only change, audited
``POST /api/v1/gw/chat/completions``      mask -> vendor -> restore (the gateway call path)

Only ``mask_enabled`` is exposed; unknown flags answer 404 rather than silently
inventing a feature surface.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from ..features import MASK_FLAG, FeatureFlagService
from .deps import Principal, get_context, get_db_session, get_masking, require_admin, require_tenant
from .schemas import (
    ChatCompletionRequest,
    ChatCompletionResponse,
    FeatureFlagResponse,
    FeatureFlagUpdate,
    FeatureFlagUpdateResponse,
    MaskRequest,
    MaskResponse,
    PlaceholderRef,
    RestoreMessagesRequest,
    RestoreMessagesResponse,
    RestoreTextRequest,
    RestoreTextResponse,
)

router = APIRouter(prefix="/api/v1/gw", tags=["gateway-masking"])

SUPPORTED_FLAGS = frozenset({MASK_FLAG})


def _flag_service(session: Session, context: Any) -> FeatureFlagService:
    return FeatureFlagService(session, default_enabled=context.settings.mask_enabled_default)


def _messages_payload(messages: list[Any]) -> list[dict[str, Any]]:
    return [message.model_dump(exclude_none=True) for message in messages]


def _new_request_id() -> str:
    return uuid.uuid4().hex


@router.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.post("/mask", response_model=MaskResponse)
def mask_messages(
    payload: MaskRequest,
    masking=Depends(get_masking),
) -> MaskResponse:
    request_id = payload.request_id or _new_request_id()
    result = masking.mask(_messages_payload(payload.messages), request_id)
    return MaskResponse(
        request_id=result.request_id,
        masked_messages=result.messages,
        masked_entity_count=result.masked_entity_count,
        distinct_values=result.distinct_values,
        placeholders=[
            PlaceholderRef(placeholder=token, type=kind)
            for token, kind in sorted(result.placeholder_kinds.items())
        ],
    )


@router.post("/mask/restore", response_model=RestoreTextResponse)
def restore_text(
    payload: RestoreTextRequest,
    masking=Depends(get_masking),
) -> RestoreTextResponse:
    restored = masking.restore_text(payload.request_id, payload.text)
    return RestoreTextResponse(request_id=payload.request_id, text=restored)


@router.post("/mask/restore-messages", response_model=RestoreMessagesResponse)
def restore_messages(
    payload: RestoreMessagesRequest,
    masking=Depends(get_masking),
) -> RestoreMessagesResponse:
    restored = masking.restore_messages(payload.request_id, _messages_payload(payload.messages))
    return RestoreMessagesResponse(request_id=payload.request_id, messages=restored)


@router.get("/features/{flag_key}", response_model=FeatureFlagResponse)
def get_feature_flag(
    flag_key: str,
    principal: Principal = Depends(require_tenant),
    session: Session = Depends(get_db_session),
    context=Depends(get_context),
) -> FeatureFlagResponse:
    if flag_key not in SUPPORTED_FLAGS:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="unknown feature flag")
    described = _flag_service(session, context).describe(principal.org_id, principal.site_id)
    return FeatureFlagResponse(**described)


@router.patch("/features/{flag_key}", response_model=FeatureFlagUpdateResponse)
def update_feature_flag(
    flag_key: str,
    payload: FeatureFlagUpdate,
    principal: Principal = Depends(require_admin),
    session: Session = Depends(get_db_session),
    context=Depends(get_context),
) -> FeatureFlagUpdateResponse:
    if flag_key not in SUPPORTED_FLAGS:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="unknown feature flag")
    service = _flag_service(session, context)
    row, entry = service.set_flag(
        org_id=principal.org_id,
        site_id=principal.site_id,
        enabled=payload.enabled,
        actor_id=principal.actor_id,
        reason=payload.reason,
        flag_key=flag_key,
    )
    session.commit()
    described = service.describe(principal.org_id, principal.site_id)
    return FeatureFlagUpdateResponse(
        **described,
        audit_id=str(entry.id),
        audit_action=entry.action,
    )


@router.post("/chat/completions", response_model=ChatCompletionResponse)
def chat_completions(
    payload: ChatCompletionRequest,
    response: Response,
    principal: Principal = Depends(require_tenant),
    session: Session = Depends(get_db_session),
    context=Depends(get_context),
) -> ChatCompletionResponse:
    enabled = _flag_service(session, context).masking_enabled(principal.org_id, principal.site_id)
    request_id = payload.request_id or _new_request_id()
    reply = context.gateway.complete(
        _messages_payload(payload.messages),
        request_id,
        masking_enabled=enabled,
    )
    for name, value in reply.response_headers.items():
        response.headers[name] = value
    return ChatCompletionResponse(
        request_id=reply.request_id,
        content=reply.content,
        x_gw={
            "masked_entity_count": reply.masked_entity_count,
            "masked": reply.masked,
        },
    )
