from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

PublishStatus = Literal["draft", "ready", "queued", "published", "failed", "cancelled"]
DiscoveryMode = Literal["stub", "hybrid", "live"]
BrandChannel = Literal[
    "x",
    "github",
    "blog",
    "linkedin",
    "medium",
    "hf_blog",
    "hf_spaces",
    "reddit",
    "devto",
    "hashnode",
]
ChannelId = BrandChannel
DraftLanguage = Literal["pl", "en"]
IntegrationStatus = Literal["configured", "missing", "invalid"]
IntegrationId = Literal[
    "github_publish",
    "rss",
    "hn",
    "arxiv",
    "x",
    "devto_publish",
    "reddit_publish",
    "hashnode_publish",
    "linkedin_publish",
    "medium_publish",
    "hf_blog_publish",
    "hf_spaces_publish",
]


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
    channels: list[BrandChannel] = Field(min_length=1)
    languages: list[DraftLanguage] = Field(min_length=1)
    tone: Literal["neutral", "expert", "short", "cta"] | None = None


class DraftVariant(BaseModel):
    channel: BrandChannel
    language: DraftLanguage
    content: str


class DraftBundle(BaseModel):
    draft_id: str
    candidate_id: str
    variants: list[DraftVariant]


class QueueDraftRequest(BaseModel):
    target_channel: BrandChannel
    account_id: str | None = None
    target: str | None = None
    # Deprecated compatibility field (use `target`).
    target_repo: str | None = None
    target_path: str | None = None
    target_language: DraftLanguage | None = None
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
    target_channel: BrandChannel
    target_language: DraftLanguage | None = None
    target: str | None = None
    # Deprecated compatibility field (use `target`).
    target_repo: str | None = None
    target_path: str | None = None
    account_id: str | None = None
    account_display_name: str | None = None
    payload: str = ""
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


class StrategyConfig(BaseModel):
    id: str
    name: str = Field(min_length=1)
    discovery_mode: DiscoveryMode
    rss_urls: list[str] = Field(default_factory=list)
    topic_keywords: list[str] = Field(default_factory=list)
    cache_ttl_seconds: int = Field(ge=30, le=86400)
    min_score: float = Field(ge=0.0, le=1.0)
    limit: int = Field(ge=1, le=200)
    active_channels: list[BrandChannel] = Field(min_length=1)
    draft_languages: list[DraftLanguage] = Field(min_length=1)
    default_accounts: dict[BrandChannel, str] = Field(default_factory=dict)


class ConfigResponse(BaseModel):
    active_strategy_id: str
    active_strategy: StrategyConfig


class ConfigUpdateRequest(BaseModel):
    discovery_mode: DiscoveryMode | None = None
    rss_urls: list[str] | None = None
    topic_keywords: list[str] | None = None
    cache_ttl_seconds: int | None = Field(default=None, ge=30, le=86400)
    min_score: float | None = Field(default=None, ge=0.0, le=1.0)
    limit: int | None = Field(default=None, ge=1, le=200)
    active_channels: list[BrandChannel] | None = Field(default=None, min_length=1)
    draft_languages: list[DraftLanguage] | None = Field(default=None, min_length=1)
    default_accounts: dict[BrandChannel, str] | None = None


class RefreshResponse(BaseModel):
    status: Literal["ok"] = "ok"
    refreshed_at: datetime
    count: int


class StrategiesResponse(BaseModel):
    active_strategy_id: str
    items: list[StrategyConfig]


class StrategyFieldsBase(BaseModel):
    discovery_mode: DiscoveryMode | None = None
    rss_urls: list[str] | None = None
    topic_keywords: list[str] | None = None
    cache_ttl_seconds: int | None = Field(default=None, ge=30, le=86400)
    min_score: float | None = Field(default=None, ge=0.0, le=1.0)
    limit: int | None = Field(default=None, ge=1, le=200)
    active_channels: list[BrandChannel] | None = Field(default=None, min_length=1)
    draft_languages: list[DraftLanguage] | None = Field(default=None, min_length=1)
    default_accounts: dict[BrandChannel, str] | None = None


class StrategyCreateRequest(StrategyFieldsBase):
    name: str = Field(min_length=1)
    base_strategy_id: str | None = None


class StrategyUpdateRequest(StrategyFieldsBase):
    name: str | None = Field(default=None, min_length=1)


class StrategyResponse(BaseModel):
    item: StrategyConfig
    active_strategy_id: str


class IntegrationDescriptor(BaseModel):
    id: IntegrationId
    name: str
    requires_key: bool
    status: IntegrationStatus
    details: str
    key_hint: str | None = None
    masked_secret: str | None = None
    configured_target: str | None = None


class IntegrationsResponse(BaseModel):
    items: list[IntegrationDescriptor]


class IntegrationTestResponse(BaseModel):
    id: IntegrationId
    success: bool
    status: IntegrationStatus
    tested_at: datetime
    message: str


class ChannelAccount(BaseModel):
    account_id: str
    channel: ChannelId
    display_name: str = Field(min_length=1)
    target: str | None = None
    enabled: bool = True
    is_default: bool = False
    secret_status: IntegrationStatus = "missing"
    capabilities: list[str] = Field(default_factory=list)
    last_tested_at: datetime | None = None
    last_test_status: IntegrationStatus | None = None
    last_test_message: str | None = None
    successful_publishes: int = 0
    failed_publishes: int = 0
    last_published_at: datetime | None = None
    last_publish_status: Literal["published", "failed"] | None = None
    last_publish_message: str | None = None


class ChannelAccountCreateRequest(BaseModel):
    display_name: str = Field(min_length=1)
    target: str | None = None
    enabled: bool = True
    is_default: bool = False


class ChannelAccountUpdateRequest(BaseModel):
    display_name: str | None = Field(default=None, min_length=1)
    target: str | None = None
    enabled: bool | None = None
    is_default: bool | None = None


class ChannelAccountsResponse(BaseModel):
    channel: ChannelId
    items: list[ChannelAccount]


class ChannelAccountResponse(BaseModel):
    item: ChannelAccount


class ChannelDescriptor(BaseModel):
    id: ChannelId
    accounts_count: int
    default_account_id: str | None = None


class ChannelsResponse(BaseModel):
    items: list[ChannelDescriptor]


class ChannelAccountTestResponse(BaseModel):
    channel: ChannelId
    account_id: str
    success: bool
    status: IntegrationStatus
    tested_at: datetime
    message: str
