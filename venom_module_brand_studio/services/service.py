from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from typing import Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import uuid4

from venom_module_brand_studio.api.schemas import (
    BrandBaseSource,
    BrandBaseSourceCreateRequest,
    BrandBaseSourceUpdateRequest,
    BrandCampaign,
    BrandCampaignCreateRequest,
    BrandCampaignRunResponse,
    BrandCampaignUpdateRequest,
    BrandKeyword,
    BrandKeywordCreateRequest,
    BrandKeywordUpdateRequest,
    BrandMonitoringScan,
    BrandMonitoringScanRequest,
    BrandMonitoringScanResponse,
    BrandMonitoringSummary,
    BrandSearchResult,
    BrandStudioAuditEntry,
    ChannelAccount,
    ChannelAccountCreateRequest,
    ChannelAccountsResponse,
    ChannelAccountTestResponse,
    ChannelAccountUpdateRequest,
    ChannelCredentialProfile,
    ChannelCredentialProfileCreateRequest,
    ChannelCredentialProfilesResponse,
    ChannelCredentialProfileTestResponse,
    ChannelCredentialProfileUpdateRequest,
    ChannelDescriptor,
    ChannelId,
    ChannelsResponse,
    ConfigUpdateRequest,
    ContentCandidate,
    CredentialProfileAuthMode,
    CredentialProfileRole,
    CredentialProfileStatus,
    DraftBundle,
    DraftVariant,
    IntegrationDescriptor,
    IntegrationId,
    IntegrationStatus,
    IntegrationTestResponse,
    OpportunityScoreBreakdown,
    PublishQueueItem,
    PublishResult,
    SearchResultClass,
    StrategyConfig,
    StrategyCreateRequest,
    StrategyUpdateRequest,
)
from venom_module_brand_studio.connectors.devto import DevtoPublisher
from venom_module_brand_studio.connectors.github import GitHubPublisher
from venom_module_brand_studio.connectors.google_cse import GoogleCSEConnector
from venom_module_brand_studio.connectors.hashnode import HashnodePublisher
from venom_module_brand_studio.connectors.hf import HfPublisher
from venom_module_brand_studio.connectors.linkedin import LinkedInPublisher
from venom_module_brand_studio.connectors.medium import MediumPublisher
from venom_module_brand_studio.connectors.reddit import RedditPublisher
from venom_module_brand_studio.connectors.sources import (
    fetch_arxiv_items,
    fetch_github_items,
    fetch_hn_items,
    fetch_rss_items,
)
from venom_module_brand_studio.services.audit_client import BrandStudioAuditPublisher
from venom_module_brand_studio.services.llm_client import BrandStudioLLMClient

logger = logging.getLogger(__name__)


def _draft_cache_ttl_seconds() -> int:
    raw = (os.getenv("BRAND_STUDIO_DRAFT_CACHE_TTL_SECONDS") or "").strip()
    try:
        return max(60, int(raw)) if raw else 86400
    except ValueError:
        return 86400


def _draft_llm_parallel_workers() -> int:
    raw = (os.getenv("BRAND_STUDIO_LLM_PARALLEL_WORKERS") or "").strip()
    try:
        return min(16, max(1, int(raw))) if raw else 4
    except ValueError:
        return 4


class StrategyNotFoundError(KeyError):
    pass


class LastStrategyDeletionError(ValueError):
    pass


class ChannelAccountNotFoundError(KeyError):
    pass


class CredentialProfileNotFoundError(KeyError):
    pass


SUPPORTED_CHANNELS: tuple[ChannelId, ...] = (
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
)
REAL_PUBLISH_CHANNELS: tuple[ChannelId, ...] = (
    "github",
    "blog",
    "devto",
    "reddit",
    "hashnode",
    "linkedin",
    "medium",
    "hf_blog",
    "hf_spaces",
)
MANUAL_PUBLISH_CHANNELS: tuple[ChannelId, ...] = ("x",)
PLANNED_PUBLISH_CHANNELS: tuple[ChannelId, ...] = ()


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _profile_role_to_account_role(role: CredentialProfileRole) -> Literal["primary", "supporting"]:
    return "primary" if role == "primary_brand" else "supporting"


def _account_role_to_profile_role(role: str) -> CredentialProfileRole:
    return "primary_brand" if role == "primary" else "supporting_brand"


def _default_auth_mode_for_channel(channel: ChannelId) -> str:
    if channel in {"x", "blog"}:
        return "none"
    if channel in {"reddit", "linkedin"}:
        return "oauth"
    return "api_key"


def _canonical_url(raw_url: str) -> str:
    parsed = urlsplit(raw_url)
    cleaned_query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if not (key.startswith("utm_") or key in {"ref", "source", "fbclid", "gclid"})
    ]
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(cleaned_query), ""))


def _normalize_lang(raw_lang: str) -> str:
    lowered = raw_lang.strip().lower()
    if lowered in {"pl", "en"}:
        return lowered
    return "other"


def _clip_01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _score_breakdown(topic: str, summary: str, age_minutes: int) -> OpportunityScoreBreakdown:
    text = f"{topic} {summary}".lower()
    relevance_hits = sum(
        1 for kw in ("ai", "agent", "llm", "governance", "routing", "memory", "module")
        if kw in text
    )
    authority_hits = sum(
        1
        for kw in ("engineering", "runtime", "python", "devops", "architecture", "platform")
        if kw in text
    )
    risk_hits = sum(1 for kw in ("giveaway", "crypto moon", "viral trick", "spam") if kw in text)

    relevance = _clip_01(relevance_hits / 6.0)
    timeliness = _clip_01(1.0 - (age_minutes / 1440.0))
    authority_fit = _clip_01(authority_hits / 5.0)
    risk_penalty = _clip_01(risk_hits / 2.0)
    final_score = _clip_01(
        (0.40 * relevance)
        + (0.25 * timeliness)
        + (0.25 * authority_fit)
        - (0.20 * risk_penalty)
    )

    reasons: list[str] = []
    if relevance >= 0.6:
        reasons.append("high topical relevance")
    if timeliness >= 0.7:
        reasons.append("fresh discussion")
    if authority_fit >= 0.6:
        reasons.append("strong authority fit")
    if risk_penalty >= 0.3:
        reasons.append("elevated risk")
    if not reasons:
        reasons.append("balanced opportunity")

    return OpportunityScoreBreakdown(
        relevance=relevance,
        timeliness=timeliness,
        authority_fit=authority_fit,
        risk_penalty=risk_penalty,
        final_score=final_score,
        reasons=reasons,
    )


def _normalize_and_rank_candidates(raw_items: list[dict[str, object]]) -> list[ContentCandidate]:
    by_dedupe_key: dict[str, ContentCandidate] = {}
    for raw in raw_items:
        canonical_url = _canonical_url(str(raw["url"]))
        topic = str(raw["topic"]).strip()
        summary = str(raw["summary"]).strip()
        age_minutes = int(raw["age_minutes"])
        breakdown = _score_breakdown(topic=topic, summary=summary, age_minutes=age_minutes)
        dedupe_hash = hashlib.sha256(
            f"{canonical_url}|{topic.lower()}|{summary.lower()}".encode("utf-8")
        ).hexdigest()

        candidate = ContentCandidate(
            id=str(raw.get("id") or f"cand-{uuid4().hex[:10]}"),
            source=str(raw["source"]),
            url=canonical_url,
            topic=topic,
            summary=summary,
            language=_normalize_lang(str(raw["language"])),
            score=breakdown.final_score,
            age_minutes=age_minutes,
            score_breakdown=breakdown,
            reasons=list(breakdown.reasons),
        )
        existing = by_dedupe_key.get(dedupe_hash)
        if existing is None or candidate.score > existing.score:
            by_dedupe_key[dedupe_hash] = candidate

    ranked = list(by_dedupe_key.values())
    ranked.sort(key=lambda item: (item.score, -item.age_minutes), reverse=True)
    return ranked


def _sample_candidates() -> list[ContentCandidate]:
    raw_items = [
        {
            "id": "cand-1",
            "source": "github",
            "url": "https://github.com/trending?utm_source=weekly",
            "topic": "Runtime governance for local-first AI stacks",
            "summary": "Growing discussion around governance and safe runtime fallback paths.",
            "language": "en",
            "age_minutes": 40,
        },
        {
            "id": "cand-2",
            "source": "hn",
            "url": "https://news.ycombinator.com/item?id=123",
            "topic": "Cost controls for hybrid local/cloud LLM routing",
            "summary": "Thread on balancing local privacy with cloud elasticity.",
            "language": "en",
            "age_minutes": 120,
        },
        {
            "id": "cand-3",
            "source": "rss",
            "url": "https://example.org/devops-ai?ref=feed",
            "topic": "Jak budowac moduły pluginowe bez długu w core",
            "summary": "Artykuł o kontraktach modułowych i separacji produktu od platformy.",
            "language": "pl",
            "age_minutes": 300,
        },
    ]
    return _normalize_and_rank_candidates(raw_items)


def _channel_match(source: str, channel: str | None) -> bool:
    if channel is None:
        return True
    normalized = re.sub(r"[^a-z]", "", channel.lower())
    if normalized == "x":
        return source in {"hn", "github", "rss"}
    if normalized == "github":
        return source in {"github", "arxiv"}
    if normalized == "blog":
        return True
    return True


def _matches_topic_keywords(item: ContentCandidate, keywords: list[str]) -> bool:
    normalized = [value.strip().lower() for value in keywords if value.strip()]
    if not normalized:
        return True
    text = " ".join(
        [
            item.topic.lower(),
            item.summary.lower(),
            item.url.lower(),
            " ".join(reason.lower() for reason in item.reasons),
        ]
    )
    return any(keyword in text for keyword in normalized)


def _default_target_path(channel: str) -> str:
    date_stamp = _utcnow().strftime("%Y-%m-%d")
    if channel == "blog":
        return f"content/brand-studio/{date_stamp}-brand-studio.md"
    return f"notes/brand-studio/{date_stamp}-brand-studio.md"


def _masked_secret(secret: str | None) -> str | None:
    value = (secret or "").strip()
    if not value:
        return None
    if len(value) <= 4:
        return "*" * len(value)
    return f"{'*' * (len(value) - 4)}{value[-4:]}"


_POSITIVE_SNIPPET_KEYWORDS: tuple[str, ...] = (
    "official",
    "my blog",
    "my project",
    "własny",
)
_RISK_SNIPPET_KEYWORDS: tuple[str, ...] = (
    "scam",
    "fraud",
    "fake",
    "spam",
    "ripoff",
)
_NEUTRAL_SNIPPET_KEYWORDS: tuple[str, ...] = (
    "review",
    "mention",
    "about",
    "profile",
)

# Retention limits for in-memory monitoring storage
_MAX_SCAN_RESULTS_RETAINED = 500
_MAX_SCANS_RETAINED = 100


