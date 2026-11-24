"""Model-gateway call path: mask, call the vendor, restore, drop the mapping.

The vendor client receives *only* the masked messages.  ``x_gw.masked_entity_count``
travels with the call as a gateway field (``x_gw_masked_entity_count`` in JSON,
``x-gw-masked-entity-count`` as a header) so the platform can bill and audit
masking volume without seeing the values.  After the vendor responds, the
placeholder tokens in the response are restored to the caller and the mapping
row is deleted immediately.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx

from .masking.service import MaskingService

logger = logging.getLogger("app.gateway")

HEADER_REQUEST_ID = "x-gw-request-id"
HEADER_MASKED_COUNT = "x-gw-masked-entity-count"
RESPONSE_FIELD_MASKED_COUNT = "x_gw"


class VendorCallError(RuntimeError):
    """The vendor call failed; the mapping has already been discarded."""


@dataclass(frozen=True)
class VendorResponse:
    status_code: int
    payload: dict[str, Any]


@dataclass(frozen=True)
class GatewayReply:
    """Result returned to the gateway caller."""

    request_id: str
    content: str
    masked: bool
    masked_entity_count: int
    vendor_status: int
    response_headers: dict[str, str]

    @property
    def custom_fields(self) -> dict[str, Any]:
        """``x_gw.*`` extras attached to the gateway response."""

        return {
            f"{RESPONSE_FIELD_MASKED_COUNT}.masked_entity_count": self.masked_entity_count,
            f"{RESPONSE_FIELD_MASKED_COUNT}.masked": self.masked,
        }


class VendorClient:
    """Minimal OpenAI-compatible chat client used by the gateway."""

    def __init__(
        self,
        base_url: str,
        *,
        model: str = "gw-upstream-default",
        timeout_seconds: float = 10.0,
        transport: httpx.BaseTransport | None = None,
        api_key: str | None = None,
    ) -> None:
        self._model = model
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=timeout_seconds,
            transport=transport,
            headers={"content-type": "application/json"},
        )
        self._api_key = api_key

    def close(self) -> None:
        self._client.close()

    def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        request_id: str,
        masked_entity_count: int,
    ) -> VendorResponse:
        headers = {HEADER_REQUEST_ID: request_id}
        if self._api_key:
            headers["authorization"] = f"Bearer {self._api_key}"
        try:
            response = self._client.post(
                "/chat/completions",
                json={"model": self._model, "messages": messages},
                headers=headers,
            )
        except httpx.HTTPError as exc:  # noqa: TRY003 - message carries no payload
            logger.warning("vendor call failed request_id=%s error=%s", request_id, type(exc).__name__)
            raise VendorCallError(f"vendor call failed: {type(exc).__name__}") from exc
        if response.status_code >= 400:
            logger.warning(
                "vendor rejected request request_id=%s status=%s", request_id, response.status_code
            )
            raise VendorCallError(f"vendor responded with HTTP {response.status_code}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise VendorCallError("vendor response was not JSON") from exc
        if not isinstance(payload, dict):
            raise VendorCallError("vendor response was not a JSON object")
        logger.debug(
            "vendor call ok request_id=%s status=%s masked_entity_count=%d",
            request_id,
            response.status_code,
            masked_entity_count,
        )
        return VendorResponse(status_code=response.status_code, payload=payload)


def _assistant_content(payload: dict[str, Any]) -> str:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise VendorCallError("vendor response has no choices")
    first = choices[0]
    if not isinstance(first, dict):
        raise VendorCallError("vendor response choice is malformed")
    message = first.get("message")
    if not isinstance(message, dict):
        raise VendorCallError("vendor response message is missing")
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            part.get("text", "")
            for part in content
            if isinstance(part, dict) and isinstance(part.get("text"), str)
        ]
        return "".join(parts)
    raise VendorCallError("vendor response message has no text content")


class ChatGateway:
    """Compose masking, the vendor call and restoration."""

    def __init__(self, masking: MaskingService, vendor: VendorClient) -> None:
        self._masking = masking
        self._vendor = vendor

    def complete(
        self,
        messages: object,
        request_id: str,
        *,
        masking_enabled: bool = True,
    ) -> GatewayReply:
        """Run one chat completion with pre-invocation masking.

        ``masking_enabled=False`` is the explicitly audited bypass path: the
        original messages are forwarded untouched and the reply reports
        ``masked=False`` so the caller (and the audit trail) can tell.
        """

        if not masking_enabled:
            logger.warning("masking bypassed by feature flag request_id=%s", request_id)
            response = self._vendor.complete(
                _require_messages(messages), request_id=request_id, masked_entity_count=0
            )
            return GatewayReply(
                request_id=request_id,
                content=_assistant_content(response.payload),
                masked=False,
                masked_entity_count=0,
                vendor_status=response.status_code,
                response_headers={
                    HEADER_REQUEST_ID: request_id,
                    HEADER_MASKED_COUNT: "0",
                },
            )

        result = self._masking.mask(messages, request_id)
        assert isinstance(result.messages, list)
        try:
            response = self._vendor.complete(
                result.messages,
                request_id=request_id,
                masked_entity_count=result.masked_entity_count,
            )
            restored = self._masking.restore_text(request_id, _assistant_content(response.payload))
        except Exception:
            # Never leave a mapping behind for a call that will not complete.
            self._masking.discard(request_id)
            raise
        return GatewayReply(
            request_id=request_id,
            content=restored,
            masked=True,
            masked_entity_count=result.masked_entity_count,
            vendor_status=response.status_code,
            response_headers={
                HEADER_REQUEST_ID: request_id,
                HEADER_MASKED_COUNT: str(result.masked_entity_count),
            },
        )


def _require_messages(messages: object) -> list[dict[str, Any]]:
    if not isinstance(messages, list) or not messages:
        raise ValueError("messages must be a non-empty list")
    return messages
