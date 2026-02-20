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
    campaign_id: str | None = None


class DraftVariant(BaseModel):
    channel: BrandChannel
    language: DraftLanguage
    content: str


class DraftBundle(BaseModel):
    draft_id: str
    candidate_id: str
    variants: list[DraftVariant]
    campaign_id: str | None = None


class QueueDraftRequest(BaseModel):
    target_channel: BrandChannel
    account_id: str | None = None
    target: str | None = None
    # Deprecated compatibility field (use `target`).
    target_repo: str | None = None
    target_path: str | None = None
    target_language: DraftLanguage | None = None
    payload_override: str | None = None
    campaign_id: str | None = None


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
    campaign_id: str | None = None


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


KeywordType = Literal[
    "brand_core", "brand_product", "brand_person", "risk_term", "competitor_context"
]
SearchResultClass = Literal[
    "owned_source",
    "brand_mention_positive",
    "brand_mention_neutral",
    "brand_mention_risk",
    "unrelated",
]
CampaignStatus = Literal["draft", "ready", "running", "completed", "failed", "cancelled"]


class BrandKeyword(BaseModel):
    keyword_id: str
    phrase: str = Field(min_length=1)
    keyword_type: KeywordType
    priority: int = Field(ge=1, le=5)
    active: bool
    created_at: datetime


class BrandKeywordCreateRequest(BaseModel):
    phrase: str = Field(min_length=1)
    keyword_type: KeywordType = "brand_core"
    priority: int = Field(default=3, ge=1, le=5)
    active: bool = True


class BrandKeywordUpdateRequest(BaseModel):
    phrase: str | None = Field(default=None, min_length=1)
    keyword_type: KeywordType | None = None
    priority: int | None = Field(default=None, ge=1, le=5)
    active: bool | None = None


class BrandKeywordsResponse(BaseModel):
    count: int
    items: list[BrandKeyword]


class BrandBaseSource(BaseModel):
    source_id: str
    name: str = Field(min_length=1)
    base_url: str = Field(min_length=1)
    channel: BrandChannel
    priority: int = Field(ge=1, le=5)
    enabled: bool
    owner_tag: str | None = None
    created_at: datetime


class BrandBaseSourceCreateRequest(BaseModel):
    name: str = Field(min_length=1)
    base_url: str = Field(min_length=1)
    channel: BrandChannel
    priority: int = Field(default=3, ge=1, le=5)
    enabled: bool = True
    owner_tag: str | None = None


class BrandBaseSourceUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1)
    base_url: str | None = Field(default=None, min_length=1)
    channel: BrandChannel | None = None
    priority: int | None = Field(default=None, ge=1, le=5)
    enabled: bool | None = None
    owner_tag: str | None = None


class BrandBaseSourcesResponse(BaseModel):
    count: int
    items: list[BrandBaseSource]


class BrandSearchResult(BaseModel):
    result_id: str
    scan_id: str
    keyword_id: str
    url: str
    title: str
    snippet: str
    position: int
    scanned_at: datetime
    classification: SearchResultClass
    maps_to_base_source: bool
    base_source_id: str | None = None


class BrandMonitoringScan(BaseModel):
    scan_id: str
    keywords_scanned: list[str]
    total_results: int
    scanned_at: datetime
    status: Literal["completed", "partial", "failed"]
    message: str | None = None


class BrandMonitoringScanRequest(BaseModel):
    keyword_ids: list[str] | None = None
    request_id: str | None = None


class BrandMonitoringScanResponse(BaseModel):
    scan: BrandMonitoringScan
    results: list[BrandSearchResult]


class BrandMonitoringResultsResponse(BaseModel):
    count: int
    scan_id: str | None = None
    items: list[BrandSearchResult]


class BrandMonitoringSummary(BaseModel):
    total_keywords: int
    active_keywords: int
    total_base_sources: int
    total_results: int
    owned_source_coverage: float = Field(ge=0.0, le=1.0)
    risk_count: int
    last_scan_at: datetime | None = None


class BrandCampaign(BaseModel):
    campaign_id: str
    name: str = Field(min_length=1)
    strategy_id: str
    source_scan_id: str | None = None
    linked_keyword_ids: list[str] = Field(default_factory=list)
    linked_result_ids: list[str] = Field(default_factory=list)
    channels: list[BrandChannel] = Field(min_length=1)
    status: CampaignStatus
    created_at: datetime
    updated_at: datetime
    draft_ids: list[str] = Field(default_factory=list)
    queue_ids: list[str] = Field(default_factory=list)


class BrandCampaignCreateRequest(BaseModel):
    name: str = Field(min_length=1)
    strategy_id: str | None = None
    source_scan_id: str | None = None
    linked_keyword_ids: list[str] = Field(default_factory=list)
    linked_result_ids: list[str] = Field(default_factory=list)
    channels: list[BrandChannel] = Field(min_length=1)


class BrandCampaignUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1)
    status: CampaignStatus | None = None
    linked_keyword_ids: list[str] | None = None
    linked_result_ids: list[str] | None = None
    channels: list[BrandChannel] | None = Field(default=None, min_length=1)


class BrandCampaignsResponse(BaseModel):
    count: int
    items: list[BrandCampaign]


class BrandCampaignResponse(BaseModel):
    item: BrandCampaign


class BrandCampaignRunResponse(BaseModel):
    campaign_id: str
    status: CampaignStatus
    message: str
    draft_ids: list[str] = Field(default_factory=list)
    queue_ids: list[str] = Field(default_factory=list)


class BrandCampaignLinkDraftResponse(BaseModel):
    campaign_id: str
    draft_id: str
    status: str
    message: str
