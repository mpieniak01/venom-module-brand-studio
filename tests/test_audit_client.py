from __future__ import annotations

from datetime import UTC, datetime

from venom_module_brand_studio.api.schemas import BrandStudioAuditEntry
from venom_module_brand_studio.services.audit_client import (
    BrandStudioAuditPublishConfig,
    BrandStudioAuditPublisher,
)


def _sample_entry() -> BrandStudioAuditEntry:
    return BrandStudioAuditEntry(
        id="audit-123",
        actor="tester",
        action="draft.generate",
        status="ok",
        payload_hash="abc123",
        timestamp=datetime.now(UTC),
    )


def test_publish_entry_posts_to_core_endpoint(monkeypatch) -> None:
    publisher = BrandStudioAuditPublisher(
        BrandStudioAuditPublishConfig(
            enabled=True,
            core_base_url="http://127.0.0.1:8000",
            timeout_seconds=1.0,
            source="module.brand_studio",
            ingest_token="secret",
        )
    )
    sent: dict[str, object] = {}

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

    class FakeClient:
        def post(self, url, json, headers):
            sent["url"] = url
            sent["json"] = json
            sent["headers"] = headers
            return FakeResponse()

    monkeypatch.setattr(publisher, "_get_client", lambda: FakeClient())
    result = publisher.publish_entry(_sample_entry())

    assert result is True
    assert str(sent["url"]).endswith("/api/v1/audit/stream")
    assert sent["headers"] == {"X-Venom-Audit-Token": "secret"}
    payload = sent["json"]
    assert isinstance(payload, dict)
    assert payload["source"] == "module.brand_studio"
    assert payload["action"] == "draft.generate"
    assert payload["actor"] == "tester"
    assert payload["status"] == "ok"
    assert payload["context"] == "abc123"


def test_publish_entry_maps_github_queue_to_core_technical_source(monkeypatch) -> None:
    publisher = BrandStudioAuditPublisher(
        BrandStudioAuditPublishConfig(
            enabled=True,
            core_base_url="http://127.0.0.1:8000",
            timeout_seconds=1.0,
            source="module.brand_studio",
            ingest_token="",
        )
    )
    sent: dict[str, object] = {}

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

    class FakeClient:
        def post(self, _url, json=None, headers=None):
            _ = headers
            sent["json"] = json
            return FakeResponse()

    entry = _sample_entry().model_copy(
        update={"action": "queue.publish", "details": "github:queue-1"}
    )
    monkeypatch.setattr(publisher, "_get_client", lambda: FakeClient())
    result = publisher.publish_entry(entry)

    assert result is True
    payload = sent["json"]
    assert isinstance(payload, dict)
    assert payload["source"] == "core.technical.github_publish"
    assert payload["context"] == "github:queue-1"


def test_publish_entry_uses_backoff_after_failure(monkeypatch) -> None:
    publisher = BrandStudioAuditPublisher(
        BrandStudioAuditPublishConfig(
            enabled=True,
            core_base_url="http://127.0.0.1:8000",
            timeout_seconds=1.0,
            source="module.brand_studio",
            ingest_token="",
        )
    )
    calls = {"count": 0}

    class FailingClient:
        def post(self, _url, json=None, headers=None):
            _ = json
            _ = headers
            calls["count"] += 1
            raise RuntimeError("core down")

    monkeypatch.setattr(publisher, "_get_client", lambda: FailingClient())

    first = publisher.publish_entry(_sample_entry())
    second = publisher.publish_entry(_sample_entry())

    assert first is False
    assert second is False
    assert calls["count"] == 1
