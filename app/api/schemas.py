"""Request and response schemas for the gateway masking API."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ChatMessage(BaseModel):
    """One chat message, in the OpenAI chat shape."""

    model_config = ConfigDict(extra="allow")

    role: str = Field(min_length=1, max_length=32)
    content: str | list[dict[str, Any]] | None = None
    name: str | None = None


class MaskRequest(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1)
    request_id: str | None = Field(default=None, max_length=128)


class PlaceholderRef(BaseModel):
    placeholder: str
    type: str


class MaskResponse(BaseModel):
    request_id: str
    masked_messages: list[dict[str, Any]]
    masked_entity_count: int
    distinct_values: int
    placeholders: list[PlaceholderRef]


class RestoreTextRequest(BaseModel):
    request_id: str
    text: str


class RestoreTextResponse(BaseModel):
    request_id: str
    text: str


class RestoreMessagesRequest(BaseModel):
    request_id: str
    messages: list[ChatMessage] = Field(min_length=1)


class RestoreMessagesResponse(BaseModel):
    request_id: str
    messages: list[dict[str, Any]]


class FeatureFlagResponse(BaseModel):
    flag_key: str
    enabled: bool
    explicit: bool
    updated_at: str | None = None
    updated_by: str | None = None
    reason: str | None = None


class FeatureFlagUpdate(BaseModel):
    enabled: bool
    reason: str | None = Field(default=None, max_length=512)


class FeatureFlagUpdateResponse(BaseModel):
    flag_key: str
    enabled: bool
    explicit: bool
    updated_at: str | None = None
    updated_by: str | None = None
    reason: str | None = None
    audit_id: str
    audit_action: str


class ChatCompletionRequest(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1)
    request_id: str | None = Field(default=None, max_length=128)


class ChatCompletionResponse(BaseModel):
    request_id: str
    content: str
    x_gw: dict[str, Any]
