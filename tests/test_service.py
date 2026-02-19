from __future__ import annotations

from venom_module_brand_studio.connectors.github import GitHubPublishResult
from venom_module_brand_studio.services.service import BrandStudioService, _canonical_url


def test_canonical_url_removes_tracking_params() -> None:
    url = _canonical_url(
        "https://example.org/post?utm_source=a&utm_medium=b&ref=r&id=1&gclid=foo"
    )
    assert url == "https://example.org/post?id=1"


def test_candidates_are_scored_and_deduplicated(monkeypatch) -> None:
    monkeypatch.setenv("BRAND_STUDIO_DISCOVERY_MODE", "stub")
    service = BrandStudioService()
    items, _ = service.list_candidates(channel=None, lang=None, limit=50, min_score=0.0)

    assert len(items) >= 3
    assert all(item.score_breakdown.final_score == item.score for item in items)
    assert all(item.score_breakdown.reasons for item in items)
    assert sorted([it.score for it in items], reverse=True) == [it.score for it in items]

    urls = [it.url for it in items]
    assert len(urls) == len(set(urls))


def test_candidates_filters_work_for_lang_and_channel(monkeypatch) -> None:
    monkeypatch.setenv("BRAND_STUDIO_DISCOVERY_MODE", "stub")
    service = BrandStudioService()

    pl_items, _ = service.list_candidates(channel=None, lang="pl", limit=50, min_score=0.0)
    assert pl_items
    assert all(item.language == "pl" for item in pl_items)

    github_items, _ = service.list_candidates(
        channel="github", lang=None, limit=50, min_score=0.0
    )
    assert github_items
    assert all(item.source in {"github", "arxiv"} for item in github_items)


def test_queue_and_publish_with_github_connector(monkeypatch) -> None:
    monkeypatch.setenv("BRAND_STUDIO_DISCOVERY_MODE", "stub")
    service = BrandStudioService()
    items, _ = service.list_candidates(channel=None, lang=None, limit=2, min_score=0.0)
    candidate_id = items[0].id
    draft = service.generate_draft(
        candidate_id=candidate_id,
        channels=["github"],
        languages=["pl"],
        tone="expert",
        actor="tester",
    )
    queue_item = service.queue_draft(
        draft_id=draft.draft_id,
        target_channel="github",
        target_language="pl",
        target_repo="owner/repo",
        target_path="content/test.md",
        payload_override=None,
        actor="tester",
    )

    class FakePublisher:
        def publish_markdown(self, *, path: str, content: str, title: str) -> GitHubPublishResult:
            assert path == "content/test.md"
            assert content
            assert title
            return GitHubPublishResult(
                external_id="sha123",
                url="https://example.org/pr/1",
                message="ok",
            )

    service._publisher = FakePublisher()  # type: ignore[attr-defined]
    result = service.publish_queue_item(
        item_id=queue_item.item_id,
        confirm_publish=True,
        actor="tester",
    )
    assert result.success is True
    assert result.status == "published"
    assert result.external_id == "sha123"


def test_live_mode_uses_adapter_results(monkeypatch) -> None:
    monkeypatch.setenv("BRAND_STUDIO_DISCOVERY_MODE", "live")
    monkeypatch.setenv("BRAND_STUDIO_RSS_URLS", "https://example.org/feed.xml")

    def fake_rss(_urls):
        return [
            {
                "id": "r1",
                "source": "rss",
                "url": "https://example.org/post?utm_source=x",
                "topic": "Module architecture in practice",
                "summary": "Practical notes from rollout.",
                "language": "en",
                "age_minutes": 20,
            }
        ]

    monkeypatch.setattr("venom_module_brand_studio.services.service.fetch_rss_items", fake_rss)
    monkeypatch.setattr("venom_module_brand_studio.services.service.fetch_github_items", lambda: [])
    monkeypatch.setattr("venom_module_brand_studio.services.service.fetch_hn_items", lambda: [])
    monkeypatch.setattr("venom_module_brand_studio.services.service.fetch_arxiv_items", lambda: [])

    service = BrandStudioService()
    items, _ = service.list_candidates(channel=None, lang=None, limit=10, min_score=0.0)
    assert items
    assert items[0].source == "rss"
    assert "utm_source" not in items[0].url
