from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
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


def _utcnow() -> datetime:
    return datetime.now(UTC)


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
        {
            "id": "cand-4",
            "source": "rss",
            "url": "https://github.com/trending",
            "topic": "Runtime governance for local-first AI stacks",
            "summary": "Growing discussion around governance and safe runtime fallback paths.",
            "language": "en",
            "age_minutes": 55,
        },
        {
            "id": "cand-5",
            "source": "arxiv",
            "url": "https://arxiv.org/abs/2501.12345",
            "topic": "Agentic coding assistants with memory and retrieval",
            "summary": "Study of productivity gains and failure modes.",
            "language": "en",
            "age_minutes": 480,
        },
    ]
    return _normalize_and_rank_candidates(raw_items)


def _canonical_url(raw_url: str) -> str:
    parsed = urlsplit(raw_url)
    cleaned_query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if not (key.startswith("utm_") or key in {"ref", "source", "fbclid", "gclid"})
    ]
    return urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, urlencode(cleaned_query), "")
    )


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
            id=str(raw["id"]),
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


class BrandStudioService:
    def __init__(self) -> None:
        self._candidates: list[ContentCandidate] = _sample_candidates()
        self._drafts: dict[str, DraftBundle] = {}
        self._queue: dict[str, PublishQueueItem] = {}
        self._audit: list[BrandStudioAuditEntry] = []

    def list_candidates(
        self,
        *,
        channel: str | None,
        lang: str | None,
        limit: int,
        min_score: float,
    ) -> tuple[list[ContentCandidate], datetime]:
        items = [
            item
            for item in self._candidates
            if item.score >= min_score
            and (lang is None or item.language == lang)
            and _channel_match(item.source, channel)
        ]
        items.sort(key=lambda it: it.score, reverse=True)
        return items[:limit], _utcnow()

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
                variants.append(
                    DraftVariant(channel=channel, language=language, content=content)
                )

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
        actor: str,
    ) -> PublishQueueItem:
        if draft_id not in self._drafts:
            raise KeyError("draft_not_found")

        now = _utcnow()
        item = PublishQueueItem(
            item_id=f"queue-{uuid4().hex[:10]}",
            draft_id=draft_id,
            target_channel=target_channel,
            status="queued",
            created_at=now,
            updated_at=now,
        )
        self._queue[item.item_id] = item
        self._add_audit(actor=actor, action="queue.create", status="queued", payload=item.item_id)
        return item

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
            return PublishResult(
                success=True,
                status="published",
                published_at=item.updated_at,
                external_id=f"ext-{item_id}",
                url=f"https://example.org/published/{item_id}",
                message="Already published",
            )

        now = _utcnow()
        item.status = "published"
        item.updated_at = now
        self._add_audit(actor=actor, action="queue.publish", status="published", payload=item_id)
        return PublishResult(
            success=True,
            status="published",
            published_at=now,
            external_id=f"ext-{item_id}",
            url=f"https://example.org/published/{item_id}",
            message="Published successfully",
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


_service = BrandStudioService()


def get_brand_studio_service() -> BrandStudioService:
    return _service


def health_payload() -> dict[str, str]:
    return {"status": "ok", "module": "brand_studio"}


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
