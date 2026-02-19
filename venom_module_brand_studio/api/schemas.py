from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

PublishStatus = Literal["draft", "ready", "queued", "published", "failed", "cancelled"]


class OpportunityScoreBreakdown(BaseModel):
    relevance: float = Field(..., ge=0.0, le=1.0)
    timeliness: float = Field(..., ge=0.0, le=1.0)
    authority_fit: float = Field(..., ge=0.0, le=1.0)
    risk_penalty: float = Field(..., ge=0.0, le=1.0)
    final_score: float = Field(..., ge=0.0, le=1.0)
    reasons: list[str] = Field(default_factory=list)


class ContentCandidate(BaseModel):
    id: str
    source: str
    url: str
    topic: str
    summary: str
    language: Literal["pl", "en", "other"]
    score: float = Field(..., ge=0.0, le=1.0)
    age_minutes: int = Field(..., ge=0)
    score_breakdown: OpportunityScoreBreakdown
    reasons: list[str] = Field(default_factory=list)


class CandidatesResponse(BaseModel):
    status: Literal["ok"] = "ok"
    count: int
    items: list[ContentCandidate]
    refreshed_at: datetime


class DraftGenerateRequest(BaseModel):
    candidate_id: str
    channels: list[Literal["x", "github", "blog"]] = Field(min_length=1)
    languages: list[Literal["pl", "en"]] = Field(min_length=1)
    tone: Literal["neutral", "expert", "short", "cta"] | None = None


class DraftVariant(BaseModel):
    channel: Literal["x", "github", "blog"]
    language: Literal["pl", "en"]
    content: str


class DraftBundle(BaseModel):
    draft_id: str
    candidate_id: str
    variants: list[DraftVariant]


class QueueDraftRequest(BaseModel):
    target_channel: Literal["x", "github", "blog"]
    target_repo: str | None = None
    target_path: str | None = None
    payload_override: str | None = None


class QueueCreateResponse(BaseModel):
    item_id: str
    status: PublishStatus
    created_at: datetime


class PublishRequest(BaseModel):
    confirm_publish: bool


class PublishResult(BaseModel):
    success: bool
    status: PublishStatus
    published_at: datetime | None = None
    external_id: str | None = None
    url: str | None = None
    message: str


class PublishQueueItem(BaseModel):
    item_id: str
    draft_id: str
    target_channel: Literal["x", "github", "blog"]
    status: PublishStatus
    created_at: datetime
    updated_at: datetime


class QueueResponse(BaseModel):
    count: int
    items: list[PublishQueueItem]


class BrandStudioAuditEntry(BaseModel):
    id: str
    actor: str
    action: str
    status: str
    payload_hash: str
    timestamp: datetime
    details: str | None = None


class AuditResponse(BaseModel):
    count: int
    items: list[BrandStudioAuditEntry]
