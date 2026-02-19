from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from uuid import uuid4

from venom_module_brand_studio.api.schemas import (
    BrandStudioAuditEntry,
    ContentCandidate,
    DraftBundle,
    DraftVariant,
    PublishQueueItem,
    PublishResult,
)


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _sample_candidates() -> list[ContentCandidate]:
    return [
        ContentCandidate(
            id="cand-1",
            source="github",
            url="https://github.com/trending",
            topic="Runtime governance for local-first AI stacks",
            summary="Growing discussion around governance and safe runtime fallback paths.",
            language="en",
            score=0.91,
            age_minutes=40,
            reasons=["fresh", "high relevance", "authority fit"],
        ),
        ContentCandidate(
            id="cand-2",
            source="hn",
            url="https://news.ycombinator.com/",
            topic="Cost controls for hybrid local/cloud LLM routing",
            summary="Thread on balancing local privacy with cloud elasticity.",
            language="en",
            score=0.84,
            age_minutes=120,
            reasons=["good engagement", "technical fit"],
        ),
        ContentCandidate(
            id="cand-3",
            source="rss",
            url="https://example.org/devops-ai",
            topic="Jak budowac moduły pluginowe bez długu w core",
            summary="Artykuł o kontraktach modułowych i separacji produktu od platformy.",
            language="pl",
            score=0.78,
            age_minutes=300,
            reasons=["brand fit", "pl audience"],
        ),
    ]


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
        # Channel is accepted for API compatibility, but not applied in MVP source set.
        _ = channel
        items = [
            item
            for item in self._candidates
            if item.score >= min_score and (lang is None or item.language == lang)
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
