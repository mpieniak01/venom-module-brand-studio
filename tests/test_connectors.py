from __future__ import annotations

from venom_module_brand_studio.connectors import devto, github, sources
from venom_module_brand_studio.connectors.devto import DevtoPublisher, _normalize_devto_target
from venom_module_brand_studio.connectors.github import GitHubPublisher


def test_github_publisher_from_env(monkeypatch) -> None:
    monkeypatch.delenv("GITHUB_TOKEN_BRAND", raising=False)
    monkeypatch.delenv("BRAND_TARGET_REPO", raising=False)
    assert GitHubPublisher.from_env() is None

    monkeypatch.setenv("GITHUB_TOKEN_BRAND", "tok")
    monkeypatch.setenv("BRAND_TARGET_REPO", "owner/repo")
    monkeypatch.setenv("BRAND_GITHUB_PUBLISH_MODE", "pr")
    publisher = GitHubPublisher.from_env()
    assert publisher is not None
    assert publisher.mode == "pr"
    assert publisher.target_repo == "owner/repo"


def test_github_publish_via_commit(monkeypatch) -> None:
    calls: list[tuple[str, str]] = []

    def fake_request(method: str, url: str, *, token: str, payload=None):
        calls.append((method, url))
        if url.endswith("/repos/owner/repo"):
            return {"default_branch": "main"}
        if "contents/path/to.md?ref=main" in url:
            return {"sha": "existing-sha"}
        if "contents/path/to.md" in url and method == "PUT":
            assert payload is not None
            assert payload["sha"] == "existing-sha"
            return {"commit": {"sha": "c1", "html_url": "https://example.org/commit/c1"}}
        raise AssertionError(f"Unexpected call {method} {url}")

    monkeypatch.setattr(github, "_request_json", fake_request)
    publisher = GitHubPublisher(token="tok", target_repo="owner/repo", mode="commit")
    result = publisher.publish_markdown(path="path/to.md", content="# hi", title="Title")
    assert result.external_id == "c1"
    assert result.url == "https://example.org/commit/c1"
    assert calls


def test_github_publish_via_pr(monkeypatch) -> None:
    calls: list[tuple[str, str]] = []

    def fake_request(method: str, url: str, *, token: str, payload=None):
        calls.append((method, url))
        if "git/ref/heads/main" in url:
            return {"object": {"sha": "base-sha"}}
        if url.endswith("/git/refs") and method == "POST":
            return {"ref": payload["ref"]}
        if "/contents/path/to.md" in url and method == "PUT":
            return {"commit": {"sha": "c2"}}
        if url.endswith("/pulls") and method == "POST":
            return {"number": 42, "html_url": "https://example.org/pr/42"}
        raise AssertionError(f"Unexpected call {method} {url}")

    monkeypatch.setattr(github, "_request_json", fake_request)
    publisher = GitHubPublisher(
        token="tok",
        target_repo="owner/repo",
        mode="pr",
        default_branch="main",
    )
    result = publisher.publish_markdown(path="path/to.md", content="# hi", title="Roadmap update")
    assert result.external_id == "pr-42"
    assert result.url == "https://example.org/pr/42"
    assert calls


def test_sources_fetchers(monkeypatch) -> None:
    rss_xml = """
    <rss><channel>
      <item>
        <title>RSS One</title>
        <description>Desc</description>
        <link>https://example.org/rss?utm_source=x</link>
        <pubDate>Wed, 19 Feb 2026 10:00:00 GMT</pubDate>
      </item>
    </channel></rss>
    """
    arxiv_xml = """
    <feed xmlns="http://www.w3.org/2005/Atom">
      <entry>
        <id>https://arxiv.org/abs/1234.567</id>
        <title>Agent Paper</title>
        <summary>Summary</summary>
        <updated>2026-02-19T09:00:00Z</updated>
      </entry>
    </feed>
    """

    def fake_get_text(url: str, **_kwargs):
        if "arxiv" in url:
            return arxiv_xml
        return rss_xml

    def fake_get_json(url: str, **_kwargs):
        if "search/repositories" in url:
            return {
                "items": [
                    {
                        "full_name": "owner/repo",
                        "description": "Repo",
                        "html_url": "https://github.com/owner/repo",
                        "updated_at": "2026-02-19T10:00:00Z",
                    }
                ]
            }
        if url.endswith("/topstories.json"):
            return [1]
        if "/item/1.json" in url:
            return {"title": "HN one", "url": "https://example.org/hn", "time": 1760000000}
        raise AssertionError(f"Unexpected url {url}")

    monkeypatch.setattr(sources, "_http_get_text", fake_get_text)
    monkeypatch.setattr(sources, "_http_get_json", fake_get_json)

    rss_items = sources.fetch_rss_items(["https://example.org/feed.xml"])
    gh_items = sources.fetch_github_items(max_items=1)
    hn_items = sources.fetch_hn_items(max_items=1)
    arxiv_items = sources.fetch_arxiv_items(max_items=1)

    assert rss_items and gh_items and hn_items and arxiv_items
    assert gh_items[0]["source"] == "github"
    assert hn_items[0]["source"] == "hn"
    assert arxiv_items[0]["source"] == "arxiv"


def test_devto_publisher_from_env(monkeypatch) -> None:
    monkeypatch.delenv("DEVTO_API_KEY", raising=False)
    assert DevtoPublisher.from_env() is None

    monkeypatch.setenv("DEVTO_API_KEY", "devto-key")
    publisher = DevtoPublisher.from_env()
    assert publisher is not None
    assert publisher.api_key == "devto-key"


def test_devto_validate_connection(monkeypatch) -> None:
    calls: list[tuple[str, str]] = []

    def fake_request_json(method: str, url: str, *, api_key: str, payload=None):  # noqa: ANN001
        calls.append((method, url))
        assert api_key == "devto-key"
        assert payload is None
        return {"ok": True}

    monkeypatch.setattr(devto, "_request_json", fake_request_json)
    publisher = DevtoPublisher(api_key="devto-key")
    assert publisher.validate_connection() is True
    assert calls == [("GET", "https://dev.to/api/articles/me/all?per_page=1")]


def test_devto_publish_markdown_sanitizes_target(monkeypatch) -> None:
    captured_payload: dict[str, object] = {}

    def fake_request_json(method: str, url: str, *, api_key: str, payload=None):  # noqa: ANN001
        assert method == "POST"
        assert url == "https://dev.to/api/articles"
        assert api_key == "devto-key"
        assert isinstance(payload, dict)
        captured_payload.update(payload)
        return {"id": 7, "url": "https://dev.to/example/post"}

    monkeypatch.setattr(devto, "_request_json", fake_request_json)
    publisher = DevtoPublisher(api_key="devto-key")

    result = publisher.publish_markdown(
        title="Title",
        content="Body",
        target="safe-user",
    )
    assert result.external_id == "devto-7"
    assert result.url == "https://dev.to/example/post"

    article = captured_payload["article"]
    assert isinstance(article, dict)
    assert article["canonical_url"] == "https://dev.to/safe-user"

    publisher.publish_markdown(
        title="Title2",
        content="Body2",
        target="https://evil.example/x?y=1",
    )
    article = captured_payload["article"]
    assert isinstance(article, dict)
    assert "canonical_url" not in article


def test_normalize_devto_target() -> None:
    assert _normalize_devto_target(" user-name ") == "user-name"
    assert _normalize_devto_target("/org_name/team/") == "org_name/team"
    assert _normalize_devto_target("https://evil.example/x") is None
