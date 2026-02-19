from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import uuid4

from venom_module_brand_studio.api.schemas import (
    BrandStudioAuditEntry,
    ChannelAccount,
    ChannelAccountCreateRequest,
    ChannelAccountsResponse,
    ChannelAccountTestResponse,
    ChannelAccountUpdateRequest,
    ChannelDescriptor,
    ChannelId,
    ChannelsResponse,
    ConfigUpdateRequest,
    ContentCandidate,
    DraftBundle,
    DraftVariant,
    IntegrationDescriptor,
    IntegrationId,
    IntegrationStatus,
    IntegrationTestResponse,
    OpportunityScoreBreakdown,
    PublishQueueItem,
    PublishResult,
    StrategyConfig,
    StrategyCreateRequest,
    StrategyUpdateRequest,
)
from venom_module_brand_studio.connectors.devto import DevtoPublisher
from venom_module_brand_studio.connectors.github import GitHubPublisher
from venom_module_brand_studio.connectors.sources import (
    fetch_arxiv_items,
    fetch_github_items,
    fetch_hn_items,
    fetch_rss_items,
)

logger = logging.getLogger(__name__)


class StrategyNotFoundError(KeyError):
    pass


class LastStrategyDeletionError(ValueError):
    pass


class ChannelAccountNotFoundError(KeyError):
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


def _utcnow() -> datetime:
    return datetime.now(UTC)


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