class BrandStudioService:
    def __init__(self) -> None:
        self._candidates: list[ContentCandidate] = []
        self._last_refresh_at: datetime = datetime.fromtimestamp(0, tz=UTC)
        self._drafts: dict[str, DraftBundle] = {}
        self._draft_cache: dict[str, tuple[str, datetime]] = {}
        self._queue: dict[str, PublishQueueItem] = {}
        self._audit: list[BrandStudioAuditEntry] = []
        self._publisher = GitHubPublisher.from_env()
        self._devto_publisher = DevtoPublisher.from_env()
        self._reddit_publisher = RedditPublisher.from_env()
        self._hashnode_publisher = HashnodePublisher.from_env()
        self._linkedin_publisher = LinkedInPublisher.from_env()
        self._medium_publisher = MediumPublisher.from_env()
        self._hf_publisher = HfPublisher.from_env()
        self._cache_file = self._resolve_cache_file()
        self._state_file = self._resolve_state_file()
        self._accounts_file = self._resolve_accounts_file()
        self._strategies: dict[str, StrategyConfig] = {}
        self._accounts: dict[ChannelId, dict[str, ChannelAccount]] = {
            channel: {} for channel in SUPPORTED_CHANNELS
        }
        self._active_strategy_id = ""
        self._last_integration_test: dict[str, datetime] = {}
        self._lock = RLock()
        self._keywords: dict[str, BrandKeyword] = {}
        self._base_sources: dict[str, BrandBaseSource] = {}
        self._scan_results: list[BrandSearchResult] = []
        self._scans: list[BrandMonitoringScan] = []
        self._campaigns: dict[str, BrandCampaign] = {}
        self._monitoring_request_id_to_scan: dict[str, str] = {}
        self._campaign_run_request_ids: set[str] = set()
        self._google_cse = GoogleCSEConnector.from_env()
        self._llm_client = BrandStudioLLMClient.from_env()
        self._audit_publisher = BrandStudioAuditPublisher.from_env()
        self._init_default_strategy()
        self._init_default_accounts()
        self._load_candidates_cache()
        self._load_runtime_state()
        self._load_accounts_state()
        self._load_monitoring_state()

    def _resolve_cache_file(self) -> Path:
        raw = (os.getenv("BRAND_STUDIO_CACHE_FILE") or "").strip()
        if raw:
            return Path(raw)
        return Path("/tmp/venom-brand-studio/candidates-cache.json")

    def _resolve_state_file(self) -> Path:
        raw = (os.getenv("BRAND_STUDIO_STATE_FILE") or "").strip()
        if raw:
            return Path(raw)
        return Path("/tmp/venom-brand-studio/runtime-state.json")

    def _resolve_accounts_file(self) -> Path:
        raw = (os.getenv("BRAND_STUDIO_ACCOUNTS_FILE") or "").strip()
        if raw:
            return Path(raw)
        return Path("/tmp/venom-brand-studio/accounts-state.json")

    def _init_default_strategy(self) -> None:
        mode = (os.getenv("BRAND_STUDIO_DISCOVERY_MODE") or "hybrid").strip().lower()
        if mode not in {"stub", "hybrid", "live"}:
            mode = "hybrid"
        rss_urls = [
            item.strip()
            for item in (os.getenv("BRAND_STUDIO_RSS_URLS") or "").split(",")
            if item.strip()
        ]
        topic_keywords = [
            item.strip()
            for item in (os.getenv("BRAND_STUDIO_TOPIC_KEYWORDS") or "").split(",")
            if item.strip()
        ]
        raw_ttl = (os.getenv("BRAND_STUDIO_CACHE_TTL_SECONDS") or "1800").strip()
        try:
            ttl = max(30, min(86400, int(raw_ttl)))
        except ValueError:
            ttl = 1800
        self._strategies = {
            "default": StrategyConfig(
                id="default",
                name="Default",
                discovery_mode=mode,
                rss_urls=rss_urls,
                topic_keywords=topic_keywords,
                cache_ttl_seconds=ttl,
                min_score=0.3,
                limit=30,
                active_channels=["x", "github", "blog"],
                draft_languages=["pl", "en"],
                default_accounts={},
            )
        }
        self._active_strategy_id = "default"

    def _init_default_accounts(self) -> None:
        defaults: dict[ChannelId, list[tuple[str, str | None]]] = {
            "github": [("default-github", os.getenv("BRAND_TARGET_REPO"))],
            "blog": [("default-blog", os.getenv("BRAND_TARGET_REPO"))],
            "x": [("default-x", None)],
        }
        for channel, items in defaults.items():
            for account_id, target in items:
                self._accounts[channel][account_id] = ChannelAccount(
                    account_id=account_id,
                    channel=channel,
                    display_name=account_id.replace("-", " ").title(),
                    identity_handle=target or None,
                    auth_mode=_default_auth_mode_for_channel(channel),
                    target=(target or None),
                    enabled=True,
                    is_default=True,
                    profile_status=self._profile_status_for_account(
                        channel=channel,
                        enabled=True,
                        auth_mode=_default_auth_mode_for_channel(channel),
                        identity_handle=target or None,
                        auth_secret_set=False,
                    ),
                    secret_status=self._secret_status_for_channel(channel),
                    capabilities=self._capabilities_for_channel(channel),
                )
            self._mark_single_default(channel)

        default_strategy = self._strategies.get("default")
        if default_strategy is not None:
            default_accounts: dict[ChannelId, str] = {}
            for channel in ("x", "github", "blog"):
                default = self._default_account_for_channel(channel)
                if default is not None:
                    default_accounts[channel] = default.account_id
            self._strategies["default"] = default_strategy.model_copy(
                update={"default_accounts": default_accounts}
            )

    def _active_strategy(self) -> StrategyConfig:
        strategy = self._strategies.get(self._active_strategy_id)
        if strategy is not None:
            return strategy
        first = next(iter(self._strategies.values()))
        self._active_strategy_id = first.id
        return first

    def _cache_ttl_seconds(self) -> int:
        return self._active_strategy().cache_ttl_seconds

    def _is_cache_fresh(self) -> bool:
        if not self._candidates:
            return False
        age_seconds = (_utcnow() - self._last_refresh_at).total_seconds()
        return age_seconds <= self._cache_ttl_seconds()

    def _load_candidates_cache(self) -> None:
        try:
            if not self._cache_file.exists():
                return
            payload = json.loads(self._cache_file.read_text(encoding="utf-8"))
            refreshed_at_raw = payload.get("refreshed_at")
            items_raw = payload.get("items")
            if not isinstance(refreshed_at_raw, str) or not isinstance(items_raw, list):
                return
            loaded_items: list[ContentCandidate] = []
            for item in items_raw:
                if isinstance(item, dict):
                    loaded_items.append(ContentCandidate.model_validate(item))
            if not loaded_items:
                return
            self._last_refresh_at = datetime.fromisoformat(refreshed_at_raw)
            if self._last_refresh_at.tzinfo is None:
                self._last_refresh_at = self._last_refresh_at.replace(tzinfo=UTC)
            self._candidates = loaded_items
        except Exception:
            return

    def _persist_candidates_cache(self) -> None:
        try:
            self._cache_file.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "refreshed_at": self._last_refresh_at.isoformat(),
                "items": [item.model_dump(mode="json") for item in self._candidates],
            }
            self._cache_file.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        except Exception as exc:
            logger.warning("Brand Studio candidates cache persist failed: %s", exc)
            return

    def _load_runtime_state(self) -> None:
        try:
            if not self._state_file.exists():
                return
            payload = json.loads(self._state_file.read_text(encoding="utf-8"))
            queue_raw = payload.get("queue")
            audit_raw = payload.get("audit")
            drafts_raw = payload.get("drafts")
            draft_cache_raw = payload.get("draft_cache")
            strategies_raw = payload.get("strategies")
            active_strategy_id = payload.get("active_strategy_id")
            integration_raw = payload.get("integration_tests")

            if isinstance(queue_raw, list):
                loaded_queue: dict[str, PublishQueueItem] = {}
                for item in queue_raw:
                    if isinstance(item, dict):
                        model = PublishQueueItem.model_validate(item)
                        loaded_queue[model.item_id] = model
                self._queue = loaded_queue

            if isinstance(audit_raw, list):
                loaded_audit: list[BrandStudioAuditEntry] = []
                for item in audit_raw:
                    if isinstance(item, dict):
                        loaded_audit.append(BrandStudioAuditEntry.model_validate(item))
                self._audit = loaded_audit

            if isinstance(drafts_raw, list):
                loaded_drafts: dict[str, DraftBundle] = {}
                for item in drafts_raw:
                    if isinstance(item, dict):
                        draft = DraftBundle.model_validate(item)
                        loaded_drafts[draft.draft_id] = draft
                self._drafts = loaded_drafts

            if isinstance(draft_cache_raw, dict):
                loaded_cache: dict[str, tuple[str, datetime]] = {}
                for key, value in draft_cache_raw.items():
                    if not isinstance(key, str) or not isinstance(value, dict):
                        continue
                    draft_id = value.get("draft_id")
                    generated_at_raw = value.get("generated_at")
                    if not isinstance(draft_id, str) or not isinstance(generated_at_raw, str):
                        continue
                    try:
                        generated_at = datetime.fromisoformat(generated_at_raw)
                        if generated_at.tzinfo is None:
                            generated_at = generated_at.replace(tzinfo=UTC)
                        loaded_cache[key] = (draft_id, generated_at)
                    except Exception:
                        continue
                self._draft_cache = loaded_cache

            if isinstance(strategies_raw, list):
                loaded_strategies: dict[str, StrategyConfig] = {}
                for item in strategies_raw:
                    if isinstance(item, dict):
                        strategy = StrategyConfig.model_validate(item)
                        loaded_strategies[strategy.id] = strategy
                if loaded_strategies:
                    self._strategies = loaded_strategies

            if isinstance(active_strategy_id, str) and active_strategy_id in self._strategies:
                self._active_strategy_id = active_strategy_id

            if isinstance(integration_raw, dict):
                loaded: dict[str, datetime] = {}
                for key, value in integration_raw.items():
                    if isinstance(key, str) and isinstance(value, str):
                        try:
                            loaded[key] = datetime.fromisoformat(value)
                        except Exception:
                            pass
                self._last_integration_test = loaded
            self._cleanup_draft_cache()
        except Exception as exc:
            logger.warning("Brand Studio runtime state load failed: %s", exc)
            return

    def _persist_runtime_state(self) -> None:
        try:
            self._state_file.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "drafts": [item.model_dump(mode="json") for item in self._drafts.values()],
                "draft_cache": {
                    key: {"draft_id": draft_id, "generated_at": generated_at.isoformat()}
                    for key, (draft_id, generated_at) in self._draft_cache.items()
                },
                "queue": [item.model_dump(mode="json") for item in self._queue.values()],
                "audit": [item.model_dump(mode="json") for item in self._audit],
                "strategies": [item.model_dump(mode="json") for item in self._strategies.values()],
                "active_strategy_id": self._active_strategy_id,
                "integration_tests": {
                    key: value.isoformat() for key, value in self._last_integration_test.items()
                },
            }
            self._state_file.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        except Exception as exc:
            logger.warning("Brand Studio runtime state persist failed: %s", exc)
            return

    def _secret_status_for_channel(self, channel: ChannelId) -> IntegrationStatus:
        if channel in {"blog", "github"}:
            token = (os.getenv("GITHUB_TOKEN_BRAND") or "").strip()
            return "configured" if token else "missing"
        if channel == "x":
            return "configured" if (os.getenv("X_API_TOKEN") or "").strip() else "missing"
        if channel == "linkedin":
            return "configured" if (os.getenv("LINKEDIN_ACCESS_TOKEN") or "").strip() else "missing"
        if channel == "medium":
            return "configured" if (os.getenv("MEDIUM_TOKEN") or "").strip() else "missing"
        if channel in {"hf_blog", "hf_spaces"}:
            return "configured" if (os.getenv("HF_TOKEN") or "").strip() else "missing"
        if channel == "reddit":
            client_id = (os.getenv("REDDIT_CLIENT_ID") or "").strip()
            client_secret = (os.getenv("REDDIT_CLIENT_SECRET") or "").strip()
            refresh_token = (os.getenv("REDDIT_REFRESH_TOKEN") or "").strip()
            if client_id and client_secret and refresh_token:
                return "configured"
            if client_id or client_secret or refresh_token:
                return "invalid"
            return "missing"
        if channel == "devto":
            return "configured" if (os.getenv("DEVTO_API_KEY") or "").strip() else "missing"
        if channel == "hashnode":
            return "configured" if (os.getenv("HASHNODE_TOKEN") or "").strip() else "missing"
        return "invalid"

    def _profile_status_for_account(
        self,
        *,
        channel: ChannelId,
        enabled: bool,
        auth_mode: CredentialProfileAuthMode,
        identity_handle: str | None,
        auth_secret_set: bool,
    ) -> CredentialProfileStatus:
        if not enabled:
            return "disabled"
        normalized_mode = auth_mode or _default_auth_mode_for_channel(channel)
        normalized_handle = (identity_handle or "").strip()
        if normalized_mode == "none":
            return "configured"
        if normalized_mode == "username_only":
            return "configured" if normalized_handle else "incomplete"
        if normalized_mode == "login_password":
            return "configured" if normalized_handle and auth_secret_set else "incomplete"

        secret_status = self._secret_status_for_channel(channel)
        if secret_status == "invalid":
            return "invalid"
        if secret_status == "configured" or auth_secret_set:
            return "configured"
        return "incomplete"

    def _capabilities_for_channel(self, channel: ChannelId) -> list[str]:
        if channel in REAL_PUBLISH_CHANNELS:
            return ["publish_markdown", "queue"]
        if channel in MANUAL_PUBLISH_CHANNELS:
            return ["manual_publish_mvp", "queue"]
        if channel in PLANNED_PUBLISH_CHANNELS:
            return ["planned_connector", "queue"]
        return ["queue"]

    def _mark_single_default(self, channel: ChannelId) -> None:
        accounts = self._accounts.get(channel, {})
        if not accounts:
            return
        default_ids = [
            item.account_id for item in accounts.values() if item.is_default and item.enabled
        ]
        chosen_id = default_ids[0] if default_ids else next(iter(accounts.keys()))
        for account_id, account in list(accounts.items()):
            accounts[account_id] = account.model_copy(
                update={"is_default": account_id == chosen_id}
            )

    def _default_account_for_channel(self, channel: ChannelId) -> ChannelAccount | None:
        accounts = self._accounts.get(channel, {})
        if not accounts:
            return None
        for account in accounts.values():
            if account.is_default and account.enabled:
                return account
        for account in accounts.values():
            if account.enabled:
                return account
        return None

    def _refresh_account_runtime_fields(self) -> None:
        for channel, accounts in self._accounts.items():
            secret_status = self._secret_status_for_channel(channel)
            capabilities = self._capabilities_for_channel(channel)
            for account_id, account in list(accounts.items()):
                auth_mode = account.auth_mode or _default_auth_mode_for_channel(channel)
                accounts[account_id] = account.model_copy(
                    update={
                        "auth_mode": auth_mode,
                        "secret_status": secret_status,
                        "profile_status": self._profile_status_for_account(
                            channel=channel,
                            enabled=account.enabled,
                            auth_mode=auth_mode,
                            identity_handle=account.identity_handle,
                            auth_secret_set=account.auth_secret_set,
                        ),
                        "capabilities": capabilities,
                    }
                )
            self._mark_single_default(channel)

    def _load_accounts_state(self) -> None:
        try:
            if not self._accounts_file.exists():
                self._refresh_account_runtime_fields()
                return
            payload = json.loads(self._accounts_file.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                self._refresh_account_runtime_fields()
                return
            for channel in SUPPORTED_CHANNELS:
                raw_items = payload.get(channel)
                if not isinstance(raw_items, list):
                    continue
                loaded: dict[str, ChannelAccount] = {}
                for raw in raw_items:
                    if not isinstance(raw, dict):
                        continue
                    item = ChannelAccount.model_validate(raw)
                    loaded[item.account_id] = item
                if loaded:
                    self._accounts[channel] = loaded
            self._refresh_account_runtime_fields()
        except Exception as exc:
            logger.warning("Brand Studio accounts state load failed: %s", exc)
            self._refresh_account_runtime_fields()
            return

    def _persist_accounts_state(self) -> None:
        try:
            self._accounts_file.parent.mkdir(parents=True, exist_ok=True)
            payload: dict[str, list[dict[str, object]]] = {}
            for channel in SUPPORTED_CHANNELS:
                payload[channel] = [
                    item.model_dump(mode="json")
                    for item in self._accounts.get(channel, {}).values()
                ]
            self._accounts_file.write_text(
                json.dumps(payload, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as exc:
            logger.warning("Brand Studio accounts state persist failed: %s", exc)
            return

    def config(self) -> tuple[str, StrategyConfig]:
        with self._lock:
            active = self._active_strategy()
            return self._active_strategy_id, active

    def update_active_config(self, payload: ConfigUpdateRequest, *, actor: str) -> StrategyConfig:
        with self._lock:
            strategy = self._active_strategy().model_copy(deep=True)
            updates = payload.model_dump(exclude_none=True)
            strategy = strategy.model_copy(update=updates)
            self._strategies[strategy.id] = strategy
            self._add_audit(actor=actor, action="config.update", status="ok", payload=strategy.id)
            self._persist_runtime_state()
            return strategy

    def strategies(self) -> tuple[str, list[StrategyConfig]]:
        with self._lock:
            items = sorted(self._strategies.values(), key=lambda item: item.name.lower())
            return self._active_strategy_id, items

    def create_strategy(self, payload: StrategyCreateRequest, *, actor: str) -> StrategyConfig:
        with self._lock:
            base = self._active_strategy()
            if payload.base_strategy_id:
                base_candidate = self._strategies.get(payload.base_strategy_id)
                if base_candidate is None:
                    raise StrategyNotFoundError("strategy_not_found")
                base = base_candidate

            strategy_id = f"strategy-{uuid4().hex[:8]}"
            updates = payload.model_dump(exclude_none=True, exclude={"name", "base_strategy_id"})
            created = base.model_copy(update={"id": strategy_id, "name": payload.name, **updates})

            self._strategies[created.id] = created
            self._persist_runtime_state()
            self._add_audit(actor=actor, action="strategy.create", status="ok", payload=created.id)
            return created

    def update_strategy(
        self,
        strategy_id: str,
        payload: StrategyUpdateRequest,
        *,
        actor: str,
    ) -> StrategyConfig:
        with self._lock:
            current = self._strategies.get(strategy_id)
            if current is None:
                raise StrategyNotFoundError("strategy_not_found")
            updates = payload.model_dump(exclude_none=True)
            updated = current.model_copy(update=updates)
            self._strategies[strategy_id] = updated
            self._persist_runtime_state()
            self._add_audit(actor=actor, action="strategy.update", status="ok", payload=strategy_id)
            return updated

    def delete_strategy(self, strategy_id: str, *, actor: str) -> None:
        with self._lock:
            if strategy_id not in self._strategies:
                raise StrategyNotFoundError("strategy_not_found")
            if len(self._strategies) == 1:
                raise LastStrategyDeletionError("last_strategy_cannot_be_deleted")
            del self._strategies[strategy_id]
            if self._active_strategy_id == strategy_id:
                self._active_strategy_id = sorted(self._strategies.keys())[0]
            self._persist_runtime_state()
            self._add_audit(actor=actor, action="strategy.delete", status="ok", payload=strategy_id)

    def activate_strategy(self, strategy_id: str, *, actor: str) -> StrategyConfig:
        with self._lock:
            strategy = self._strategies.get(strategy_id)
            if strategy is None:
                raise StrategyNotFoundError("strategy_not_found")
            self._active_strategy_id = strategy_id
            self._persist_runtime_state()
            self._add_audit(
                actor=actor,
                action="strategy.activate",
                status="ok",
                payload=strategy_id,
            )
            return strategy

    def channels(self) -> ChannelsResponse:
        with self._lock:
            self._refresh_account_runtime_fields()
            items: list[ChannelDescriptor] = []
            for channel in SUPPORTED_CHANNELS:
                accounts = self._accounts.get(channel, {})
                default = self._default_account_for_channel(channel)
                items.append(
                    ChannelDescriptor(
                        id=channel,
                        accounts_count=len(accounts),
                        default_account_id=default.account_id if default else None,
                    )
                )
            return ChannelsResponse(items=items)

    def channel_accounts(self, channel: ChannelId) -> ChannelAccountsResponse:
        with self._lock:
            self._refresh_account_runtime_fields()
            items = list(self._accounts.get(channel, {}).values())
            items.sort(key=lambda item: item.display_name.lower())
            return ChannelAccountsResponse(channel=channel, items=items)

    def create_channel_account(
        self,
        channel: ChannelId,
        payload: ChannelAccountCreateRequest,
        *,
        actor: str,
    ) -> ChannelAccount:
        with self._lock:
            account_id = f"{channel}-{uuid4().hex[:8]}"
            current = self._accounts.get(channel, {})
            if payload.role == "supporting":
                if not payload.supports_account_id:
                    raise ValueError("supporting_account_requires_supports_account_id")
                referenced = current.get(payload.supports_account_id)
                if referenced is None:
                    raise ChannelAccountNotFoundError("supports_account_id_not_found")
                if referenced.role != "primary":
                    raise ValueError("supports_account_id_must_reference_primary_account")
            auth_mode = payload.auth_mode or _default_auth_mode_for_channel(channel)
            identity_handle = payload.identity_handle or payload.target
            auth_secret_set = bool((payload.auth_secret or "").strip())
            created = ChannelAccount(
                account_id=account_id,
                channel=channel,
                display_name=payload.display_name,
                identity_handle=identity_handle,
                auth_mode=auth_mode,
                auth_secret_set=auth_secret_set,
                target=payload.target,
                enabled=payload.enabled,
                is_default=payload.is_default or len(current) == 0,
                role=payload.role,
                supports_account_id=payload.supports_account_id,
                profile_status=self._profile_status_for_account(
                    channel=channel,
                    enabled=payload.enabled,
                    auth_mode=auth_mode,
                    identity_handle=identity_handle,
                    auth_secret_set=auth_secret_set,
                ),
                secret_status=self._secret_status_for_channel(channel),
                capabilities=self._capabilities_for_channel(channel),
            )
            current[account_id] = created
            self._mark_single_default(channel)
            self._persist_accounts_state()
            self._add_audit(
                actor=actor,
                action="account.create",
                status="ok",
                payload=f"{channel}:{account_id}",
            )
            active = self._active_strategy()
            if not active.default_accounts.get(channel):
                default = self._default_account_for_channel(channel)
                if default is not None:
                    updated_defaults = dict(active.default_accounts)
                    updated_defaults[channel] = default.account_id
                    self._strategies[active.id] = active.model_copy(
                        update={"default_accounts": updated_defaults}
                    )
                    self._persist_runtime_state()
            return current[account_id]

    def update_channel_account(
        self,
        channel: ChannelId,
        account_id: str,
        payload: ChannelAccountUpdateRequest,
        *,
        actor: str,
    ) -> ChannelAccount:
        with self._lock:
            current = self._accounts.get(channel, {})
            account = current.get(account_id)
            if account is None:
                raise ChannelAccountNotFoundError("account_not_found")
            updates = payload.model_dump(exclude_none=True, exclude={"auth_secret"})
            if payload.auth_secret is not None:
                updates["auth_secret_set"] = bool(payload.auth_secret.strip())
            updated = account.model_copy(update=updates)
            updated = updated.model_copy(
                update={
                    "profile_status": self._profile_status_for_account(
                        channel=channel,
                        enabled=updated.enabled,
                        auth_mode=updated.auth_mode,
                        identity_handle=updated.identity_handle,
                        auth_secret_set=updated.auth_secret_set,
                    )
                }
            )
            current[account_id] = updated
            self._mark_single_default(channel)
            self._persist_accounts_state()
            self._add_audit(
                actor=actor,
                action="account.update",
                status="ok",
                payload=f"{channel}:{account_id}",
            )
            return current[account_id]

    def delete_channel_account(self, channel: ChannelId, account_id: str, *, actor: str) -> None:
        with self._lock:
            current = self._accounts.get(channel, {})
            if account_id not in current:
                raise ChannelAccountNotFoundError("account_not_found")
            del current[account_id]
            self._mark_single_default(channel)
            self._persist_accounts_state()
            self._add_audit(
                actor=actor,
                action="account.delete",
                status="ok",
                payload=f"{channel}:{account_id}",
            )
            # Clean strategy mappings from removed account.
            for strategy_id, strategy in list(self._strategies.items()):
                if strategy.default_accounts.get(channel) == account_id:
                    defaults = dict(strategy.default_accounts)
                    defaults.pop(channel, None)
                    self._strategies[strategy_id] = strategy.model_copy(
                        update={"default_accounts": defaults}
                    )
            self._persist_runtime_state()

    def activate_channel_account(
        self,
        channel: ChannelId,
        account_id: str,
        *,
        actor: str,
    ) -> ChannelAccount:
        with self._lock:
            current = self._accounts.get(channel, {})
            account = current.get(account_id)
            if account is None:
                raise ChannelAccountNotFoundError("account_not_found")
            for candidate_id, candidate in list(current.items()):
                current[candidate_id] = candidate.model_copy(
                    update={"is_default": candidate_id == account_id}
                )
            self._persist_accounts_state()
            active = self._active_strategy()
            defaults = dict(active.default_accounts)
            defaults[channel] = account_id
            self._strategies[active.id] = active.model_copy(update={"default_accounts": defaults})
            self._persist_runtime_state()
            self._add_audit(
                actor=actor,
                action="account.activate",
                status="ok",
                payload=f"{channel}:{account_id}",
            )
            return current[account_id]

    def test_channel_account(
        self,
        channel: ChannelId,
        account_id: str,
        *,
        actor: str,
    ) -> ChannelAccountTestResponse:  # pragma: no cover
        with self._lock:
            current = self._accounts.get(channel, {})
            account = current.get(account_id)
            if account is None:
                raise ChannelAccountNotFoundError("account_not_found")
            status = self._secret_status_for_channel(channel)
            success = status == "configured"
            message = "Account configured"
            if status == "missing":
                message = "Missing credentials for channel"
            elif status == "invalid":
                message = "Invalid channel configuration"

            if channel in {"github", "blog"} and success and self._publisher is not None:
                try:
                    self._publisher.validate_connection()
                    message = "GitHub API reachable for account"
                except Exception as exc:
                    status = "invalid"
                    success = False
                    message = f"GitHub test failed: {exc}"
            if channel == "devto" and success and self._devto_publisher is not None:
                try:
                    self._devto_publisher.validate_connection()
                    message = "Dev.to API reachable for account"
                except Exception as exc:
                    status = "invalid"
                    success = False
                    message = f"Dev.to test failed: {exc}"
            if channel == "reddit" and success and self._reddit_publisher is not None:
                try:
                    self._reddit_publisher.validate_connection()
                    message = "Reddit API reachable for account"
                except Exception as exc:
                    status = "invalid"
                    success = False
                    message = f"Reddit test failed: {exc}"
            if channel == "hashnode" and success and self._hashnode_publisher is not None:
                try:
                    self._hashnode_publisher.validate_connection()
                    message = "Hashnode API reachable for account"
                except Exception as exc:
                    status = "invalid"
                    success = False
                    message = f"Hashnode test failed: {exc}"
            if channel == "linkedin" and success and self._linkedin_publisher is not None:
                try:
                    self._linkedin_publisher.validate_connection()
                    message = "LinkedIn API reachable for account"
                except Exception as exc:
                    status = "invalid"
                    success = False
                    message = f"LinkedIn test failed: {exc}"
            if channel == "medium" and success and self._medium_publisher is not None:
                try:
                    self._medium_publisher.validate_connection()
                    message = "Medium API reachable for account"
                except Exception as exc:
                    status = "invalid"
                    success = False
                    message = f"Medium test failed: {exc}"
            if channel in {"hf_blog", "hf_spaces"} and success and self._hf_publisher is not None:
                try:
                    self._hf_publisher.validate_connection()
                    message = "Hugging Face API reachable for account"
                except Exception as exc:
                    status = "invalid"
                    success = False
                    message = f"Hugging Face test failed: {exc}"

            tested_at = _utcnow()
            profile_status = self._profile_status_for_account(
                channel=channel,
                enabled=account.enabled,
                auth_mode=account.auth_mode,
                identity_handle=account.identity_handle,
                auth_secret_set=account.auth_secret_set,
            )
            if account.enabled and status == "invalid":
                profile_status = "invalid"
            current[account_id] = account.model_copy(
                update={
                    "secret_status": status,
                    "profile_status": profile_status,
                    "last_tested_at": tested_at,
                    "last_test_status": status,
                    "last_test_message": message,
                }
            )
            self._persist_accounts_state()
            self._add_audit(
                actor=actor,
                action="account.test",
                status="ok" if success else "failed",
                payload=f"{channel}:{account_id}:{status}",
            )
            return ChannelAccountTestResponse(
                channel=channel,
                account_id=account_id,
                success=success,
                status=status,
                tested_at=tested_at,
                message=message,
            )

    def _to_credential_profile(self, account: ChannelAccount) -> ChannelCredentialProfile:
        return ChannelCredentialProfile(
            profile_id=account.account_id,
            channel=account.channel,
            role=_account_role_to_profile_role(account.role),
            identity_display_name=account.display_name,
            identity_handle=account.identity_handle,
            auth_mode=account.auth_mode,
            target=account.target,
            enabled=account.enabled,
            is_default=account.is_default,
            status=account.profile_status,
            supports_profile_id=account.supports_account_id,
            capabilities=account.capabilities,
            last_tested_at=account.last_tested_at,
            last_test_status=account.last_test_status,
            last_test_message=account.last_test_message,
            successful_publishes=account.successful_publishes,
            failed_publishes=account.failed_publishes,
            last_published_at=account.last_published_at,
            last_publish_status=account.last_publish_status,
            last_publish_message=account.last_publish_message,
        )

    def _find_account_by_profile_id(self, profile_id: str) -> tuple[ChannelId, ChannelAccount]:
        for channel in SUPPORTED_CHANNELS:
            item = self._accounts.get(channel, {}).get(profile_id)
            if item is not None:
                return channel, item
        raise CredentialProfileNotFoundError("profile_not_found")

    def credential_profiles(
        self,
        *,
        channel: ChannelId | None = None,
        role: CredentialProfileRole | None = None,
        status_filter: CredentialProfileStatus | None = None,
    ) -> ChannelCredentialProfilesResponse:
        with self._lock:
            self._refresh_account_runtime_fields()
            items: list[ChannelCredentialProfile] = []
            channels = [channel] if channel else list(SUPPORTED_CHANNELS)
            for current_channel in channels:
                for account in self._accounts.get(current_channel, {}).values():
                    profile = self._to_credential_profile(account)
                    if role and profile.role != role:
                        continue
                    if status_filter and profile.status != status_filter:
                        continue
                    items.append(profile)
            items.sort(
                key=lambda item: (
                    item.channel,
                    0 if item.role == "primary_brand" else 1,
                    item.identity_display_name.lower(),
                )
            )
            return ChannelCredentialProfilesResponse(count=len(items), items=items)

    def create_credential_profile(
        self,
        payload: ChannelCredentialProfileCreateRequest,
        *,
        actor: str,
    ) -> ChannelCredentialProfile:
        created = self.create_channel_account(
            payload.channel,
            ChannelAccountCreateRequest(
                display_name=payload.identity_display_name,
                identity_handle=payload.identity_handle,
                auth_mode=payload.auth_mode,
                auth_secret=payload.auth_secret,
                target=payload.target,
                enabled=payload.enabled,
                is_default=payload.is_default,
                role=_profile_role_to_account_role(payload.role),
                supports_account_id=payload.supports_profile_id,
            ),
            actor=actor,
        )
        return self._to_credential_profile(created)

    def update_credential_profile(
        self,
        profile_id: str,
        payload: ChannelCredentialProfileUpdateRequest,
        *,
        actor: str,
    ) -> ChannelCredentialProfile:
        with self._lock:
            channel, account = self._find_account_by_profile_id(profile_id)
            channel_accounts = self._accounts.get(channel, {})

            role = _profile_role_to_account_role(payload.role) if payload.role else account.role
            supports_account_id = (
                account.supports_account_id
                if payload.supports_profile_id is None
                else payload.supports_profile_id
            )
            if role == "supporting":
                if not supports_account_id:
                    raise ValueError("supporting_account_requires_supports_account_id")
                referenced = channel_accounts.get(supports_account_id)
                if referenced is None:
                    raise ChannelAccountNotFoundError("supports_account_id_not_found")
                if referenced.role != "primary":
                    raise ValueError("supports_account_id_must_reference_primary_account")
            else:
                supports_account_id = None

            auth_secret_set = account.auth_secret_set
            if payload.auth_secret is not None:
                auth_secret_set = bool(payload.auth_secret.strip())

            identity_display_name = payload.identity_display_name or account.display_name
            identity_handle = (
                account.identity_handle
                if payload.identity_handle is None
                else payload.identity_handle
            )
            auth_mode = payload.auth_mode or account.auth_mode
            target = account.target if payload.target is None else payload.target
            enabled = account.enabled if payload.enabled is None else payload.enabled
            is_default = account.is_default if payload.is_default is None else payload.is_default

            updated = account.model_copy(
                update={
                    "display_name": identity_display_name,
                    "identity_handle": identity_handle,
                    "auth_mode": auth_mode,
                    "auth_secret_set": auth_secret_set,
                    "target": target,
                    "enabled": enabled,
                    "is_default": is_default,
                    "role": role,
                    "supports_account_id": supports_account_id,
                    "profile_status": self._profile_status_for_account(
                        channel=channel,
                        enabled=enabled,
                        auth_mode=auth_mode,
                        identity_handle=identity_handle,
                        auth_secret_set=auth_secret_set,
                    ),
                }
            )
            channel_accounts[profile_id] = updated
            self._mark_single_default(channel)
            self._persist_accounts_state()
            self._add_audit(
                actor=actor,
                action="credential_profile.update",
                status="ok",
                payload=f"{channel}:{profile_id}",
            )
            return self._to_credential_profile(channel_accounts[profile_id])

    def delete_credential_profile(self, profile_id: str, *, actor: str) -> None:
        with self._lock:
            channel, _account = self._find_account_by_profile_id(profile_id)
        self.delete_channel_account(channel, profile_id, actor=actor)

    def activate_credential_profile(
        self, profile_id: str, *, actor: str
    ) -> ChannelCredentialProfile:
        with self._lock:
            channel, _account = self._find_account_by_profile_id(profile_id)
            current = self._accounts.get(channel, {})
            account = current.get(profile_id)
            if account is None:
                raise CredentialProfileNotFoundError("profile_not_found")

            for candidate_id, candidate in list(current.items()):
                enabled_value = candidate.enabled
                if candidate_id == profile_id:
                    enabled_value = True
                current[candidate_id] = candidate.model_copy(
                    update={
                        "enabled": enabled_value,
                        "is_default": candidate_id == profile_id,
                    }
                )
            self._refresh_account_runtime_fields()
            self._persist_accounts_state()

            active = self._active_strategy()
            defaults = dict(active.default_accounts)
            defaults[channel] = profile_id
            self._strategies[active.id] = active.model_copy(update={"default_accounts": defaults})
            self._persist_runtime_state()
            self._add_audit(
                actor=actor,
                action="account.activate",
                status="ok",
                payload=f"{channel}:{profile_id}",
            )
            return self._to_credential_profile(current[profile_id])

    def test_credential_profile(
        self,
        profile_id: str,
        *,
        actor: str,
    ) -> ChannelCredentialProfileTestResponse:
        with self._lock:
            channel, _account = self._find_account_by_profile_id(profile_id)
        account_test = self.test_channel_account(channel, profile_id, actor=actor)
        with self._lock:
            refreshed = self._accounts[channel][profile_id]
            profile_status = refreshed.profile_status
        return ChannelCredentialProfileTestResponse(
            profile_id=profile_id,
            success=account_test.success,
            status=profile_status,
            tested_at=account_test.tested_at,
            message=account_test.message,
        )

    def _record_account_publish_result(
        self,
        *,
        item: PublishQueueItem,
        status: str,
        message: str,
        published_at: datetime,
    ) -> None:
        if not item.account_id or item.target_channel not in SUPPORTED_CHANNELS:
            return
        channel_accounts = self._accounts.get(item.target_channel, {})
        account = channel_accounts.get(item.account_id)
        if account is None:
            return
        updates: dict[str, object] = {
            "last_published_at": published_at,
            "last_publish_status": "published" if status == "published" else "failed",
            "last_publish_message": message,
        }
        if status == "published":
            updates["successful_publishes"] = account.successful_publishes + 1
        else:
            updates["failed_publishes"] = account.failed_publishes + 1
        channel_accounts[item.account_id] = account.model_copy(update=updates)
        self._persist_accounts_state()

    def refresh_candidates(self, *, force: bool = False) -> None:
        if not force and self._is_cache_fresh():
            return
        strategy = self._active_strategy()
        mode = strategy.discovery_mode
        if mode == "stub":
            self._candidates = _sample_candidates()
            self._last_refresh_at = _utcnow()
            self._persist_candidates_cache()
            return

        live_items = self._fetch_live_items()
        if live_items:
            self._candidates = _normalize_and_rank_candidates(live_items)
            self._last_refresh_at = _utcnow()
            self._persist_candidates_cache()
            return
        if mode == "live":
            self._candidates = []
        else:
            self._candidates = _sample_candidates()
        self._last_refresh_at = _utcnow()
        self._persist_candidates_cache()

    def _fetch_live_items(self) -> list[dict[str, object]]:
        items: list[dict[str, object]] = []
        strategy = self._active_strategy()
        try:
            if strategy.rss_urls:
                items.extend(fetch_rss_items(strategy.rss_urls))
        except Exception:
            pass
        try:
            items.extend(fetch_github_items())
        except Exception:
            pass
        try:
            items.extend(fetch_hn_items())
        except Exception:
            pass
        try:
            items.extend(fetch_arxiv_items())
        except Exception:
            pass
        return items

    def force_refresh(self, *, actor: str) -> tuple[datetime, int]:
        self.refresh_candidates(force=True)
        self._add_audit(actor=actor, action="config.refresh", status="ok", payload="refresh")
        return self._last_refresh_at, len(self._candidates)

    def list_candidates(
        self,
        *,
        channel: str | None,
        lang: str | None,
        limit: int,
        min_score: float | None,
    ) -> tuple[list[ContentCandidate], datetime]:
        self.refresh_candidates()
        self.process_scheduled_queue()
        strategy = self._active_strategy()
        effective_min_score = strategy.min_score if min_score is None else min_score
        effective_limit = min(limit, strategy.limit)
        items = [
            item
            for item in self._candidates
            if item.score >= effective_min_score
            and (lang is None or item.language == lang)
            and _channel_match(item.source, channel)
            and _matches_topic_keywords(item, strategy.topic_keywords)
        ]
        items.sort(key=lambda it: it.score, reverse=True)
        return items[:effective_limit], self._last_refresh_at

    def generate_draft(
        self,
        *,
        candidate_id: str,
        channels: list[str],
        languages: list[str],
        tone: str | None,
        actor: str,
        campaign_id: str | None = None,
        refresh: bool = False,
    ) -> DraftBundle:
        candidate = next((it for it in self._candidates if it.id == candidate_id), None)
        if candidate is None:
            raise KeyError("candidate_not_found")

        cache_key = self._draft_cache_key(
            candidate_id=candidate_id,
            channels=channels,
            languages=languages,
            tone=tone,
            campaign_id=campaign_id,
        )
        if not refresh:
            cached = self._get_cached_draft(cache_key)
            if cached is not None:
                self._add_audit(
                    actor=actor,
                    action="draft.generate",
                    status="cached",
                    payload=cached.draft_id,
                )
                return cached

        variants: list[DraftVariant] = []

        # Stage 1: generate primary content variants
        primary_jobs: list[tuple[str, str, str, str]] = []
        primary_content: dict[str, str] = {}
        for channel in channels:
            for language in languages:
                fallback = self._fallback_primary_content(
                    candidate_topic=candidate.topic,
                    candidate_summary=candidate.summary,
                    language=language,
                    tone=tone,
                )
                prompt = self._build_primary_prompt(
                    candidate_topic=candidate.topic,
                    candidate_summary=candidate.summary,
                    candidate_url=candidate.url,
                    channel=channel,
                    language=language,
                    tone=tone,
                )
                audit_context = f"primary:{channel}:{language}"
                primary_jobs.append((f"{channel}:{language}", prompt, fallback, audit_context))

        primary_content = self._generate_many_draft_texts_with_llm_fallback(
            jobs=primary_jobs,
            actor=actor,
        )

        for channel in channels:
            for language in languages:
                content = primary_content[f"{channel}:{language}"]
                variants.append(DraftVariant(channel=channel, language=language, content=content))

        # Stage 2: generate supporting variants with attribution for supporting accounts
        supporting_jobs: list[tuple[str, str, str, str]] = []
        for channel in channels:
            channel_accounts = self._accounts.get(channel, {})
            primary_account: ChannelAccount | None = None
            for acc in channel_accounts.values():
                if acc.role == "primary" and acc.enabled:
                    primary_account = acc
                    break

            for acc in channel_accounts.values():
                if acc.role != "supporting" or not acc.enabled:
                    continue
                source_ref = (
                    primary_account.display_name if primary_account else candidate.url
                )
                for language in languages:
                    base = primary_content.get(f"{channel}:{language}", "")
                    fallback = self._fallback_supporting_content(
                        source_ref=source_ref,
                        candidate_topic=candidate.topic,
                        candidate_url=candidate.url,
                        primary_content=base,
                        language=language,
                    )
                    prompt = self._build_supporting_prompt(
                        source_ref=source_ref,
                        candidate_topic=candidate.topic,
                        candidate_summary=candidate.summary,
                        candidate_url=candidate.url,
                        primary_content=base,
                        channel=channel,
                        language=language,
                        tone=tone,
                    )
                    audit_context = f"supporting:{channel}:{language}:{acc.account_id}"
                    job_key = f"{channel}:{language}:{acc.account_id}"
                    supporting_jobs.append((job_key, prompt, fallback, audit_context))

        supporting_content = self._generate_many_draft_texts_with_llm_fallback(
            jobs=supporting_jobs,
            actor=actor,
        )

        for channel in channels:
            for acc in self._accounts.get(channel, {}).values():
                if acc.role != "supporting" or not acc.enabled:
                    continue
                for language in languages:
                    job_key = f"{channel}:{language}:{acc.account_id}"
                    teaser = supporting_content.get(job_key)
                    if teaser is None:
                        continue
                    teaser = self._ensure_supporting_attribution(
                        text=teaser, language=language, candidate_url=candidate.url
                    )
                    variants.append(
                        DraftVariant(
                            channel=channel,
                            language=language,
                            content=teaser,
                            account_id=acc.account_id,
                        )
                    )

        draft_id = f"draft-{uuid4().hex[:10]}"
        bundle = DraftBundle(
            draft_id=draft_id, candidate_id=candidate_id, variants=variants, campaign_id=campaign_id
        )
        self._drafts[draft_id] = bundle
        self._draft_cache[cache_key] = (draft_id, _utcnow())
        self._cleanup_draft_cache()
        self._persist_runtime_state()
        audit_payload = f"{draft_id}:campaign={campaign_id}" if campaign_id else draft_id
        self._add_audit(actor=actor, action="draft.generate", status="ok", payload=audit_payload)
        return bundle

    def _draft_cache_key(
        self,
        *,
        candidate_id: str,
        channels: list[str],
        languages: list[str],
        tone: str | None,
        campaign_id: str | None,
    ) -> str:
        return json.dumps(
            {
                "candidate_id": candidate_id,
                "channels": channels,
                "languages": languages,
                "tone": tone or "",
                "campaign_id": campaign_id or "",
            },
            sort_keys=True,
            ensure_ascii=False,
        )

    def _get_cached_draft(self, cache_key: str) -> DraftBundle | None:
        cached = self._draft_cache.get(cache_key)
        if cached is None:
            return None
        draft_id, generated_at = cached
        age_seconds = (_utcnow() - generated_at).total_seconds()
        if age_seconds > _draft_cache_ttl_seconds():
            self._draft_cache.pop(cache_key, None)
            return None
        bundle = self._drafts.get(draft_id)
        if bundle is None:
            self._draft_cache.pop(cache_key, None)
            return None
        return bundle

    def _cleanup_draft_cache(self) -> None:
        now = _utcnow()
        ttl = _draft_cache_ttl_seconds()
        for cache_key, (draft_id, generated_at) in list(self._draft_cache.items()):
            if draft_id not in self._drafts:
                self._draft_cache.pop(cache_key, None)
                continue
            if (now - generated_at).total_seconds() > ttl:
                self._draft_cache.pop(cache_key, None)

    def _fallback_primary_content(
        self,
        *,
        candidate_topic: str,
        candidate_summary: str,
        language: str,
        tone: str | None,
    ) -> str:
        tone_suffix = f" ({tone})" if tone else ""
        if language == "pl":
            return (
                f"{candidate_topic}: {candidate_summary} "
                f"Moja perspektywa inżynierska i praktyczne wnioski.{tone_suffix}".strip()
            )
        return (
            f"{candidate_topic}: {candidate_summary} "
            f"My engineering perspective with practical takeaways.{tone_suffix}".strip()
        )

    def _fallback_supporting_content(
        self,
        *,
        source_ref: str,
        candidate_topic: str,
        candidate_url: str,
        primary_content: str,
        language: str,
    ) -> str:
        if language == "pl":
            return (
                f"Cytując {source_ref}: {candidate_topic}. "
                f"Oryginalne źródło wiedzy: {candidate_url} | "
                f"{primary_content}"
            ).strip()
        return (
            f"Quoting {source_ref}: {candidate_topic}. "
            f"Original knowledge source: {candidate_url} | "
            f"{primary_content}"
        ).strip()

    def _build_primary_prompt(
        self,
        *,
        candidate_topic: str,
        candidate_summary: str,
        candidate_url: str,
        channel: str,
        language: str,
        tone: str | None,
    ) -> str:
        if language == "pl":
            return (
                "Role: primary\n"
                "Jesteś ekspertem budującym markę osobistą inżyniera AI.\n"
                "Napisz merytoryczny post ekspercki do publikacji.\n"
                f"Kanał: {channel}\n"
                f"Język: {language}\n"
                f"Ton: {tone or 'expert'}\n"
                f"Temat: {candidate_topic}\n"
                f"Streszczenie: {candidate_summary}\n"
                f"Źródło: {candidate_url}\n"
                "Wymagania: 1) konkret i praktyczne wnioski, 2) naturalny styl, "
                "3) bez nagłówków markdown i bez list numerowanych."
            )
        return (
            "Role: primary\n"
            "You are an AI engineering expert building a personal brand.\n"
            "Write a full expert post ready for publication.\n"
            f"Channel: {channel}\n"
            f"Language: {language}\n"
            f"Tone: {tone or 'expert'}\n"
            f"Topic: {candidate_topic}\n"
            f"Summary: {candidate_summary}\n"
            f"Source: {candidate_url}\n"
            "Requirements: 1) practical insight, 2) professional engaging tone, "
            "3) no markdown headers and no numbered lists."
        )

    def _build_supporting_prompt(
        self,
        *,
        source_ref: str,
        candidate_topic: str,
        candidate_summary: str,
        candidate_url: str,
        primary_content: str,
        channel: str,
        language: str,
        tone: str | None,
    ) -> str:
        shortened_primary = self._truncate_for_supporting_prompt(primary_content)
        if language == "pl":
            return (
                "Role: supporting\n"
                "Napisz teaser/cytat promujący główny wpis.\n"
                f"Kanał: {channel}\n"
                f"Język: {language}\n"
                f"Ton: {tone or 'short'}\n"
                f"Marka źródłowa: {source_ref}\n"
                f"Temat: {candidate_topic}\n"
                f"Streszczenie: {candidate_summary}\n"
                f"Oryginalny wpis URL: {candidate_url}\n"
                f"Kontekst głównego wpisu: {shortened_primary}\n"
                "Wymagania: max 2-3 zdania, musi zawierać zwrot 'Oryginalne źródło wiedzy: <URL>'."
            )
        return (
            "Role: supporting\n"
            "Write a short teaser/quote that redirects traffic to the primary post.\n"
            f"Channel: {channel}\n"
            f"Language: {language}\n"
            f"Tone: {tone or 'short'}\n"
            f"Primary source brand: {source_ref}\n"
            f"Topic: {candidate_topic}\n"
            f"Summary: {candidate_summary}\n"
            f"Original post URL: {candidate_url}\n"
            f"Primary post context: {shortened_primary}\n"
            "Requirements: max 2-3 sentences, include 'Original knowledge source: <URL>'."
        )

    def _truncate_for_supporting_prompt(self, text: str) -> str:
        limit = 1000
        trimmed = text.strip()
        if len(trimmed) <= limit:
            return trimmed
        return f"{trimmed[:limit].rstrip()}..."

    def _generate_many_draft_texts_with_llm_fallback(
        self,
        *,
        jobs: list[tuple[str, str, str, str]],
        actor: str,
    ) -> dict[str, str]:
        if not jobs:
            return {}
        if not self._llm_client.enabled:
            return {job_key: fallback for job_key, _prompt, fallback, _ctx in jobs}

        if len(jobs) == 1:
            job_key, prompt, fallback, audit_context = jobs[0]
            content = self._generate_draft_text_with_llm_fallback(
                prompt=prompt,
                fallback=fallback,
                actor=actor,
                audit_context=audit_context,
            )
            return {job_key: content}

        workers = min(_draft_llm_parallel_workers(), len(jobs))
        resolved: dict[str, str] = {}
        with ThreadPoolExecutor(max_workers=workers) as executor:
            future_to_job = {
                executor.submit(self._llm_client.generate_text, prompt): (
                    job_key,
                    fallback,
                    audit_context,
                )
                for job_key, prompt, fallback, audit_context in jobs
            }
            for future in as_completed(future_to_job):
                job_key, fallback, audit_context = future_to_job[future]
                try:
                    resolved[job_key] = future.result()
                except Exception as exc:
                    logger.warning("Brand Studio LLM fallback (%s): %s", audit_context, exc)
                    self._add_audit(
                        actor=actor,
                        action="draft.generate.llm",
                        status="fallback",
                        payload=f"{audit_context}:{exc}",
                    )
                    resolved[job_key] = fallback

        return resolved

    def _generate_draft_text_with_llm_fallback(
        self,
        *,
        prompt: str,
        fallback: str,
        actor: str,
        audit_context: str,
    ) -> str:
        if not self._llm_client.enabled:
            return fallback
        try:
            return self._llm_client.generate_text(prompt)
        except Exception as exc:
            logger.warning("Brand Studio LLM fallback (%s): %s", audit_context, exc)
            self._add_audit(
                actor=actor,
                action="draft.generate.llm",
                status="fallback",
                payload=f"{audit_context}:{exc}",
            )
            return fallback

    def _ensure_supporting_attribution(
        self, *, text: str, language: str, candidate_url: str
    ) -> str:
        normalized = text.lower()
        normalized_url = candidate_url.lower()
        has_url = normalized_url in normalized
        if language == "pl":
            has_phrase = "oryginalne źródło wiedzy" in normalized
            if has_phrase and has_url:
                return text
            return f"{text.rstrip()}\n\nOryginalne źródło wiedzy: {candidate_url}"
        has_phrase = "original knowledge source" in normalized
        if has_phrase and has_url:
            return text
        return f"{text.rstrip()}\n\nOriginal knowledge source: {candidate_url}"

    def queue_draft(
        self,
        *,
        draft_id: str,
        target_channel: str,
        target_language: str | None,
        target: str | None,
        target_repo: str | None,
        target_path: str | None,
        payload_override: str | None,
        actor: str,
        account_id: str | None = None,
        campaign_id: str | None = None,
        scheduled_at: datetime | None = None,
        publish_mode: Literal["manual", "auto"] = "manual",
    ) -> PublishQueueItem:
        with self._lock:
            bundle = self._drafts.get(draft_id)
            if bundle is None:
                raise KeyError("draft_not_found")

            candidate_variant = self._choose_variant(
                bundle=bundle,
                target_channel=target_channel,
                target_language=target_language,
                account_id=account_id,
            )
            if candidate_variant is None:
                raise KeyError("draft_variant_not_found")

            payload = payload_override or candidate_variant.content
            selected_account = self._resolve_account_for_queue(
                target_channel=target_channel, account_id=account_id
            )
            now = _utcnow()
            resolved_target = (
                target
                or target_repo
                or (selected_account.target if selected_account else None)
                or os.getenv("BRAND_TARGET_REPO")
            )
            item = PublishQueueItem(
                item_id=f"queue-{uuid4().hex[:10]}",
                draft_id=draft_id,
                target_channel=target_channel,
                target_language=candidate_variant.language,
                target=resolved_target,
                target_repo=resolved_target,
                target_path=target_path or _default_target_path(target_channel),
                account_id=selected_account.account_id if selected_account else None,
                account_display_name=selected_account.display_name if selected_account else None,
                payload=payload,
                status="queued",
                created_at=now,
                updated_at=now,
                campaign_id=campaign_id,
                scheduled_at=scheduled_at,
                publish_mode=publish_mode,
            )
            self._queue[item.item_id] = item
            self._persist_runtime_state()
            audit_payload = (
                f"{item.target_channel}:{item.item_id}:campaign={campaign_id}"
                if campaign_id
                else f"{item.target_channel}:{item.item_id}"
            )
            self._add_audit(
                actor=actor,
                action="queue.create",
                status="queued",
                payload=audit_payload,
            )
            return item

    def _resolve_account_for_queue(
        self,
        *,
        target_channel: str,
        account_id: str | None,
    ) -> ChannelAccount | None:
        channel = target_channel if target_channel in SUPPORTED_CHANNELS else None
        if channel is None:
            return None
        accounts = self._accounts.get(channel, {})
        if account_id:
            selected = accounts.get(account_id)
            if selected is None:
                raise ChannelAccountNotFoundError("account_not_found")
            return selected
        strategy = self._active_strategy()
        strategy_default = strategy.default_accounts.get(channel)
        if strategy_default and strategy_default in accounts:
            return accounts[strategy_default]
        return self._default_account_for_channel(channel)

    def _choose_variant(
        self,
        *,
        bundle: DraftBundle,
        target_channel: str,
        target_language: str | None,
        account_id: str | None = None,
    ) -> DraftVariant | None:
        variants = [v for v in bundle.variants if v.channel == target_channel]
        if target_language:
            lang_match = [v for v in variants if v.language == target_language]
            if lang_match:
                variants = lang_match
        if not variants:
            return None
        if account_id:
            account_match = [v for v in variants if v.account_id == account_id]
            if account_match:
                return account_match[0]
        # Prefer primary variants (account_id is None) over supporting ones
        primary_match = [v for v in variants if v.account_id is None]
        return primary_match[0] if primary_match else variants[0]

    def publish_queue_item(
        self,
        *,
        item_id: str,
        confirm_publish: bool,
        actor: str,
    ) -> PublishResult:  # pragma: no cover
        with self._lock:
            item = self._queue.get(item_id)
            if item is None:
                raise KeyError("queue_item_not_found")
            if not confirm_publish:
                raise ValueError("confirm_publish_required")
            if item.status == "published":
                raise ValueError("queue_item_already_published")

            now = _utcnow()
            queue_target = item.target or item.target_repo
            if item.target_channel in {"github", "blog"}:
                if self._publisher is None:
                    item.status = "failed"
                    item.updated_at = now
                    self._persist_runtime_state()
                    self._add_audit(
                        actor=actor,
                        action="queue.publish",
                        status="failed",
                        payload=f"{item_id}:github_not_configured",
                    )
                    self._record_account_publish_result(
                        item=item,
                        status="failed",
                        message="GitHub publisher not configured",
                        published_at=now,
                    )
                    return PublishResult(
                        success=False,
                        status="failed",
                        published_at=now,
                        message=(
                            "GitHub publisher not configured "
                            "(set GITHUB_TOKEN_BRAND and BRAND_TARGET_REPO)"
                        ),
                    )
                try:
                    result = self._publisher.publish_markdown(
                        path=item.target_path or _default_target_path(item.target_channel),
                        content=item.payload,
                        title=f"{item.target_channel}-{item.item_id}",
                    )
                    item.status = "published"
                    item.updated_at = now
                    self._persist_runtime_state()
                    self._add_audit(
                        actor=actor,
                        action="queue.publish",
                        status="published",
                        payload=f"{item.target_channel}:{item_id}",
                    )
                    self._record_account_publish_result(
                        item=item,
                        status="published",
                        message=result.message,
                        published_at=now,
                    )
                    return PublishResult(
                        success=True,
                        status="published",
                        published_at=now,
                        external_id=result.external_id,
                        url=result.url,
                        message=result.message,
                    )
                except Exception as exc:
                    item.status = "failed"
                    item.updated_at = now
                    self._persist_runtime_state()
                    self._add_audit(
                        actor=actor,
                        action="queue.publish",
                        status="failed",
                        payload=f"{item_id}:{exc}",
                    )
                    self._record_account_publish_result(
                        item=item,
                        status="failed",
                        message=f"GitHub publish failed: {exc}",
                        published_at=now,
                    )
                    return PublishResult(
                        success=False,
                        status="failed",
                        published_at=now,
                        message=f"GitHub publish failed: {exc}",
                    )

            if item.target_channel == "devto":
                if self._devto_publisher is None:
                    item.status = "failed"
                    item.updated_at = now
                    self._persist_runtime_state()
                    self._add_audit(
                        actor=actor,
                        action="queue.publish",
                        status="failed",
                        payload=f"{item_id}:devto_not_configured",
                    )
                    self._record_account_publish_result(
                        item=item,
                        status="failed",
                        message="Dev.to publisher not configured",
                        published_at=now,
                    )
                    return PublishResult(
                        success=False,
                        status="failed",
                        published_at=now,
                        message="Dev.to publisher not configured (set DEVTO_API_KEY)",
                    )
                try:
                    publish_result = self._devto_publisher.publish_markdown(
                        title=f"{item.target_channel}-{item.item_id}",
                        content=item.payload,
                        target=queue_target,
                    )
                    item.status = "published"
                    item.updated_at = now
                    self._persist_runtime_state()
                    self._add_audit(
                        actor=actor,
                        action="queue.publish",
                        status="published",
                        payload=f"{item.target_channel}:{item_id}",
                    )
                    self._record_account_publish_result(
                        item=item,
                        status="published",
                        message=publish_result.message,
                        published_at=now,
                    )
                    return PublishResult(
                        success=True,
                        status="published",
                        published_at=now,
                        external_id=publish_result.external_id,
                        url=publish_result.url,
                        message=publish_result.message,
                    )
                except Exception as exc:
                    item.status = "failed"
                    item.updated_at = now
                    self._persist_runtime_state()
                    self._add_audit(
                        actor=actor,
                        action="queue.publish",
                        status="failed",
                        payload=f"{item_id}:{exc}",
                    )
                    self._record_account_publish_result(
                        item=item,
                        status="failed",
                        message=f"Dev.to publish failed: {exc}",
                        published_at=now,
                    )
                    return PublishResult(
                        success=False,
                        status="failed",
                        published_at=now,
                        message=f"Dev.to publish failed: {exc}",
                    )

            if item.target_channel == "reddit":
                if self._reddit_publisher is None:
                    item.status = "failed"
                    item.updated_at = now
                    self._persist_runtime_state()
                    self._add_audit(
                        actor=actor,
                        action="queue.publish",
                        status="failed",
                        payload=f"{item_id}:reddit_not_configured",
                    )
                    self._record_account_publish_result(
                        item=item,
                        status="failed",
                        message="Reddit publisher not configured",
                        published_at=now,
                    )
                    return PublishResult(
                        success=False,
                        status="failed",
                        published_at=now,
                        message=(
                            "Reddit publisher not configured "
                            "(set REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET, REDDIT_REFRESH_TOKEN)"
                        ),
                    )
                try:
                    publish_result = self._reddit_publisher.publish_markdown(
                        title=f"{item.target_channel}-{item.item_id}",
                        content=item.payload,
                        subreddit=queue_target,
                    )
                    item.status = "published"
                    item.updated_at = now
                    self._persist_runtime_state()
                    self._add_audit(
                        actor=actor,
                        action="queue.publish",
                        status="published",
                        payload=f"{item.target_channel}:{item_id}",
                    )
                    self._record_account_publish_result(
                        item=item,
                        status="published",
                        message=publish_result.message,
                        published_at=now,
                    )
                    return PublishResult(
                        success=True,
                        status="published",
                        published_at=now,
                        external_id=publish_result.external_id,
                        url=publish_result.url,
                        message=publish_result.message,
                    )
                except Exception as exc:
                    item.status = "failed"
                    item.updated_at = now
                    self._persist_runtime_state()
                    self._add_audit(
                        actor=actor,
                        action="queue.publish",
                        status="failed",
                        payload=f"{item_id}:{exc}",
                    )
                    self._record_account_publish_result(
                        item=item,
                        status="failed",
                        message=f"Reddit publish failed: {exc}",
                        published_at=now,
                    )
                    return PublishResult(
                        success=False,
                        status="failed",
                        published_at=now,
                        message=f"Reddit publish failed: {exc}",
                    )

            if item.target_channel == "hashnode":
                if self._hashnode_publisher is None:
                    item.status = "failed"
                    item.updated_at = now
                    self._persist_runtime_state()
                    self._add_audit(
                        actor=actor,
                        action="queue.publish",
                        status="failed",
                        payload=f"{item_id}:hashnode_not_configured",
                    )
                    self._record_account_publish_result(
                        item=item,
                        status="failed",
                        message="Hashnode publisher not configured",
                        published_at=now,
                    )
                    return PublishResult(
                        success=False,
                        status="failed",
                        published_at=now,
                        message="Hashnode publisher not configured (set HASHNODE_TOKEN)",
                    )
                try:
                    publish_result = self._hashnode_publisher.publish_markdown(
                        title=f"{item.target_channel}-{item.item_id}",
                        content=item.payload,
                        target=queue_target,
                    )
                    item.status = "published"
                    item.updated_at = now
                    self._persist_runtime_state()
                    self._add_audit(
                        actor=actor,
                        action="queue.publish",
                        status="published",
                        payload=f"{item.target_channel}:{item_id}",
                    )
                    self._record_account_publish_result(
                        item=item,
                        status="published",
                        message=publish_result.message,
                        published_at=now,
                    )
                    return PublishResult(
                        success=True,
                        status="published",
                        published_at=now,
                        external_id=publish_result.external_id,
                        url=publish_result.url,
                        message=publish_result.message,
                    )
                except Exception as exc:
                    item.status = "failed"
                    item.updated_at = now
                    self._persist_runtime_state()
                    self._add_audit(
                        actor=actor,
                        action="queue.publish",
                        status="failed",
                        payload=f"{item_id}:{exc}",
                    )
                    self._record_account_publish_result(
                        item=item,
                        status="failed",
                        message=f"Hashnode publish failed: {exc}",
                        published_at=now,
                    )
                    return PublishResult(
                        success=False,
                        status="failed",
                        published_at=now,
                        message=f"Hashnode publish failed: {exc}",
                    )

            if item.target_channel == "linkedin":
                if self._linkedin_publisher is None:
                    item.status = "failed"
                    item.updated_at = now
                    self._persist_runtime_state()
                    self._add_audit(
                        actor=actor,
                        action="queue.publish",
                        status="failed",
                        payload=f"{item_id}:linkedin_not_configured",
                    )
                    self._record_account_publish_result(
                        item=item,
                        status="failed",
                        message="LinkedIn publisher not configured",
                        published_at=now,
                    )
                    return PublishResult(
                        success=False,
                        status="failed",
                        published_at=now,
                        message="LinkedIn publisher not configured (set LINKEDIN_ACCESS_TOKEN)",
                    )
                try:
                    publish_result = self._linkedin_publisher.publish_markdown(
                        title=f"{item.target_channel}-{item.item_id}",
                        content=item.payload,
                        target=queue_target,
                    )
                    item.status = "published"
                    item.updated_at = now
                    self._persist_runtime_state()
                    self._add_audit(
                        actor=actor,
                        action="queue.publish",
                        status="published",
                        payload=f"{item.target_channel}:{item_id}",
                    )
                    self._record_account_publish_result(
                        item=item,
                        status="published",
                        message=publish_result.message,
                        published_at=now,
                    )
                    return PublishResult(
                        success=True,
                        status="published",
                        published_at=now,
                        external_id=publish_result.external_id,
                        url=publish_result.url,
                        message=publish_result.message,
                    )
                except Exception as exc:
                    item.status = "failed"
                    item.updated_at = now
                    self._persist_runtime_state()
                    self._add_audit(
                        actor=actor,
                        action="queue.publish",
                        status="failed",
                        payload=f"{item_id}:{exc}",
                    )
                    self._record_account_publish_result(
                        item=item,
                        status="failed",
                        message=f"LinkedIn publish failed: {exc}",
                        published_at=now,
                    )
                    return PublishResult(
                        success=False,
                        status="failed",
                        published_at=now,
                        message=f"LinkedIn publish failed: {exc}",
                    )

            if item.target_channel == "medium":
                if self._medium_publisher is None:
                    item.status = "failed"
                    item.updated_at = now
                    self._persist_runtime_state()
                    self._add_audit(
                        actor=actor,
                        action="queue.publish",
                        status="failed",
                        payload=f"{item_id}:medium_not_configured",
                    )
                    self._record_account_publish_result(
                        item=item,
                        status="failed",
                        message="Medium publisher not configured",
                        published_at=now,
                    )
                    return PublishResult(
                        success=False,
                        status="failed",
                        published_at=now,
                        message="Medium publisher not configured (set MEDIUM_TOKEN)",
                    )
                try:
                    publish_result = self._medium_publisher.publish_markdown(
                        title=f"{item.target_channel}-{item.item_id}",
                        content=item.payload,
                        target=queue_target,
                    )
                    item.status = "published"
                    item.updated_at = now
                    self._persist_runtime_state()
                    self._add_audit(
                        actor=actor,
                        action="queue.publish",
                        status="published",
                        payload=f"{item.target_channel}:{item_id}",
                    )
                    self._record_account_publish_result(
                        item=item,
                        status="published",
                        message=publish_result.message,
                        published_at=now,
                    )
                    return PublishResult(
                        success=True,
                        status="published",
                        published_at=now,
                        external_id=publish_result.external_id,
                        url=publish_result.url,
                        message=publish_result.message,
                    )
                except Exception as exc:
                    item.status = "failed"
                    item.updated_at = now
                    self._persist_runtime_state()
                    self._add_audit(
                        actor=actor,
                        action="queue.publish",
                        status="failed",
                        payload=f"{item_id}:{exc}",
                    )
                    self._record_account_publish_result(
                        item=item,
                        status="failed",
                        message=f"Medium publish failed: {exc}",
                        published_at=now,
                    )
                    return PublishResult(
                        success=False,
                        status="failed",
                        published_at=now,
                        message=f"Medium publish failed: {exc}",
                    )

            if item.target_channel in {"hf_blog", "hf_spaces"}:
                if self._hf_publisher is None:
                    item.status = "failed"
                    item.updated_at = now
                    self._persist_runtime_state()
                    self._add_audit(
                        actor=actor,
                        action="queue.publish",
                        status="failed",
                        payload=f"{item_id}:hf_not_configured",
                    )
                    self._record_account_publish_result(
                        item=item,
                        status="failed",
                        message="HF publisher not configured",
                        published_at=now,
                    )
                    return PublishResult(
                        success=False,
                        status="failed",
                        published_at=now,
                        message="HF publisher not configured (set HF_TOKEN)",
                    )
                try:
                    publish_result = self._hf_publisher.publish_markdown(
                        channel=item.target_channel,
                        title=f"{item.target_channel}-{item.item_id}",
                        content=item.payload,
                        target=queue_target,
                    )
                    item.status = "published"
                    item.updated_at = now
                    self._persist_runtime_state()
                    self._add_audit(
                        actor=actor,
                        action="queue.publish",
                        status="published",
                        payload=f"{item.target_channel}:{item_id}",
                    )
                    self._record_account_publish_result(
                        item=item,
                        status="published",
                        message=publish_result.message,
                        published_at=now,
                    )
                    return PublishResult(
                        success=True,
                        status="published",
                        published_at=now,
                        external_id=publish_result.external_id,
                        url=publish_result.url,
                        message=publish_result.message,
                    )
                except Exception as exc:
                    item.status = "failed"
                    item.updated_at = now
                    self._persist_runtime_state()
                    self._add_audit(
                        actor=actor,
                        action="queue.publish",
                        status="failed",
                        payload=f"{item_id}:{exc}",
                    )
                    self._record_account_publish_result(
                        item=item,
                        status="failed",
                        message=f"HF publish failed: {exc}",
                        published_at=now,
                    )
                    return PublishResult(
                        success=False,
                        status="failed",
                        published_at=now,
                        message=f"HF publish failed: {exc}",
                    )

            if item.target_channel == "x":
                item.status = "published"
                item.updated_at = now
                self._persist_runtime_state()
                self._add_audit(
                    actor=actor,
                    action="queue.publish",
                    status="manual",
                    payload=f"{item_id}:{item.target_channel}",
                )
                self._record_account_publish_result(
                    item=item,
                    status="published",
                    message="X publish marked as manual-complete in MVP",
                    published_at=now,
                )
                return PublishResult(
                    success=True,
                    status="published",
                    published_at=now,
                    external_id=f"manual-{item_id}",
                    message="X publish marked as manual-complete in MVP",
                )

            item.status = "failed"
            item.updated_at = now
            self._persist_runtime_state()
            self._add_audit(
                actor=actor,
                action="queue.publish",
                status="failed",
                payload=f"{item_id}:{item.target_channel}_connector_not_implemented",
            )
            self._record_account_publish_result(
                item=item,
                status="failed",
                message=f"Connector for channel '{item.target_channel}' is not implemented yet",
                published_at=now,
            )
            return PublishResult(
                success=False,
                status="failed",
                published_at=now,
                message=f"Connector for channel '{item.target_channel}' is not implemented yet",
            )

    def queue_items(self, *, campaign_id: str | None = None) -> list[PublishQueueItem]:
        self.process_scheduled_queue()
        with self._lock:
            items = list(self._queue.values())
            if campaign_id:
                items = [it for it in items if it.campaign_id == campaign_id]
            items.sort(key=lambda it: it.created_at, reverse=True)
            return items

    def process_scheduled_queue(self) -> int:
        now = _utcnow()
        with self._lock:
            due_item_ids = [
                item.item_id
                for item in self._queue.values()
                if item.status == "queued"
                and item.publish_mode == "auto"
                and item.scheduled_at is not None
                and item.scheduled_at <= now
            ]
        processed = 0
        for item_id in due_item_ids:
            try:
                self.publish_queue_item(
                    item_id=item_id,
                    confirm_publish=True,
                    actor="system:scheduler",
                )
                processed += 1
            except Exception as exc:
                logger.warning("process_scheduled_queue: failed to publish %s: %s", item_id, exc)
        return processed

    def audit_items(self) -> list[BrandStudioAuditEntry]:
        with self._lock:
            return list(reversed(self._audit))

    def integrations(self) -> list[IntegrationDescriptor]:  # pragma: no cover
        strategy = self._active_strategy()
        github_token = (os.getenv("GITHUB_TOKEN_BRAND") or "").strip()
        github_repo = (os.getenv("BRAND_TARGET_REPO") or "").strip()
        x_token = (os.getenv("X_API_TOKEN") or "").strip()
        devto_key = (os.getenv("DEVTO_API_KEY") or "").strip()
        reddit_client_id = (os.getenv("REDDIT_CLIENT_ID") or "").strip()
        reddit_client_secret = (os.getenv("REDDIT_CLIENT_SECRET") or "").strip()
        reddit_refresh_token = (os.getenv("REDDIT_REFRESH_TOKEN") or "").strip()
        hashnode_token = (os.getenv("HASHNODE_TOKEN") or "").strip()
        linkedin_token = (os.getenv("LINKEDIN_ACCESS_TOKEN") or "").strip()
        medium_token = (os.getenv("MEDIUM_TOKEN") or "").strip()
        hf_token = (os.getenv("HF_TOKEN") or "").strip()

        items = [
            IntegrationDescriptor(
                id="github_publish",
                name="GitHub publish",
                requires_key=True,
                status="configured" if github_token and github_repo else "missing",
                details=(
                    "GitHub connector ready"
                    if github_token and github_repo
                    else "Missing GITHUB_TOKEN_BRAND or BRAND_TARGET_REPO"
                ),
                key_hint="GITHUB_TOKEN_BRAND",
                masked_secret=_masked_secret(github_token),
                configured_target=github_repo or None,
            ),
            IntegrationDescriptor(
                id="rss",
                name="RSS feeds",
                requires_key=False,
                status="configured" if strategy.rss_urls else "missing",
                details=(
                    f"Configured feeds: {len(strategy.rss_urls)}"
                    if strategy.rss_urls
                    else "No RSS feeds configured"
                ),
            ),
            IntegrationDescriptor(
                id="hn",
                name="Hacker News",
                requires_key=False,
                status="configured",
                details="Public API",
            ),
            IntegrationDescriptor(
                id="arxiv",
                name="arXiv",
                requires_key=False,
                status="configured",
                details="Public API",
            ),
            IntegrationDescriptor(
                id="x",
                name="X / Twitter",
                requires_key=True,
                status="configured" if x_token else "missing",
                details=(
                    "Token present (manual publish only)"
                    if x_token
                    else "Missing X_API_TOKEN"
                ),
                key_hint="X_API_TOKEN",
                masked_secret=_masked_secret(x_token),
            ),
            IntegrationDescriptor(
                id="devto_publish",
                name="Dev.to publish",
                requires_key=True,
                status="configured" if devto_key else "missing",
                details=(
                    "Dev.to token present"
                    if devto_key
                    else "Missing DEVTO_API_KEY"
                ),
                key_hint="DEVTO_API_KEY",
                masked_secret=_masked_secret(devto_key),
            ),
            IntegrationDescriptor(
                id="reddit_publish",
                name="Reddit publish",
                requires_key=True,
                status=(
                    "configured"
                    if self._reddit_publisher is not None
                    else (
                        "invalid"
                        if reddit_client_id or reddit_client_secret or reddit_refresh_token
                        else "missing"
                    )
                ),
                details=(
                    "Reddit connector ready"
                    if self._reddit_publisher is not None
                    else (
                        "Incomplete Reddit credentials "
                        "(required: REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET, REDDIT_REFRESH_TOKEN)"
                        if reddit_client_id or reddit_client_secret or reddit_refresh_token
                        else "Missing REDDIT_CLIENT_ID/REDDIT_CLIENT_SECRET/REDDIT_REFRESH_TOKEN"
                    )
                ),
                key_hint="REDDIT_CLIENT_ID/REDDIT_CLIENT_SECRET/REDDIT_REFRESH_TOKEN",
                masked_secret=_masked_secret(reddit_refresh_token or reddit_client_secret),
            ),
            IntegrationDescriptor(
                id="hashnode_publish",
                name="Hashnode publish",
                requires_key=True,
                status="configured" if self._hashnode_publisher is not None else "missing",
                details=(
                    "Hashnode connector ready"
                    if self._hashnode_publisher is not None
                    else "Missing HASHNODE_TOKEN"
                ),
                key_hint="HASHNODE_TOKEN",
                masked_secret=_masked_secret(hashnode_token),
            ),
            IntegrationDescriptor(
                id="linkedin_publish",
                name="LinkedIn publish",
                requires_key=True,
                status="configured" if self._linkedin_publisher is not None else "missing",
                details=(
                    "LinkedIn connector ready"
                    if self._linkedin_publisher is not None
                    else "Missing LINKEDIN_ACCESS_TOKEN"
                ),
                key_hint="LINKEDIN_ACCESS_TOKEN",
                masked_secret=_masked_secret(linkedin_token),
            ),
            IntegrationDescriptor(
                id="medium_publish",
                name="Medium publish",
                requires_key=True,
                status="configured" if self._medium_publisher is not None else "missing",
                details=(
                    "Medium connector ready"
                    if self._medium_publisher is not None
                    else "Missing MEDIUM_TOKEN"
                ),
                key_hint="MEDIUM_TOKEN",
                masked_secret=_masked_secret(medium_token),
            ),
            IntegrationDescriptor(
                id="hf_blog_publish",
                name="HF Blog publish",
                requires_key=True,
                status="configured" if self._hf_publisher is not None else "missing",
                details=(
                    "HF blog connector ready"
                    if self._hf_publisher is not None
                    else "Missing HF_TOKEN"
                ),
                key_hint="HF_TOKEN",
                masked_secret=_masked_secret(hf_token),
            ),
            IntegrationDescriptor(
                id="hf_spaces_publish",
                name="HF Spaces publish",
                requires_key=True,
                status="configured" if self._hf_publisher is not None else "missing",
                details=(
                    "HF spaces connector ready"
                    if self._hf_publisher is not None
                    else "Missing HF_TOKEN"
                ),
                key_hint="HF_TOKEN",
                masked_secret=_masked_secret(hf_token),
            ),
        ]
        return items

    def test_integration(
        self,
        integration_id: IntegrationId,
        *,
        actor: str,
    ) -> IntegrationTestResponse:  # pragma: no cover
        now = _utcnow()
        status = "invalid"
        success = False
        message = "Unsupported integration"

        try:
            if integration_id == "github_publish":
                if self._publisher is None:
                    status = "missing"
                    message = "GitHub publisher not configured"
                else:
                    self._publisher.validate_connection()
                    status = "configured"
                    success = True
                    message = "GitHub API reachable"
            elif integration_id == "rss":
                urls = self._active_strategy().rss_urls
                if not urls:
                    status = "missing"
                    message = "No RSS URLs configured"
                else:
                    items = fetch_rss_items(urls[:1], max_items_per_feed=1)
                    status = "configured"
                    success = True
                    message = f"RSS test ok ({len(items)} item(s))"
            elif integration_id == "hn":
                items = fetch_hn_items(max_items=1)
                status = "configured"
                success = True
                message = f"HN test ok ({len(items)} item(s))"
            elif integration_id == "arxiv":
                items = fetch_arxiv_items(max_items=1)
                status = "configured"
                success = True
                message = f"arXiv test ok ({len(items)} item(s))"
            elif integration_id == "x":
                token = (os.getenv("X_API_TOKEN") or "").strip()
                if not token:
                    status = "missing"
                    message = "Missing X_API_TOKEN"
                else:
                    status = "configured"
                    success = True
                    message = "Token present (manual publish in MVP)"
            elif integration_id == "devto_publish":
                if self._devto_publisher is None:
                    status = "missing"
                    message = "Missing DEVTO_API_KEY"
                else:
                    self._devto_publisher.validate_connection()
                    status = "configured"
                    success = True
                    message = "Dev.to API reachable"
            elif integration_id == "reddit_publish":
                if self._reddit_publisher is None:
                    client_id = (os.getenv("REDDIT_CLIENT_ID") or "").strip()
                    client_secret = (os.getenv("REDDIT_CLIENT_SECRET") or "").strip()
                    refresh_token = (os.getenv("REDDIT_REFRESH_TOKEN") or "").strip()
                    if client_id or client_secret or refresh_token:
                        status = "invalid"
                        success = False
                        message = (
                            "Incomplete Reddit credentials "
                            "(required: REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET, "
                            "REDDIT_REFRESH_TOKEN)"
                        )
                    else:
                        status = "missing"
                        message = (
                            "Missing REDDIT_CLIENT_ID/REDDIT_CLIENT_SECRET/"
                            "REDDIT_REFRESH_TOKEN"
                        )
                else:
                    self._reddit_publisher.validate_connection()
                    status = "configured"
                    success = True
                    message = "Reddit API reachable"
            elif integration_id == "hashnode_publish":
                if self._hashnode_publisher is None:
                    status = "missing"
                    message = "Missing HASHNODE_TOKEN"
                else:
                    self._hashnode_publisher.validate_connection()
                    status = "configured"
                    success = True
                    message = "Hashnode API reachable"
            elif integration_id == "linkedin_publish":
                if self._linkedin_publisher is None:
                    status = "missing"
                    message = "Missing LINKEDIN_ACCESS_TOKEN"
                else:
                    self._linkedin_publisher.validate_connection()
                    status = "configured"
                    success = True
                    message = "LinkedIn API reachable"
            elif integration_id == "medium_publish":
                if self._medium_publisher is None:
                    status = "missing"
                    message = "Missing MEDIUM_TOKEN"
                else:
                    self._medium_publisher.validate_connection()
                    status = "configured"
                    success = True
                    message = "Medium API reachable"
            elif integration_id in {"hf_blog_publish", "hf_spaces_publish"}:
                if self._hf_publisher is None:
                    status = "missing"
                    message = "Missing HF_TOKEN"
                else:
                    self._hf_publisher.validate_connection()
                    status = "configured"
                    success = True
                    message = "Hugging Face API reachable"
        except Exception as exc:
            logger.exception("Brand Studio integration test failed [%s]", integration_id)
            status = "invalid"
            message = f"Integration test failed: {exc}"

        self._last_integration_test[integration_id] = now
        self._persist_runtime_state()
        self._add_audit(
            actor=actor,
            action="integration.test",
            status="ok" if success else "failed",
            payload=f"{integration_id}:{status}",
        )
        return IntegrationTestResponse(
            id=integration_id,
            success=success,
            status=status,
            tested_at=now,
            message=message,
        )

    def _add_audit(self, *, actor: str, action: str, status: str, payload: str) -> None:
        entry: BrandStudioAuditEntry
        with self._lock:
            payload_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()
            payload_summary = payload.strip()
            if len(payload_summary) > 220:
                payload_summary = payload_summary[:220] + "..."
            entry = BrandStudioAuditEntry(
                id=f"audit-{uuid4().hex[:10]}",
                actor=actor,
                action=action,
                status=status,
                payload_hash=payload_hash,
                timestamp=_utcnow(),
                details=payload_summary or None,
            )
            self._audit.append(entry)
            self._persist_runtime_state()
        try:
            self._audit_publisher.publish_entry(entry)
        except Exception as exc:  # pragma: no cover - defensive guard
            logger.warning("Brand Studio audit publish failed: %s", exc)

    # ---- Monitoring feature guard ----

    def _monitoring_enabled(self) -> bool:
        raw = (os.getenv("FEATURE_BRAND_STUDIO_MONITORING") or "true").strip().lower()
        return raw not in {"0", "false", "off", "no"}

    # ---- Keywords CRUD ----

    def keywords_list(self) -> list[BrandKeyword]:
        with self._lock:
            items = list(self._keywords.values())
            items.sort(key=lambda it: it.phrase.lower())
            return items

    def keyword_create(self, payload: BrandKeywordCreateRequest, *, actor: str) -> BrandKeyword:
        with self._lock:
            keyword_id = f"kw-{uuid4().hex[:8]}"
            now = _utcnow()
            item = BrandKeyword(
                keyword_id=keyword_id,
                phrase=payload.phrase,
                keyword_type=payload.keyword_type,
                priority=payload.priority,
                active=payload.active,
                created_at=now,
            )
            self._keywords[keyword_id] = item
            self._persist_monitoring_state()
            self._add_audit(actor=actor, action="keyword.create", status="ok", payload=keyword_id)
            return item

    def keyword_update(
        self, keyword_id: str, payload: BrandKeywordUpdateRequest, *, actor: str
    ) -> BrandKeyword:
        with self._lock:
            current = self._keywords.get(keyword_id)
            if current is None:
                raise KeyError("keyword_not_found")
            updates = payload.model_dump(exclude_none=True)
            updated = current.model_copy(update=updates)
            self._keywords[keyword_id] = updated
            self._persist_monitoring_state()
            self._add_audit(actor=actor, action="keyword.update", status="ok", payload=keyword_id)
            return updated

    def keyword_delete(self, keyword_id: str, *, actor: str) -> None:
        with self._lock:
            if keyword_id not in self._keywords:
                raise KeyError("keyword_not_found")
            del self._keywords[keyword_id]
            self._persist_monitoring_state()
            self._add_audit(actor=actor, action="keyword.delete", status="ok", payload=keyword_id)

    # ---- Base Sources CRUD ----

    def base_sources_list(self) -> list[BrandBaseSource]:
        with self._lock:
            items = list(self._base_sources.values())
            items.sort(key=lambda it: it.name.lower())
            return items

    def base_source_create(
        self, payload: BrandBaseSourceCreateRequest, *, actor: str
    ) -> BrandBaseSource:
        with self._lock:
            canonical = _canonical_url(payload.base_url)
            for existing in self._base_sources.values():
                if _canonical_url(existing.base_url) == canonical:
                    raise ValueError("base_source_url_duplicate")
            source_id = f"src-{uuid4().hex[:8]}"
            now = _utcnow()
            item = BrandBaseSource(
                source_id=source_id,
                name=payload.name,
                base_url=canonical,
                channel=payload.channel,
                priority=payload.priority,
                enabled=payload.enabled,
                owner_tag=payload.owner_tag,
                created_at=now,
            )
            self._base_sources[source_id] = item
            self._persist_monitoring_state()
            self._add_audit(
                actor=actor, action="base_source.create", status="ok", payload=source_id
            )
            return item

    def base_source_update(
        self, source_id: str, payload: BrandBaseSourceUpdateRequest, *, actor: str
    ) -> BrandBaseSource:
        with self._lock:
            current = self._base_sources.get(source_id)
            if current is None:
                raise KeyError("base_source_not_found")
            updates = payload.model_dump(exclude_none=True)
            if "base_url" in updates:
                updates["base_url"] = _canonical_url(updates["base_url"])
            updated = current.model_copy(update=updates)
            self._base_sources[source_id] = updated
            self._persist_monitoring_state()
            self._add_audit(
                actor=actor, action="base_source.update", status="ok", payload=source_id
            )
            return updated

    def base_source_delete(self, source_id: str, *, actor: str) -> None:
        with self._lock:
            if source_id not in self._base_sources:
                raise KeyError("base_source_not_found")
            del self._base_sources[source_id]
            self._persist_monitoring_state()
            self._add_audit(
                actor=actor, action="base_source.delete", status="ok", payload=source_id
            )

    def _monitoring_schedule_interval_seconds(self) -> int | None:
        cron_expr = (os.getenv("BRAND_STUDIO_MONITORING_SCHEDULE_CRON") or "").strip().lower()
        if cron_expr:
            cron_aliases = {
                "@hourly": 3600,
                "@daily": 86400,
                "@weekly": 604800,
            }
            if cron_expr in cron_aliases:
                return cron_aliases[cron_expr]
            match = re.fullmatch(r"\*/(\d+)\s+\*\s+\*\s+\*\s+\*", cron_expr)
            if match:
                minutes = int(match.group(1))
                if minutes > 0:
                    return minutes * 60
            logger.warning(
                "Unsupported BRAND_STUDIO_MONITORING_SCHEDULE_CRON format: %s "
                "(supported: @hourly/@daily/@weekly or */N * * * *)",
                cron_expr,
            )
            return None

        interval_minutes_raw = (os.getenv("BRAND_STUDIO_MONITORING_SCHEDULE_MINUTES") or "").strip()
        if not interval_minutes_raw:
            return None
        try:
            interval_minutes = int(interval_minutes_raw)
        except ValueError:
            logger.warning(
                "Invalid BRAND_STUDIO_MONITORING_SCHEDULE_MINUTES value: %s",
                interval_minutes_raw,
            )
            return None
        if interval_minutes <= 0:
            return None
        return interval_minutes * 60

    def run_scheduled_monitoring_scan_if_due(self) -> bool:
        if not self._monitoring_enabled():
            return False
        interval_seconds = self._monitoring_schedule_interval_seconds()
        if interval_seconds is None:
            return False

        now = _utcnow()
        with self._lock:
            if not any(kw.active for kw in self._keywords.values()):
                return False
            last_scan_at = self._scans[-1].scanned_at if self._scans else None
            if last_scan_at and (now - last_scan_at).total_seconds() < interval_seconds:
                return False
            request_id = f"auto-scan:{int(now.timestamp()) // interval_seconds}"
            if request_id in self._monitoring_request_id_to_scan:
                return False

        try:
            self.monitoring_scan(
                BrandMonitoringScanRequest(request_id=request_id),
                actor="system:scheduler",
            )
            return True
        except Exception as exc:  # pragma: no cover - defensive fallback
            logger.warning("Brand Studio scheduled monitoring scan failed: %s", exc)
            return False

    # ---- Monitoring scan ----

    def _classify_result(
        self, url: str, snippet: str, base_sources: dict[str, BrandBaseSource]
    ) -> tuple[SearchResultClass, bool, str | None]:
        """Classify a search result relative to known base sources.

        Args:
            url: The result URL to classify.
            snippet: The result snippet text.
            base_sources: Snapshot of known brand base sources for ownership matching.

        Note: only 'www.' is stripped when comparing domains; other subdomains
        (mobile, amp, etc.) are not normalised. Register separate base sources
        for those variants if needed.
        """
        result_domain = urlsplit(url).netloc.lower().lstrip("www.")
        for src in base_sources.values():
            src_domain = urlsplit(src.base_url).netloc.lower().lstrip("www.")
            if result_domain == src_domain or url.startswith(src.base_url):
                return "owned_source", True, src.source_id
        snippet_lower = snippet.lower()
        has_positive = any(kw in snippet_lower for kw in _POSITIVE_SNIPPET_KEYWORDS)
        has_risk = any(kw in snippet_lower for kw in _RISK_SNIPPET_KEYWORDS)
        has_neutral = any(kw in snippet_lower for kw in _NEUTRAL_SNIPPET_KEYWORDS)
        # Apply explicit priority when multiple categories match: risk > positive > neutral.
        if has_risk:
            return "brand_mention_risk", False, None
        if has_positive:
            return "brand_mention_positive", False, None
        if has_neutral:
            return "brand_mention_neutral", False, None
        return "unrelated", False, None

    def _stub_search_results(self, keyword: BrandKeyword) -> list[dict[str, object]]:
        return [
            {
                "url": f"https://example.com/{keyword.phrase.replace(' ', '-')}-result-1",
                "title": f"{keyword.phrase} – Overview",
                "snippet": f"Content about {keyword.phrase} from example.com.",
                "position": 1,
            },
            {
                "url": f"https://news.ycombinator.com/item?q={keyword.phrase.replace(' ', '+')}",
                "title": f"HN: Discussion on {keyword.phrase}",
                "snippet": f"Hacker News discussion mentioning {keyword.phrase}.",
                "position": 2,
            },
        ]

    def monitoring_scan(
        self, payload: BrandMonitoringScanRequest, *, actor: str
    ) -> BrandMonitoringScanResponse:
        # ---- Phase 1: read state under lock, check idempotency ----
        with self._lock:
            if payload.request_id and payload.request_id in self._monitoring_request_id_to_scan:
                cached_scan_id = self._monitoring_request_id_to_scan[payload.request_id]
                cached_scan = next(
                    (s for s in self._scans if s.scan_id == cached_scan_id), None
                )
                if cached_scan:
                    results = [
                        r for r in self._scan_results if r.scan_id == cached_scan_id
                    ]
                    return BrandMonitoringScanResponse(scan=cached_scan, results=results)

            if payload.keyword_ids:
                keywords_to_scan = [
                    kw
                    for kw in self._keywords.values()
                    if kw.keyword_id in payload.keyword_ids
                ]
            else:
                keywords_to_scan = [kw for kw in self._keywords.values() if kw.active]

            # Snapshot mutable state needed for classification
            base_sources_snapshot = dict(self._base_sources)
            google_cse = self._google_cse

        # ---- Phase 2: external API calls outside the lock ----
        scan_id = f"scan-{uuid4().hex[:8]}"
        now = _utcnow()
        all_results: list[BrandSearchResult] = []
        scan_status: Literal["completed", "partial", "failed"] = "completed"
        scan_message: str | None = None
        failed_keyword_ids: list[str] = []

        for kw in keywords_to_scan:
            try:
                if google_cse is not None:
                    raw_results = google_cse.search(kw.phrase)
                else:
                    raw_results = self._stub_search_results(kw)
                for raw in raw_results:
                    classification, maps_to_src, src_id = self._classify_result(
                        str(raw["url"]), str(raw["snippet"]), base_sources_snapshot
                    )
                    result = BrandSearchResult(
                        result_id=f"res-{uuid4().hex[:8]}",
                        scan_id=scan_id,
                        keyword_id=kw.keyword_id,
                        url=str(raw["url"]),
                        title=str(raw["title"]),
                        snippet=str(raw["snippet"]),
                        position=int(raw["position"]),
                        scanned_at=now,
                        classification=classification,
                        maps_to_base_source=maps_to_src,
                        base_source_id=src_id,
                    )
                    all_results.append(result)
            except (RuntimeError, OSError, ValueError) as exc:
                scan_status = "partial"
                if scan_message is None:
                    scan_message = f"Partial failure on keyword {kw.keyword_id}: {exc}"
                failed_keyword_ids.append(kw.keyword_id)

        # ---- Phase 3: update state under lock ----
        with self._lock:
            scan = BrandMonitoringScan(
                scan_id=scan_id,
                keywords_scanned=[kw.keyword_id for kw in keywords_to_scan],
                total_results=len(all_results),
                scanned_at=now,
                status=scan_status,
                message=scan_message,
            )
            self._scans.append(scan)
            self._scan_results.extend(all_results)
            if payload.request_id:
                self._monitoring_request_id_to_scan[payload.request_id] = scan_id
            self._persist_monitoring_state()
            self._add_audit(
                actor=actor,
                action="monitoring.scan",
                status="ok",
                payload=(
                    f"{scan_id}:keywords={len(keywords_to_scan)}:results={len(all_results)}"
                ),
            )
            for kw_id in failed_keyword_ids:
                self._add_audit(
                    actor=actor,
                    action="monitoring.scan",
                    status="partial",
                    payload=f"{scan_id}:kw={kw_id}:failed",
                )
            return BrandMonitoringScanResponse(scan=scan, results=all_results)

    def monitoring_results(self, *, scan_id: str | None = None) -> list[BrandSearchResult]:
        with self._lock:
            if scan_id:
                return [r for r in self._scan_results if r.scan_id == scan_id]
            return list(self._scan_results)

    def monitoring_summary(self) -> BrandMonitoringSummary:
        self.process_scheduled_queue()
        with self._lock:
            total_results = len(self._scan_results)
            owned_count = sum(1 for r in self._scan_results if r.maps_to_base_source)
            risk_count = sum(
                1 for r in self._scan_results if r.classification == "brand_mention_risk"
            )
            coverage = (owned_count / total_results) if total_results > 0 else 0.0
            last_scan_at = self._scans[-1].scanned_at if self._scans else None
            return BrandMonitoringSummary(
                total_keywords=len(self._keywords),
                active_keywords=sum(1 for kw in self._keywords.values() if kw.active),
                total_base_sources=len(self._base_sources),
                total_results=total_results,
                owned_source_coverage=coverage,
                risk_count=risk_count,
                last_scan_at=last_scan_at,
            )

    # ---- Campaigns CRUD ----

    def campaigns_list(self) -> list[BrandCampaign]:
        with self._lock:
            items = list(self._campaigns.values())
            items.sort(key=lambda it: it.created_at, reverse=True)
            return items

    def campaign_create(
        self, payload: BrandCampaignCreateRequest, *, actor: str
    ) -> BrandCampaign:
        with self._lock:
            campaign_id = f"camp-{uuid4().hex[:8]}"
            now = _utcnow()
            strategy_id = payload.strategy_id or self._active_strategy_id
            item = BrandCampaign(
                campaign_id=campaign_id,
                name=payload.name,
                strategy_id=strategy_id,
                source_scan_id=payload.source_scan_id,
                linked_keyword_ids=list(payload.linked_keyword_ids),
                linked_result_ids=list(payload.linked_result_ids),
                channels=list(payload.channels),
                status="draft",
                created_at=now,
                updated_at=now,
            )
            self._campaigns[campaign_id] = item
            self._persist_monitoring_state()
            self._add_audit(
                actor=actor, action="campaign.create", status="ok", payload=campaign_id
            )
            return item

    def campaign_get(self, campaign_id: str) -> BrandCampaign:
        with self._lock:
            item = self._campaigns.get(campaign_id)
            if item is None:
                raise KeyError("campaign_not_found")
            return item

    def campaign_update(
        self, campaign_id: str, payload: BrandCampaignUpdateRequest, *, actor: str
    ) -> BrandCampaign:
        with self._lock:
            current = self._campaigns.get(campaign_id)
            if current is None:
                raise KeyError("campaign_not_found")
            updates = payload.model_dump(exclude_none=True)
            updates["updated_at"] = _utcnow()
            updated = current.model_copy(update=updates)
            self._campaigns[campaign_id] = updated
            self._persist_monitoring_state()
            self._add_audit(
                actor=actor, action="campaign.update", status="ok", payload=campaign_id
            )
            return updated

    def campaign_run(
        self, campaign_id: str, *, request_id: str | None = None, actor: str
    ) -> BrandCampaignRunResponse:
        with self._lock:
            item = self._campaigns.get(campaign_id)
            if item is None:
                raise KeyError("campaign_not_found")
            if item.status in {"completed", "failed", "cancelled"}:
                raise ValueError("campaign_already_terminal")
            run_key = f"{campaign_id}:{request_id}" if request_id else None
            if run_key and run_key in self._campaign_run_request_ids:
                return BrandCampaignRunResponse(
                    campaign_id=campaign_id,
                    status=item.status,
                    message="Idempotent: campaign run already initiated",
                    draft_ids=list(item.draft_ids),
                    queue_ids=list(item.queue_ids),
                )
            now = _utcnow()
            created_draft_ids: list[str] = []
            created_queue_ids: list[str] = []
            failed_queue_count = 0

            if item.linked_result_ids:
                strategy = self._active_strategy()
                languages = list(strategy.draft_languages)
                for result_id in item.linked_result_ids:
                    result = next(
                        (r for r in self._scan_results if r.result_id == result_id), None
                    )
                    if result is None:
                        continue
                    virtual_id = f"cand-campaign-{uuid4().hex[:8]}"
                    breakdown = OpportunityScoreBreakdown(
                        relevance=0.5,
                        timeliness=0.5,
                        authority_fit=0.5,
                        risk_penalty=0.0,
                        final_score=0.5,
                        reasons=["campaign-linked monitoring result"],
                    )
                    virtual_candidate = ContentCandidate(
                        id=virtual_id,
                        source="monitoring",
                        url=result.url,
                        topic=result.title,
                        summary=result.snippet,
                        language="en",
                        score=0.5,
                        age_minutes=0,
                        score_breakdown=breakdown,
                        reasons=["campaign-linked monitoring result"],
                    )
                    self._candidates.append(virtual_candidate)
                    draft = self.generate_draft(
                        candidate_id=virtual_id,
                        channels=list(item.channels),
                        languages=languages,
                        tone=None,
                        actor=actor,
                        campaign_id=campaign_id,
                    )
                    created_draft_ids.append(draft.draft_id)
                    for channel in item.channels:
                        try:
                            queue_item = self.queue_draft(
                                draft_id=draft.draft_id,
                                target_channel=channel,
                                target_language=languages[0] if languages else "en",
                                target=None,
                                target_repo=None,
                                target_path=None,
                                payload_override=None,
                                actor=actor,
                                campaign_id=campaign_id,
                            )
                            created_queue_ids.append(queue_item.item_id)
                        except (ValueError, KeyError, RuntimeError) as exc:
                            failed_queue_count += 1
                            logger.warning(
                                "campaign_run: failed to queue draft %s for channel %s: %s",
                                draft.draft_id,
                                channel,
                                exc,
                                exc_info=True,
                            )

            updated = item.model_copy(
                update={
                    "status": "running",
                    "updated_at": now,
                    "draft_ids": list(item.draft_ids) + created_draft_ids,
                    "queue_ids": list(item.queue_ids) + created_queue_ids,
                }
            )
            self._campaigns[campaign_id] = updated
            if run_key:
                self._campaign_run_request_ids.add(run_key)
            self._persist_monitoring_state()
            self._add_audit(
                actor=actor,
                action="campaign.run",
                status="ok",
                payload=f"{campaign_id}:drafts={len(created_draft_ids)}:queued={len(created_queue_ids)}",
            )
            parts = [
                f"Campaign started. Created {len(created_draft_ids)} draft(s), "
                f"{len(created_queue_ids)} queue item(s)."
            ]
            if failed_queue_count:
                parts.append(f" {failed_queue_count} queue operation(s) failed (see audit).")
            msg = "".join(parts)
            return BrandCampaignRunResponse(
                campaign_id=campaign_id,
                status="running",
                message=msg,
                draft_ids=created_draft_ids,
                queue_ids=created_queue_ids,
            )

    def campaign_link_draft(self, campaign_id: str, draft_id: str, *, actor: str) -> BrandCampaign:
        with self._lock:
            campaign = self._campaigns.get(campaign_id)
            if campaign is None:
                raise KeyError("campaign_not_found")
            bundle = self._drafts.get(draft_id)
            if bundle is None:
                raise KeyError("draft_not_found")
            updated_draft_ids = list(campaign.draft_ids)
            if draft_id not in updated_draft_ids:
                updated_draft_ids.append(draft_id)
            updated_bundle = bundle.model_copy(update={"campaign_id": campaign_id})
            self._drafts[draft_id] = updated_bundle
            updated_camp = campaign.model_copy(
                update={"draft_ids": updated_draft_ids, "updated_at": _utcnow()}
            )
            self._campaigns[campaign_id] = updated_camp
            self._persist_monitoring_state()
            self._add_audit(
                actor=actor,
                action="campaign.link_draft",
                status="ok",
                payload=f"{campaign_id}:draft={draft_id}",
            )
            return updated_camp

    # ---- Monitoring persistence ----

    def _resolve_monitoring_file(self) -> Path:
        raw = (os.getenv("BRAND_STUDIO_MONITORING_FILE") or "").strip()
        if raw:
            return Path(raw)
        state_file = self._resolve_state_file()
        return state_file.parent / "monitoring-state.json"

    def _persist_monitoring_state(self) -> None:
        try:
            monitoring_file = self._resolve_monitoring_file()
            monitoring_file.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "keywords": [kw.model_dump(mode="json") for kw in self._keywords.values()],
                "base_sources": [
                    src.model_dump(mode="json") for src in self._base_sources.values()
                ],
                "scan_results": [
                    r.model_dump(mode="json")
                    for r in self._scan_results[-_MAX_SCAN_RESULTS_RETAINED:]
                ],
                "scans": [s.model_dump(mode="json") for s in self._scans[-_MAX_SCANS_RETAINED:]],
                "campaigns": [c.model_dump(mode="json") for c in self._campaigns.values()],
                "monitoring_request_ids": self._monitoring_request_id_to_scan,
                "campaign_run_request_ids": list(self._campaign_run_request_ids),
            }
            monitoring_file.write_text(
                json.dumps(payload, ensure_ascii=False), encoding="utf-8"
            )
        except Exception as exc:
            logger.warning("Brand Studio monitoring state persist failed: %s", exc)

    def _load_monitoring_state(self) -> None:
        try:
            monitoring_file = self._resolve_monitoring_file()
            if not monitoring_file.exists():
                return
            payload = json.loads(monitoring_file.read_text(encoding="utf-8"))

            kw_raw = payload.get("keywords")
            if isinstance(kw_raw, list):
                for item in kw_raw:
                    if isinstance(item, dict):
                        kw = BrandKeyword.model_validate(item)
                        self._keywords[kw.keyword_id] = kw

            src_raw = payload.get("base_sources")
            if isinstance(src_raw, list):
                for item in src_raw:
                    if isinstance(item, dict):
                        src = BrandBaseSource.model_validate(item)
                        self._base_sources[src.source_id] = src

            results_raw = payload.get("scan_results")
            if isinstance(results_raw, list):
                for item in results_raw:
                    if isinstance(item, dict):
                        self._scan_results.append(BrandSearchResult.model_validate(item))

            scans_raw = payload.get("scans")
            if isinstance(scans_raw, list):
                for item in scans_raw:
                    if isinstance(item, dict):
                        self._scans.append(BrandMonitoringScan.model_validate(item))

            camps_raw = payload.get("campaigns")
            if isinstance(camps_raw, list):
                for item in camps_raw:
                    if isinstance(item, dict):
                        camp = BrandCampaign.model_validate(item)
                        self._campaigns[camp.campaign_id] = camp

            req_ids = payload.get("monitoring_request_ids")
            if isinstance(req_ids, dict):
                # New format: dict mapping request_id → scan_id
                self._monitoring_request_id_to_scan = {
                    str(k): str(v) for k, v in req_ids.items()
                }
            elif isinstance(req_ids, list):
                # Legacy format: just a list of request_ids (no scan_id mapping available)
                self._monitoring_request_id_to_scan = {
                    rid: "" for rid in req_ids if isinstance(rid, str)
                }

            run_ids = payload.get("campaign_run_request_ids")
            if isinstance(run_ids, list):
                self._campaign_run_request_ids = set(run_ids)
        except Exception as exc:
            logger.warning("Brand Studio monitoring state load failed: %s", exc)


_service = BrandStudioService()


def get_brand_studio_service() -> BrandStudioService:
    return _service


def health_payload() -> dict[str, str]:
    return {"status": "ok", "module": "brand_studio"}
