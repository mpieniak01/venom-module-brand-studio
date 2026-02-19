from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import uuid4

from venom_module_brand_studio.api.schemas import (
    BrandStudioAuditEntry,
    ContentCandidate,
    DraftBundle,
    DraftVariant,
    OpportunityScoreBreakdown,
    PublishQueueItem,
    PublishResult,
)
from venom_module_brand_studio.connectors.github import GitHubPublisher
from venom_module_brand_studio.connectors.sources import (
    fetch_arxiv_items,
    fetch_github_items,
    fetch_hn_items,
    fetch_rss_items,
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


class BrandStudioService:
    def __init__(self) -> None:
        self._candidates: list[ContentCandidate] = []
        self._last_refresh_at: datetime = datetime.fromtimestamp(0, tz=UTC)
        self._drafts: dict[str, DraftBundle] = {}
        self._queue: dict[str, PublishQueueItem] = {}
        self._audit: list[BrandStudioAuditEntry] = []
        self._publisher = GitHubPublisher.from_env()
        self._cache_ttl_seconds = self._resolve_cache_ttl_seconds()
        self._cache_file = self._resolve_cache_file()
        self._state_file = self._resolve_state_file()
        self._load_candidates_cache()
        self._load_runtime_state()

    def _resolve_cache_ttl_seconds(self) -> int:
        raw = (os.getenv("BRAND_STUDIO_CACHE_TTL_SECONDS") or "1800").strip()
        try:
            value = int(raw)
        except ValueError:
            return 1800
        return max(30, value)

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

    def _is_cache_fresh(self) -> bool:
        if not self._candidates:
            return False
        age_seconds = (_utcnow() - self._last_refresh_at).total_seconds()
        return age_seconds <= self._cache_ttl_seconds

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
            # Cache is best-effort only. Invalid file should not break module startup.
            return

    def _persist_candidates_cache(self) -> None:
        try:
            self._cache_file.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "refreshed_at": self._last_refresh_at.isoformat(),
                "items": [item.model_dump(mode="json") for item in self._candidates],
            }
            self._cache_file.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        except Exception:
            # Cache write errors must not fail request processing.
            return

    def _load_runtime_state(self) -> None:
        try:
            if not self._state_file.exists():
                return
            payload = json.loads(self._state_file.read_text(encoding="utf-8"))
            queue_raw = payload.get("queue")
            audit_raw = payload.get("audit")
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
        except Exception:
            # State file is best-effort only.
            return

    def _persist_runtime_state(self) -> None:
        try:
            self._state_file.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "queue": [item.model_dump(mode="json") for item in self._queue.values()],
                "audit": [item.model_dump(mode="json") for item in self._audit],
            }
            self._state_file.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        except Exception:
            # Persistence is best-effort and must never break request flow.
            return

    def refresh_candidates(self) -> None:
        if self._is_cache_fresh():
            return
        mode = (os.getenv("BRAND_STUDIO_DISCOVERY_MODE") or "hybrid").strip().lower()
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
        rss_urls = [
            item.strip()
            for item in (os.getenv("BRAND_STUDIO_RSS_URLS") or "").split(",")
            if item.strip()
        ]
        try:
            if rss_urls:
                items.extend(fetch_rss_items(rss_urls))
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

    def list_candidates(
        self,
        *,
        channel: str | None,
        lang: str | None,
        limit: int,
        min_score: float,
    ) -> tuple[list[ContentCandidate], datetime]:
        self.refresh_candidates()
        items = [
            item
            for item in self._candidates
            if item.score >= min_score
            and (lang is None or item.language == lang)
            and _channel_match(item.source, channel)
        ]
        items.sort(key=lambda it: it.score, reverse=True)
        return items[:limit], self._last_refresh_at

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
        now = _utcnow()
        item = PublishQueueItem(
            item_id=f"queue-{uuid4().hex[:10]}",
            draft_id=draft_id,
            target_channel=target_channel,
            target_language=candidate_variant.language,
            target_repo=target_repo or os.getenv("BRAND_TARGET_REPO"),
            target_path=target_path or _default_target_path(target_channel),
            payload=payload,
            status="queued",
            created_at=now,
            updated_at=now,
        )
        self._queue[item.item_id] = item
        self._add_audit(actor=actor, action="queue.create", status="queued", payload=item.item_id)
        return item

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

        # X channel remains manual in MVP.
        item.status = "published"
        item.updated_at = now
        self._add_audit(
            actor=actor,
            action="queue.publish",
            status="manual",
            payload=f"{item_id}:x",
        )
        return PublishResult(
            success=True,
            status="published",
            published_at=now,
            external_id=f"manual-{item_id}",
            message="X publish marked as manual-complete in MVP",
        )

    def queue_items(self) -> list[PublishQueueItem]:
        items = list(self._queue.values())
        items.sort(key=lambda it: it.created_at, reverse=True)
        return items

    def audit_items(self) -> list[BrandStudioAuditEntry]:
        return list(reversed(self._audit))

    def _add_audit(self, *, actor: str, action: str, status: str, payload: str) -> None:
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