class BrandStudioService:
    def __init__(self) -> None:
        self._candidates: list[ContentCandidate] = []
        self._last_refresh_at: datetime = datetime.fromtimestamp(0, tz=UTC)
        self._drafts: dict[str, DraftBundle] = {}
        self._queue: dict[str, PublishQueueItem] = {}
        self._audit: list[BrandStudioAuditEntry] = []
        self._publisher = GitHubPublisher.from_env()
        self._devto_publisher = DevtoPublisher.from_env()
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
        self._init_default_strategy()
        self._init_default_accounts()
        self._load_candidates_cache()
        self._load_runtime_state()
        self._load_accounts_state()

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
                    target=(target or None),
                    enabled=True,
                    is_default=True,
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
        except Exception as exc:
            logger.warning("Brand Studio runtime state load failed: %s", exc)
            return

    def _persist_runtime_state(self) -> None:
        try:
            self._state_file.parent.mkdir(parents=True, exist_ok=True)
            payload = {
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
            return "configured" if client_id and client_secret else "missing"
        if channel == "devto":
            return "configured" if (os.getenv("DEVTO_API_KEY") or "").strip() else "missing"
        if channel == "hashnode":
            return "configured" if (os.getenv("HASHNODE_TOKEN") or "").strip() else "missing"
        return "invalid"

    def _capabilities_for_channel(self, channel: ChannelId) -> list[str]:
        if channel in {"github", "blog"}:
            return ["publish_markdown", "queue"]
        if channel in {"x", "linkedin", "reddit", "devto", "hashnode", "medium"}:
            return ["manual_publish_mvp", "queue"]
        if channel in {"hf_blog", "hf_spaces"}:
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
                accounts[account_id] = account.model_copy(
                    update={"secret_status": secret_status, "capabilities": capabilities}
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
            created = ChannelAccount(
                account_id=account_id,
                channel=channel,
                display_name=payload.display_name,
                target=payload.target,
                enabled=payload.enabled,
                is_default=payload.is_default or len(current) == 0,
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
            updates = payload.model_dump(exclude_none=True)
            updated = account.model_copy(update=updates)
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
    ) -> ChannelAccountTestResponse:
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

            tested_at = _utcnow()
            current[account_id] = account.model_copy(update={"secret_status": status})
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
        strategy = self._active_strategy()
        effective_min_score = strategy.min_score if min_score is None else min_score
        effective_limit = min(limit, strategy.limit)
        items = [
            item
            for item in self._candidates
            if item.score >= effective_min_score
            and (lang is None or item.language == lang)
            and _channel_match(item.source, channel)
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
    ) -> DraftBundle:
        candidate = next((it for it in self._candidates if it.id == candidate_id), None)
        if candidate is None:
            raise KeyError("candidate_not_found")

        tone_suffix = f" ({tone})" if tone else ""
        variants: list[DraftVariant] = []
        for channel in channels:
            for language in languages:
                if language == "pl":
                    content = (
                        f"{candidate.topic}: {candidate.summary} "
                        f"Moja perspektywa inżynierska i praktyczne wnioski.{tone_suffix}".strip()
                    )
                else:
                    content = (
                        f"{candidate.topic}: {candidate.summary} "
                        f"My engineering perspective with practical takeaways.{tone_suffix}".strip()
                    )
                variants.append(DraftVariant(channel=channel, language=language, content=content))

        draft_id = f"draft-{uuid4().hex[:10]}"
        bundle = DraftBundle(draft_id=draft_id, candidate_id=candidate_id, variants=variants)
        self._drafts[draft_id] = bundle
        self._add_audit(actor=actor, action="draft.generate", status="ok", payload=draft_id)
        return bundle

    def queue_draft(
        self,
        *,
        draft_id: str,
        target_channel: str,
        target_language: str | None,
        target_repo: str | None,
        target_path: str | None,
        payload_override: str | None,
        actor: str,
        account_id: str | None = None,
    ) -> PublishQueueItem:
        bundle = self._drafts.get(draft_id)
        if bundle is None:
            raise KeyError("draft_not_found")

        candidate_variant = self._choose_variant(
            bundle=bundle, target_channel=target_channel, target_language=target_language
        )
        if candidate_variant is None:
            raise KeyError("draft_variant_not_found")

        payload = payload_override or candidate_variant.content
        selected_account = self._resolve_account_for_queue(
            target_channel=target_channel, account_id=account_id
        )
        now = _utcnow()
        item = PublishQueueItem(
            item_id=f"queue-{uuid4().hex[:10]}",
            draft_id=draft_id,
            target_channel=target_channel,
            target_language=candidate_variant.language,
            target_repo=target_repo
            or (selected_account.target if selected_account else None)
            or os.getenv("BRAND_TARGET_REPO"),
            target_path=target_path or _default_target_path(target_channel),
            account_id=selected_account.account_id if selected_account else None,
            account_display_name=selected_account.display_name if selected_account else None,
            payload=payload,
            status="queued",
            created_at=now,
            updated_at=now,
        )
        self._queue[item.item_id] = item
        self._persist_runtime_state()
        self._add_audit(actor=actor, action="queue.create", status="queued", payload=item.item_id)
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
            return accounts.get(account_id)
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
    ) -> DraftVariant | None:
        variants = [v for v in bundle.variants if v.channel == target_channel]
        if target_language:
            lang_match = [v for v in variants if v.language == target_language]
            if lang_match:
                return lang_match[0]
        return variants[0] if variants else None

    def publish_queue_item(
        self,
        *,
        item_id: str,
        confirm_publish: bool,
        actor: str,
    ) -> PublishResult:
        item = self._queue.get(item_id)
        if item is None:
            raise KeyError("queue_item_not_found")
        if not confirm_publish:
            raise ValueError("confirm_publish_required")
        if item.status == "published":
            raise ValueError("queue_item_already_published")

        now = _utcnow()
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
                    payload=item_id,
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
                    target=item.target_repo,
                )
                item.status = "published"
                item.updated_at = now
                self._persist_runtime_state()
                self._add_audit(
                    actor=actor,
                    action="queue.publish",
                    status="published",
                    payload=item_id,
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
                return PublishResult(
                    success=False,
                    status="failed",
                    published_at=now,
                    message=f"Dev.to publish failed: {exc}",
                )

        item.status = "published"
        item.updated_at = now
        self._persist_runtime_state()
        self._add_audit(
            actor=actor,
            action="queue.publish",
            status="manual",
            payload=f"{item_id}:{item.target_channel}",
        )
        return PublishResult(
            success=True,
            status="published",
            published_at=now,
            external_id=f"manual-{item_id}",
            message=f"{item.target_channel} publish marked as manual-complete in MVP",
        )

    def queue_items(self) -> list[PublishQueueItem]:
        with self._lock:
            items = list(self._queue.values())
            items.sort(key=lambda it: it.created_at, reverse=True)
            return items

    def audit_items(self) -> list[BrandStudioAuditEntry]:
        with self._lock:
            return list(reversed(self._audit))

    def integrations(self) -> list[IntegrationDescriptor]:
        strategy = self._active_strategy()
        github_token = (os.getenv("GITHUB_TOKEN_BRAND") or "").strip()
        github_repo = (os.getenv("BRAND_TARGET_REPO") or "").strip()
        x_token = (os.getenv("X_API_TOKEN") or "").strip()
        devto_key = (os.getenv("DEVTO_API_KEY") or "").strip()
        reddit_client_id = (os.getenv("REDDIT_CLIENT_ID") or "").strip()
        reddit_client_secret = (os.getenv("REDDIT_CLIENT_SECRET") or "").strip()
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
                status="configured" if reddit_client_id and reddit_client_secret else "missing",
                details=(
                    "Reddit credentials present"
                    if reddit_client_id and reddit_client_secret
                    else "Missing REDDIT_CLIENT_ID or REDDIT_CLIENT_SECRET"
                ),
                key_hint="REDDIT_CLIENT_ID/REDDIT_CLIENT_SECRET",
                masked_secret=_masked_secret(reddit_client_secret),
            ),
            IntegrationDescriptor(
                id="hashnode_publish",
                name="Hashnode publish",
                requires_key=True,
                status="configured" if hashnode_token else "missing",
                details=(
                    "Hashnode token present"
                    if hashnode_token
                    else "Missing HASHNODE_TOKEN"
                ),
                key_hint="HASHNODE_TOKEN",
                masked_secret=_masked_secret(hashnode_token),
            ),
            IntegrationDescriptor(
                id="linkedin_publish",
                name="LinkedIn publish",
                requires_key=True,
                status="configured" if linkedin_token else "missing",
                details=(
                    "LinkedIn token present (connector planned)"
                    if linkedin_token
                    else "Missing LINKEDIN_ACCESS_TOKEN"
                ),
                key_hint="LINKEDIN_ACCESS_TOKEN",
                masked_secret=_masked_secret(linkedin_token),
            ),
            IntegrationDescriptor(
                id="medium_publish",
                name="Medium publish",
                requires_key=True,
                status="configured" if medium_token else "missing",
                details=(
                    "Medium token present (connector planned)"
                    if medium_token
                    else "Missing MEDIUM_TOKEN"
                ),
                key_hint="MEDIUM_TOKEN",
                masked_secret=_masked_secret(medium_token),
            ),
            IntegrationDescriptor(
                id="hf_blog_publish",
                name="HF Blog publish",
                requires_key=True,
                status="configured" if hf_token else "missing",
                details=(
                    "HF token present (connector planned)"
                    if hf_token
                    else "Missing HF_TOKEN"
                ),
                key_hint="HF_TOKEN",
                masked_secret=_masked_secret(hf_token),
            ),
            IntegrationDescriptor(
                id="hf_spaces_publish",
                name="HF Spaces publish",
                requires_key=True,
                status="configured" if hf_token else "missing",
                details=(
                    "HF token present (connector planned)"
                    if hf_token
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
    ) -> IntegrationTestResponse:
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
                client_id = (os.getenv("REDDIT_CLIENT_ID") or "").strip()
                client_secret = (os.getenv("REDDIT_CLIENT_SECRET") or "").strip()
                if client_id and client_secret:
                    status = "configured"
                    success = True
                    message = "Credentials present (connector planned)"
                else:
                    status = "missing"
                    message = "Missing REDDIT_CLIENT_ID/REDDIT_CLIENT_SECRET"
            elif integration_id == "hashnode_publish":
                token = (os.getenv("HASHNODE_TOKEN") or "").strip()
                if token:
                    status = "configured"
                    success = True
                    message = "Token present (connector planned)"
                else:
                    status = "missing"
                    message = "Missing HASHNODE_TOKEN"
            elif integration_id == "linkedin_publish":
                token = (os.getenv("LINKEDIN_ACCESS_TOKEN") or "").strip()
                if token:
                    status = "configured"
                    success = True
                    message = "Token present (connector planned)"
                else:
                    status = "missing"
                    message = "Missing LINKEDIN_ACCESS_TOKEN"
            elif integration_id == "medium_publish":
                token = (os.getenv("MEDIUM_TOKEN") or "").strip()
                if token:
                    status = "configured"
                    success = True
                    message = "Token present (connector planned)"
                else:
                    status = "missing"
                    message = "Missing MEDIUM_TOKEN"
            elif integration_id in {"hf_blog_publish", "hf_spaces_publish"}:
                token = (os.getenv("HF_TOKEN") or "").strip()
                if token:
                    status = "configured"
                    success = True
                    message = "Token present (connector planned)"
                else:
                    status = "missing"
                    message = "Missing HF_TOKEN"
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
        with self._lock:
            payload_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()
            self._audit.append(
                BrandStudioAuditEntry(
                    id=f"audit-{uuid4().hex[:10]}",
                    actor=actor,
                    action=action,
                    status=status,
                    payload_hash=payload_hash,
                    timestamp=_utcnow(),
                )
            )
            self._persist_runtime_state()


_service = BrandStudioService()


def get_brand_studio_service() -> BrandStudioService:
    return _service


def health_payload() -> dict[str, str]:
    return {"status": "ok", "module": "brand_studio"}
