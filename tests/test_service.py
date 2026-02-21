from __future__ import annotations

from pathlib import Path

import pytest

from venom_module_brand_studio.api.schemas import (
    ChannelAccountCreateRequest,
    ChannelAccountUpdateRequest,
    ConfigUpdateRequest,
    StrategyCreateRequest,
    StrategyUpdateRequest,
)
from venom_module_brand_studio.connectors.github import GitHubPublishResult
from venom_module_brand_studio.services.service import (
    BrandStudioService,
    ChannelAccountNotFoundError,
    _canonical_url,
)


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


def test_candidates_filters_work_for_lang_and_channel(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("BRAND_STUDIO_DISCOVERY_MODE", "stub")
    monkeypatch.setenv("BRAND_STUDIO_STATE_FILE", str(tmp_path / "runtime-state.json"))
    monkeypatch.setenv("BRAND_STUDIO_CACHE_FILE", str(tmp_path / "candidates-cache.json"))
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
        target=None,
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


def test_live_mode_uses_adapter_results(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("BRAND_STUDIO_DISCOVERY_MODE", "live")
    monkeypatch.setenv("BRAND_STUDIO_RSS_URLS", "https://example.org/feed.xml")
    monkeypatch.setenv("BRAND_STUDIO_CACHE_FILE", str(tmp_path / "candidates-cache.json"))
    monkeypatch.setenv("BRAND_STUDIO_STATE_FILE", str(tmp_path / "runtime-state.json"))

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


def test_cache_ttl_avoids_repeated_external_fetch(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("BRAND_STUDIO_DISCOVERY_MODE", "live")
    monkeypatch.setenv("BRAND_STUDIO_RSS_URLS", "https://example.org/feed.xml")
    monkeypatch.setenv("BRAND_STUDIO_CACHE_TTL_SECONDS", "3600")
    monkeypatch.setenv("BRAND_STUDIO_CACHE_FILE", str(tmp_path / "candidates-cache.json"))
    monkeypatch.setenv("BRAND_STUDIO_STATE_FILE", str(tmp_path / "runtime-state.json"))

    calls = {"rss": 0}

    def fake_rss(_urls):
        calls["rss"] += 1
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
    service.list_candidates(channel=None, lang=None, limit=10, min_score=0.0)
    service.list_candidates(channel=None, lang=None, limit=10, min_score=0.0)

    assert calls["rss"] == 1


def test_cache_survives_service_restart(monkeypatch, tmp_path: Path) -> None:
    cache_file = tmp_path / "candidates-cache.json"
    monkeypatch.setenv("BRAND_STUDIO_DISCOVERY_MODE", "live")
    monkeypatch.setenv("BRAND_STUDIO_RSS_URLS", "https://example.org/feed.xml")
    monkeypatch.setenv("BRAND_STUDIO_CACHE_TTL_SECONDS", "3600")
    monkeypatch.setenv("BRAND_STUDIO_CACHE_FILE", str(cache_file))
    monkeypatch.setenv("BRAND_STUDIO_STATE_FILE", str(tmp_path / "runtime-state.json"))

    def fake_rss_initial(_urls):
        return [
            {
                "id": "r1",
                "source": "rss",
                "url": "https://example.org/post?utm_source=x",
                "topic": "Persisted topic",
                "summary": "Persisted summary",
                "language": "en",
                "age_minutes": 20,
            }
        ]

    monkeypatch.setattr(
        "venom_module_brand_studio.services.service.fetch_rss_items",
        fake_rss_initial,
    )
    monkeypatch.setattr("venom_module_brand_studio.services.service.fetch_github_items", lambda: [])
    monkeypatch.setattr("venom_module_brand_studio.services.service.fetch_hn_items", lambda: [])
    monkeypatch.setattr("venom_module_brand_studio.services.service.fetch_arxiv_items", lambda: [])

    service = BrandStudioService()
    items, _ = service.list_candidates(channel=None, lang=None, limit=10, min_score=0.0)
    assert items and items[0].topic == "Persisted topic"
    assert cache_file.exists()

    def fake_rss_should_not_run(_urls):
        raise AssertionError(
            "External source should not be called after restart while cache is fresh"
        )

    monkeypatch.setattr(
        "venom_module_brand_studio.services.service.fetch_rss_items",
        fake_rss_should_not_run,
    )
    restarted = BrandStudioService()
    cached_items, _ = restarted.list_candidates(channel=None, lang=None, limit=10, min_score=0.0)
    assert cached_items and cached_items[0].topic == "Persisted topic"


def test_queue_and_audit_state_survive_service_restart(monkeypatch, tmp_path: Path) -> None:
    state_file = tmp_path / "runtime-state.json"
    cache_file = tmp_path / "candidates-cache.json"
    monkeypatch.setenv("BRAND_STUDIO_DISCOVERY_MODE", "stub")
    monkeypatch.setenv("BRAND_STUDIO_STATE_FILE", str(state_file))
    monkeypatch.setenv("BRAND_STUDIO_CACHE_FILE", str(cache_file))

    service = BrandStudioService()
    items, _ = service.list_candidates(channel=None, lang=None, limit=5, min_score=0.0)
    draft = service.generate_draft(
        candidate_id=items[0].id,
        channels=["x"],
        languages=["pl"],
        tone="expert",
        actor="tester",
    )
    queue_item = service.queue_draft(
        draft_id=draft.draft_id,
        target_channel="x",
        target_language="pl",
        target=None,
        target_repo=None,
        target_path=None,
        payload_override=None,
        actor="tester",
    )
    result = service.publish_queue_item(
        item_id=queue_item.item_id,
        confirm_publish=True,
        actor="tester",
    )
    assert result.success is True
    assert state_file.exists()

    restarted = BrandStudioService()
    queue = restarted.queue_items()
    audit = restarted.audit_items()
    assert queue
    assert queue[0].item_id == queue_item.item_id
    assert queue[0].status == "published"
    assert audit


def test_strategy_lifecycle_and_config_persistence(monkeypatch, tmp_path: Path) -> None:
    state_file = tmp_path / "runtime-state.json"
    cache_file = tmp_path / "candidates-cache.json"
    monkeypatch.setenv("BRAND_STUDIO_DISCOVERY_MODE", "stub")
    monkeypatch.setenv("BRAND_STUDIO_STATE_FILE", str(state_file))
    monkeypatch.setenv("BRAND_STUDIO_CACHE_FILE", str(cache_file))

    service = BrandStudioService()
    active_id, active = service.config()
    assert active_id == "default"
    assert active.discovery_mode == "stub"

    created = service.create_strategy(
        StrategyCreateRequest(name="Founder EN", discovery_mode="live"),
        actor="tester",
    )
    assert created.id != "default"
    assert created.discovery_mode == "live"

    updated = service.update_strategy(
        created.id,
        StrategyUpdateRequest(min_score=0.55, limit=12),
        actor="tester",
    )
    assert updated.min_score == 0.55
    assert updated.limit == 12

    active = service.activate_strategy(created.id, actor="tester")
    assert active.id == created.id
    service.update_active_config(
        ConfigUpdateRequest(cache_ttl_seconds=900, rss_urls=["https://example.org/feed.xml"]),
        actor="tester",
    )

    service.delete_strategy("default", actor="tester")

    restarted = BrandStudioService()
    restarted_active_id, restarted_active = restarted.config()
    assert restarted_active_id == created.id
    assert restarted_active.cache_ttl_seconds == 900
    assert restarted_active.rss_urls == ["https://example.org/feed.xml"]


def test_integrations_status_and_test(monkeypatch, tmp_path: Path) -> None:
    state_file = tmp_path / "runtime-state.json"
    cache_file = tmp_path / "candidates-cache.json"
    monkeypatch.setenv("BRAND_STUDIO_DISCOVERY_MODE", "live")
    monkeypatch.setenv("BRAND_STUDIO_RSS_URLS", "https://example.org/feed.xml")
    monkeypatch.setenv("BRAND_STUDIO_STATE_FILE", str(state_file))
    monkeypatch.setenv("BRAND_STUDIO_CACHE_FILE", str(cache_file))
    monkeypatch.delenv("GITHUB_TOKEN_BRAND", raising=False)
    monkeypatch.delenv("BRAND_TARGET_REPO", raising=False)

    monkeypatch.setattr(
        "venom_module_brand_studio.services.service.fetch_rss_items",
        lambda _urls, max_items_per_feed=8: [  # noqa: ARG005
            {
                "id": "r1",
                "source": "rss",
                "url": "https://example.org/r1",
                "topic": "t",
                "summary": "s",
                "language": "en",
                "age_minutes": 10,
            }
        ],
    )
    monkeypatch.setattr(
        "venom_module_brand_studio.services.service.fetch_hn_items",
        lambda max_items=12: [],  # noqa: ARG005
    )
    monkeypatch.setattr(
        "venom_module_brand_studio.services.service.fetch_arxiv_items",
        lambda max_items=12: [],  # noqa: ARG005
    )

    service = BrandStudioService()
    integrations = {item.id: item for item in service.integrations()}
    assert integrations["github_publish"].status == "missing"
    assert integrations["rss"].status == "configured"
    assert integrations["hn"].status == "configured"

    rss_test = service.test_integration("rss", actor="tester")
    assert rss_test.success is True
    assert rss_test.status == "configured"

    github_test = service.test_integration("github_publish", actor="tester")
    assert github_test.success is False
    assert github_test.status == "missing"


def test_channel_accounts_lifecycle_and_queue_binding(monkeypatch, tmp_path: Path) -> None:
    state_file = tmp_path / "runtime-state.json"
    cache_file = tmp_path / "candidates-cache.json"
    accounts_file = tmp_path / "accounts-state.json"
    monkeypatch.setenv("BRAND_STUDIO_DISCOVERY_MODE", "stub")
    monkeypatch.setenv("BRAND_STUDIO_STATE_FILE", str(state_file))
    monkeypatch.setenv("BRAND_STUDIO_CACHE_FILE", str(cache_file))
    monkeypatch.setenv("BRAND_STUDIO_ACCOUNTS_FILE", str(accounts_file))

    service = BrandStudioService()
    created = service.create_channel_account(
        "devto",
        ChannelAccountCreateRequest(
            display_name="Dev.to Main",
            target="devto-user",
            is_default=True,
        ),
        actor="tester",
    )
    assert created.channel == "devto"
    assert created.is_default is True

    listed = service.channel_accounts("devto")
    assert listed.items
    assert listed.items[0].account_id == created.account_id

    updated = service.update_channel_account(
        "devto",
        created.account_id,
        ChannelAccountUpdateRequest(display_name="Dev.to Updated"),
        actor="tester",
    )
    assert updated.display_name == "Dev.to Updated"

    test_result = service.test_channel_account("devto", created.account_id, actor="tester")
    assert test_result.account_id == created.account_id
    assert test_result.status in {"missing", "configured", "invalid"}
    refreshed_after_test = service.channel_accounts("devto").items[0]
    assert refreshed_after_test.last_tested_at is not None
    assert refreshed_after_test.last_test_status == test_result.status

    items, _ = service.list_candidates(channel=None, lang=None, limit=1, min_score=0.0)
    draft = service.generate_draft(
        candidate_id=items[0].id,
        channels=["devto"],
        languages=["pl"],
        tone="expert",
        actor="tester",
    )
    queue_item = service.queue_draft(
        draft_id=draft.draft_id,
        target_channel="devto",
        account_id=created.account_id,
        target_language="pl",
        target=None,
        target_repo=None,
        target_path=None,
        payload_override=None,
        actor="tester",
    )
    assert queue_item.account_id == created.account_id
    assert queue_item.account_display_name == "Dev.to Updated"

    service.delete_channel_account("devto", created.account_id, actor="tester")
    assert service.channel_accounts("devto").items == []


def test_queue_draft_raises_for_unknown_explicit_account_id(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("BRAND_STUDIO_DISCOVERY_MODE", "stub")
    monkeypatch.setenv("BRAND_STUDIO_STATE_FILE", str(tmp_path / "runtime-state.json"))
    monkeypatch.setenv("BRAND_STUDIO_CACHE_FILE", str(tmp_path / "candidates-cache.json"))
    monkeypatch.setenv("BRAND_STUDIO_ACCOUNTS_FILE", str(tmp_path / "accounts-state.json"))
    service = BrandStudioService()

    items, _ = service.list_candidates(channel=None, lang=None, limit=1, min_score=0.0)
    draft = service.generate_draft(
        candidate_id=items[0].id,
        channels=["devto"],
        languages=["pl"],
        tone="expert",
        actor="tester",
    )

    with pytest.raises(ChannelAccountNotFoundError):
        service.queue_draft(
            draft_id=draft.draft_id,
            target_channel="devto",
            account_id="missing-account",
            target_language="pl",
            target=None,
            target_repo=None,
            target_path=None,
            payload_override=None,
            actor="tester",
        )


def test_publish_devto_channel_with_connector(monkeypatch, tmp_path: Path) -> None:
    state_file = tmp_path / "runtime-state.json"
    cache_file = tmp_path / "candidates-cache.json"
    accounts_file = tmp_path / "accounts-state.json"
    monkeypatch.setenv("BRAND_STUDIO_DISCOVERY_MODE", "stub")
    monkeypatch.setenv("BRAND_STUDIO_STATE_FILE", str(state_file))
    monkeypatch.setenv("BRAND_STUDIO_CACHE_FILE", str(cache_file))
    monkeypatch.setenv("BRAND_STUDIO_ACCOUNTS_FILE", str(accounts_file))

    service = BrandStudioService()
    account = service.create_channel_account(
        "devto",
        ChannelAccountCreateRequest(display_name="Devto E2E", target="devto-user", is_default=True),
        actor="tester",
    )
    items, _ = service.list_candidates(channel=None, lang=None, limit=1, min_score=0.0)
    draft = service.generate_draft(
        candidate_id=items[0].id,
        channels=["devto"],
        languages=["en"],
        tone="expert",
        actor="tester",
    )
    queue_item = service.queue_draft(
        draft_id=draft.draft_id,
        target_channel="devto",
        target_language="en",
        target=None,
        target_repo=None,
        target_path=None,
        payload_override=None,
        actor="tester",
        account_id=account.account_id,
    )

    class FakeDevtoPublisher:
        def publish_markdown(self, *, title: str, content: str, target: str | None = None):  # noqa: ANN001
            assert title
            assert content
            assert target == "devto-user"

            class Result:
                external_id = "devto-42"
                url = "https://dev.to/example"
                message = "Published to Dev.to"

            return Result()

    service._devto_publisher = FakeDevtoPublisher()  # type: ignore[attr-defined]

    result = service.publish_queue_item(
        item_id=queue_item.item_id,
        confirm_publish=True,
        actor="tester",
    )
    assert result.success is True
    assert result.status == "published"
    assert result.external_id == "devto-42"
    refreshed = service.channel_accounts("devto").items[0]
    assert refreshed.successful_publishes == 1
    assert refreshed.last_publish_status == "published"


def test_publish_reddit_channel_with_connector(monkeypatch, tmp_path: Path) -> None:
    state_file = tmp_path / "runtime-state.json"
    cache_file = tmp_path / "candidates-cache.json"
    accounts_file = tmp_path / "accounts-state.json"
    monkeypatch.setenv("BRAND_STUDIO_DISCOVERY_MODE", "stub")
    monkeypatch.setenv("BRAND_STUDIO_STATE_FILE", str(state_file))
    monkeypatch.setenv("BRAND_STUDIO_CACHE_FILE", str(cache_file))
    monkeypatch.setenv("BRAND_STUDIO_ACCOUNTS_FILE", str(accounts_file))
    monkeypatch.setenv("REDDIT_CLIENT_ID", "id")
    monkeypatch.setenv("REDDIT_CLIENT_SECRET", "secret")
    monkeypatch.setenv("REDDIT_REFRESH_TOKEN", "refresh")

    service = BrandStudioService()
    account = service.create_channel_account(
        "reddit",
        ChannelAccountCreateRequest(
            display_name="Reddit account",
            target="r/python",
            is_default=True,
        ),
        actor="tester",
    )
    items, _ = service.list_candidates(channel=None, lang=None, limit=1, min_score=0.0)
    draft = service.generate_draft(
        candidate_id=items[0].id,
        channels=["reddit"],
        languages=["en"],
        tone="expert",
        actor="tester",
    )
    queue_item = service.queue_draft(
        draft_id=draft.draft_id,
        target_channel="reddit",
        target_language="en",
        target=None,
        target_repo=None,
        target_path=None,
        payload_override=None,
        actor="tester",
        account_id=account.account_id,
    )
    class FakeRedditPublisher:
        def publish_markdown(self, *, title: str, content: str, subreddit: str | None = None):  # noqa: ANN001
            assert title
            assert content
            assert subreddit == "r/python"

            class Result:
                external_id = "t3_abc"
                url = "https://reddit.com/r/python/comments/abc"
                message = "Published to Reddit r/python"

            return Result()

        def validate_connection(self):  # noqa: ANN201
            return True

    service._reddit_publisher = FakeRedditPublisher()  # type: ignore[attr-defined]
    result = service.publish_queue_item(
        item_id=queue_item.item_id,
        confirm_publish=True,
        actor="tester",
    )
    assert result.success is True
    assert result.status == "published"
    assert result.external_id == "t3_abc"
    refreshed = service.channel_accounts("reddit").items[0]
    assert refreshed.successful_publishes == 1
    assert refreshed.failed_publishes == 0


def test_publish_fails_for_unconfigured_hashnode_channel(monkeypatch, tmp_path: Path) -> None:
    state_file = tmp_path / "runtime-state.json"
    cache_file = tmp_path / "candidates-cache.json"
    accounts_file = tmp_path / "accounts-state.json"
    monkeypatch.setenv("BRAND_STUDIO_DISCOVERY_MODE", "stub")
    monkeypatch.setenv("BRAND_STUDIO_STATE_FILE", str(state_file))
    monkeypatch.setenv("BRAND_STUDIO_CACHE_FILE", str(cache_file))
    monkeypatch.setenv("BRAND_STUDIO_ACCOUNTS_FILE", str(accounts_file))

    service = BrandStudioService()
    account = service.create_channel_account(
        "hashnode",
        ChannelAccountCreateRequest(
            display_name="Hashnode account",
            target="my-publication",
            is_default=True,
        ),
        actor="tester",
    )
    items, _ = service.list_candidates(channel=None, lang=None, limit=1, min_score=0.0)
    draft = service.generate_draft(
        candidate_id=items[0].id,
        channels=["hashnode"],
        languages=["en"],
        tone="expert",
        actor="tester",
    )
    queue_item = service.queue_draft(
        draft_id=draft.draft_id,
        target_channel="hashnode",
        target_language="en",
        target=None,
        target_repo=None,
        target_path=None,
        payload_override=None,
        actor="tester",
        account_id=account.account_id,
    )
    result = service.publish_queue_item(
        item_id=queue_item.item_id,
        confirm_publish=True,
        actor="tester",
    )
    assert result.success is False
    assert result.status == "failed"
    assert "not configured" in result.message


@pytest.mark.parametrize(
    ("channel", "env_name", "env_value", "target"),
    [
        ("hashnode", "HASHNODE_TOKEN", "hash-token", "publication-1"),
        ("linkedin", "LINKEDIN_ACCESS_TOKEN", "li-token", "urn:li:person:abc"),
        ("medium", "MEDIUM_TOKEN", "med-token", "https://example.org/canonical"),
        ("hf_spaces", "HF_TOKEN", "hf-token", "org/my-space"),
        ("hf_blog", "HF_TOKEN", "hf-token", "org/my-blog"),
    ],
)
def test_publish_additional_channels_with_connectors(
    monkeypatch,
    tmp_path: Path,
    channel: str,
    env_name: str,
    env_value: str,
    target: str,
) -> None:
    state_file = tmp_path / "runtime-state.json"
    cache_file = tmp_path / "candidates-cache.json"
    accounts_file = tmp_path / "accounts-state.json"
    monkeypatch.setenv("BRAND_STUDIO_DISCOVERY_MODE", "stub")
    monkeypatch.setenv("BRAND_STUDIO_STATE_FILE", str(state_file))
    monkeypatch.setenv("BRAND_STUDIO_CACHE_FILE", str(cache_file))
    monkeypatch.setenv("BRAND_STUDIO_ACCOUNTS_FILE", str(accounts_file))
    monkeypatch.setenv(env_name, env_value)

    service = BrandStudioService()
    account = service.create_channel_account(
        channel,  # type: ignore[arg-type]
        ChannelAccountCreateRequest(
            display_name=f"{channel}-account",
            target=target,
            is_default=True,
        ),
        actor="tester",
    )
    items, _ = service.list_candidates(channel=None, lang=None, limit=1, min_score=0.0)
    draft = service.generate_draft(
        candidate_id=items[0].id,
        channels=[channel],
        languages=["en"],
        tone="expert",
        actor="tester",
    )
    queue_item = service.queue_draft(
        draft_id=draft.draft_id,
        target_channel=channel,
        target_language="en",
        target=None,
        target_repo=None,
        target_path=None,
        payload_override=None,
        actor="tester",
        account_id=account.account_id,
    )

    class FakePublisher:
        def validate_connection(self):  # noqa: ANN201
            return True

        def publish_markdown(self, **_kwargs):  # noqa: ANN003, ANN201
            class Result:
                external_id = "ext-1"
                url = "https://example.org/post/1"
                message = "Published"

            return Result()

    if channel == "hashnode":
        service._hashnode_publisher = FakePublisher()  # type: ignore[attr-defined]
    elif channel == "linkedin":
        service._linkedin_publisher = FakePublisher()  # type: ignore[attr-defined]
    elif channel == "medium":
        service._medium_publisher = FakePublisher()  # type: ignore[attr-defined]
    else:
        service._hf_publisher = FakePublisher()  # type: ignore[attr-defined]

    result = service.publish_queue_item(
        item_id=queue_item.item_id,
        confirm_publish=True,
        actor="tester",
    )
    assert result.success is True
    assert result.status == "published"


def test_channel_account_role_fields(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("BRAND_STUDIO_DISCOVERY_MODE", "stub")
    monkeypatch.setenv("BRAND_STUDIO_STATE_FILE", str(tmp_path / "runtime-state.json"))
    monkeypatch.setenv("BRAND_STUDIO_CACHE_FILE", str(tmp_path / "candidates-cache.json"))
    monkeypatch.setenv("BRAND_STUDIO_ACCOUNTS_FILE", str(tmp_path / "accounts-state.json"))

    service = BrandStudioService()
    primary = service.create_channel_account(
        "devto",
        ChannelAccountCreateRequest(
            display_name="Primary Devto",
            target="devto-main",
            is_default=True,
            role="primary",
        ),
        actor="tester",
    )
    assert primary.role == "primary"
    assert primary.supports_account_id is None

    supporting = service.create_channel_account(
        "devto",
        ChannelAccountCreateRequest(
            display_name="Supporting Devto",
            target="devto-secondary",
            role="supporting",
            supports_account_id=primary.account_id,
        ),
        actor="tester",
    )
    assert supporting.role == "supporting"
    assert supporting.supports_account_id == primary.account_id

    listed = service.channel_accounts("devto")
    roles = {acc.account_id: acc.role for acc in listed.items}
    assert roles[primary.account_id] == "primary"
    assert roles[supporting.account_id] == "supporting"


def test_generate_draft_supporting_variant_attribution(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("BRAND_STUDIO_DISCOVERY_MODE", "stub")
    monkeypatch.setenv("BRAND_STUDIO_STATE_FILE", str(tmp_path / "runtime-state.json"))
    monkeypatch.setenv("BRAND_STUDIO_CACHE_FILE", str(tmp_path / "candidates-cache.json"))
    monkeypatch.setenv("BRAND_STUDIO_ACCOUNTS_FILE", str(tmp_path / "accounts-state.json"))

    service = BrandStudioService()
    primary = service.create_channel_account(
        "devto",
        ChannelAccountCreateRequest(
            display_name="Primary Brand",
            target="devto-main",
            is_default=True,
            role="primary",
        ),
        actor="tester",
    )
    service.create_channel_account(
        "devto",
        ChannelAccountCreateRequest(
            display_name="Supporting Brand",
            target="devto-secondary",
            role="supporting",
            supports_account_id=primary.account_id,
        ),
        actor="tester",
    )

    items, _ = service.list_candidates(channel=None, lang=None, limit=1, min_score=0.0)
    draft = service.generate_draft(
        candidate_id=items[0].id,
        channels=["devto"],
        languages=["pl", "en"],
        tone=None,
        actor="tester",
    )
    devto_variants = [v for v in draft.variants if v.channel == "devto"]
    # 2 primary (pl, en) + 2 supporting (pl, en) = 4
    assert len(devto_variants) == 4

    supporting_pl = [v for v in devto_variants if "Cytując" in v.content]
    supporting_en = [v for v in devto_variants if "Quoting" in v.content]
    primary_variants = [
        v for v in devto_variants if "Cytując" not in v.content and "Quoting" not in v.content
    ]
    assert len(supporting_pl) == 1
    assert len(supporting_en) == 1
    assert len(primary_variants) == 2
    assert all("Oryginalne źródło wiedzy" in v.content for v in supporting_pl)
    assert all("Original knowledge source" in v.content for v in supporting_en)


def test_process_scheduled_queue_auto_publishes_due_items(monkeypatch, tmp_path: Path) -> None:
    from datetime import timedelta

    from venom_module_brand_studio.services.service import _utcnow

    monkeypatch.setenv("BRAND_STUDIO_DISCOVERY_MODE", "stub")
    monkeypatch.setenv("BRAND_STUDIO_STATE_FILE", str(tmp_path / "runtime-state.json"))
    monkeypatch.setenv("BRAND_STUDIO_CACHE_FILE", str(tmp_path / "candidates-cache.json"))
    monkeypatch.setenv("BRAND_STUDIO_ACCOUNTS_FILE", str(tmp_path / "accounts-state.json"))

    service = BrandStudioService()
    items, _ = service.list_candidates(channel=None, lang=None, limit=2, min_score=0.0)
    draft = service.generate_draft(
        candidate_id=items[0].id,
        channels=["x"],
        languages=["pl"],
        tone=None,
        actor="tester",
    )
    past_time = _utcnow() - timedelta(minutes=5)
    queue_item = service.queue_draft(
        draft_id=draft.draft_id,
        target_channel="x",
        target_language="pl",
        target=None,
        target_repo=None,
        target_path=None,
        payload_override=None,
        actor="tester",
        scheduled_at=past_time,
        publish_mode="auto",
    )
    assert queue_item.publish_mode == "auto"
    assert queue_item.scheduled_at == past_time

    processed = service.process_scheduled_queue()
    assert processed == 1

    refreshed = service.queue_items()
    assert refreshed[0].status == "published"


def test_process_scheduled_queue_skips_future_and_manual_items(
    monkeypatch, tmp_path: Path
) -> None:
    from datetime import timedelta

    from venom_module_brand_studio.services.service import _utcnow

    monkeypatch.setenv("BRAND_STUDIO_DISCOVERY_MODE", "stub")
    monkeypatch.setenv("BRAND_STUDIO_STATE_FILE", str(tmp_path / "runtime-state.json"))
    monkeypatch.setenv("BRAND_STUDIO_CACHE_FILE", str(tmp_path / "candidates-cache.json"))
    monkeypatch.setenv("BRAND_STUDIO_ACCOUNTS_FILE", str(tmp_path / "accounts-state.json"))

    service = BrandStudioService()
    items, _ = service.list_candidates(channel=None, lang=None, limit=2, min_score=0.0)
    draft = service.generate_draft(
        candidate_id=items[0].id,
        channels=["x"],
        languages=["pl"],
        tone=None,
        actor="tester",
    )
    future_time = _utcnow() + timedelta(hours=1)
    service.queue_draft(
        draft_id=draft.draft_id,
        target_channel="x",
        target_language="pl",
        target=None,
        target_repo=None,
        target_path=None,
        payload_override=None,
        actor="tester",
        scheduled_at=future_time,
        publish_mode="auto",
    )
    past_time = _utcnow() - timedelta(minutes=5)
    draft2 = service.generate_draft(
        candidate_id=items[0].id,
        channels=["x"],
        languages=["pl"],
        tone=None,
        actor="tester",
    )
    service.queue_draft(
        draft_id=draft2.draft_id,
        target_channel="x",
        target_language="pl",
        target=None,
        target_repo=None,
        target_path=None,
        payload_override=None,
        actor="tester",
        scheduled_at=past_time,
        publish_mode="manual",
    )

    processed = service.process_scheduled_queue()
    assert processed == 0


def test_queue_draft_stores_scheduled_at_and_publish_mode(monkeypatch, tmp_path: Path) -> None:
    from datetime import timedelta

    from venom_module_brand_studio.services.service import _utcnow

    monkeypatch.setenv("BRAND_STUDIO_DISCOVERY_MODE", "stub")
    monkeypatch.setenv("BRAND_STUDIO_STATE_FILE", str(tmp_path / "runtime-state.json"))
    monkeypatch.setenv("BRAND_STUDIO_CACHE_FILE", str(tmp_path / "candidates-cache.json"))

    service = BrandStudioService()
    items, _ = service.list_candidates(channel=None, lang=None, limit=1, min_score=0.0)
    draft = service.generate_draft(
        candidate_id=items[0].id,
        channels=["x"],
        languages=["pl"],
        tone=None,
        actor="tester",
    )
    scheduled = _utcnow() + timedelta(hours=2)
    queue_item = service.queue_draft(
        draft_id=draft.draft_id,
        target_channel="x",
        target_language="pl",
        target=None,
        target_repo=None,
        target_path=None,
        payload_override=None,
        actor="tester",
        scheduled_at=scheduled,
        publish_mode="auto",
    )
    assert queue_item.scheduled_at == scheduled
    assert queue_item.publish_mode == "auto"
