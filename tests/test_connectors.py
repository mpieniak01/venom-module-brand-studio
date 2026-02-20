from __future__ import annotations

from venom_module_brand_studio.connectors import (
    devto,
    github,
    hashnode,
    hf,
    linkedin,
    medium,
    reddit,
    sources,
)
from venom_module_brand_studio.connectors.devto import DevtoPublisher, _normalize_devto_target
from venom_module_brand_studio.connectors.github import GitHubPublisher
from venom_module_brand_studio.connectors.hashnode import HashnodePublisher
from venom_module_brand_studio.connectors.hf import HfPublisher
from venom_module_brand_studio.connectors.linkedin import LinkedInPublisher
from venom_module_brand_studio.connectors.medium import MediumPublisher
from venom_module_brand_studio.connectors.reddit import RedditPublisher, _normalize_subreddit


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


def test_reddit_publisher_from_env(monkeypatch) -> None:
    monkeypatch.delenv("REDDIT_CLIENT_ID", raising=False)
    monkeypatch.delenv("REDDIT_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("REDDIT_REFRESH_TOKEN", raising=False)
    assert RedditPublisher.from_env() is None

    monkeypatch.setenv("REDDIT_CLIENT_ID", "id")
    monkeypatch.setenv("REDDIT_CLIENT_SECRET", "secret")
    monkeypatch.setenv("REDDIT_REFRESH_TOKEN", "refresh")
    publisher = RedditPublisher.from_env()
    assert publisher is not None
    assert publisher.client_id == "id"


def test_reddit_validate_connection(monkeypatch) -> None:
    calls: list[tuple[str, str]] = []

    def fake_request_json(method: str, url: str, *, headers=None, form=None, payload=None):  # noqa: ANN001
        calls.append((method, url))
        if "access_token" in url:
            assert form is not None
            return {"access_token": "token"}
        assert headers is not None
        assert headers["Authorization"] == "bearer token"
        return {"name": "brand-user"}

    monkeypatch.setattr(reddit, "_request_json", fake_request_json)
    publisher = RedditPublisher(
        client_id="id",
        client_secret="secret",
        refresh_token="refresh",
        user_agent="ua",
    )
    assert publisher.validate_connection() is True
    assert calls[0][1].endswith("/api/v1/access_token")
    assert calls[1][1].endswith("/api/v1/me")


def test_reddit_publish_markdown(monkeypatch) -> None:
    def fake_request_json(method: str, url: str, *, headers=None, form=None, payload=None):  # noqa: ANN001
        if "access_token" in url:
            return {"access_token": "token"}
        if "/api/submit" in url:
            assert form is not None
            assert form["sr"] == "python"
            return {"json": {"data": {"name": "t3_abc", "url": "https://reddit.com/r/python/comments/abc"}}}
        raise AssertionError(f"Unexpected call {method} {url}")

    monkeypatch.setattr(reddit, "_request_json", fake_request_json)
    publisher = RedditPublisher(
        client_id="id",
        client_secret="secret",
        refresh_token="refresh",
        user_agent="ua",
    )
    result = publisher.publish_markdown(
        title="Title",
        content="Body",
        subreddit="r/python",
    )
    assert result.external_id == "t3_abc"


def test_hashnode_publisher_from_env(monkeypatch) -> None:
    monkeypatch.delenv("HASHNODE_TOKEN", raising=False)
    assert HashnodePublisher.from_env() is None
    monkeypatch.setenv("HASHNODE_TOKEN", "hash-token")
    publisher = HashnodePublisher.from_env()
    assert publisher is not None


def test_hashnode_validate_and_publish(monkeypatch) -> None:
    def fake_graphql(query: str, *, token: str, variables=None):  # noqa: ANN001
        assert token == "hash-token"
        if "query { me" in query:
            return {"me": {"username": "user"}}
        assert variables is not None
        return {
            "publishPost": {
                "post": {"id": "h1", "url": "https://hashnode.com/p/one"},
            }
        }

    monkeypatch.setattr(hashnode, "_graphql", fake_graphql)
    publisher = HashnodePublisher(token="hash-token")
    assert publisher.validate_connection() is True
    result = publisher.publish_markdown(title="Title", content="Body", target="pub-id")
    assert result.external_id == "h1"


def test_linkedin_publisher_from_env(monkeypatch) -> None:
    monkeypatch.delenv("LINKEDIN_ACCESS_TOKEN", raising=False)
    assert LinkedInPublisher.from_env() is None
    monkeypatch.setenv("LINKEDIN_ACCESS_TOKEN", "li-token")
    assert LinkedInPublisher.from_env() is not None


def test_linkedin_validate_and_publish(monkeypatch) -> None:
    def fake_request_json(method: str, url: str, *, token: str, payload=None):  # noqa: ANN001
        assert token == "li-token"
        if method == "GET":
            return {"id": "abc"}
        assert payload is not None
        return {"id": "urn:li:share:1"}

    monkeypatch.setattr(linkedin, "_request_json", fake_request_json)
    publisher = LinkedInPublisher(access_token="li-token")
    assert publisher.validate_connection() is True
    result = publisher.publish_markdown(title="Title", content="Body", target=None)
    assert result.external_id == "urn:li:share:1"


def test_medium_publisher_from_env(monkeypatch) -> None:
    monkeypatch.delenv("MEDIUM_TOKEN", raising=False)
    assert MediumPublisher.from_env() is None
    monkeypatch.setenv("MEDIUM_TOKEN", "med-token")
    assert MediumPublisher.from_env() is not None


def test_medium_validate_and_publish(monkeypatch) -> None:
    def fake_request_json(method: str, url: str, *, token: str, payload=None):  # noqa: ANN001
        assert token == "med-token"
        if method == "GET":
            return {"data": {"id": "user-1"}}
        assert payload is not None
        return {"data": {"id": "m1", "url": "https://medium.com/@u/m1"}}

    monkeypatch.setattr(medium, "_request_json", fake_request_json)
    publisher = MediumPublisher(token="med-token")
    assert publisher.validate_connection() is True
    result = publisher.publish_markdown(title="T", content="Body", target="https://example.org")
    assert result.external_id == "m1"


def test_hf_publisher_from_env(monkeypatch) -> None:
    monkeypatch.delenv("HF_TOKEN", raising=False)
    assert HfPublisher.from_env() is None
    monkeypatch.setenv("HF_TOKEN", "hf-token")
    assert HfPublisher.from_env() is not None


def test_hf_validate_and_publish(monkeypatch) -> None:
    calls: list[tuple[str, str]] = []

    def fake_request_json(method: str, url: str, *, token: str, payload=None):  # noqa: ANN001
        calls.append((method, url))
        assert token == "hf-token"
        if method == "GET":
            return {"name": "user"}
        assert payload is not None
        return {"ok": True}

    monkeypatch.setattr(hf, "_request_json", fake_request_json)
    publisher = HfPublisher(token="hf-token")
    assert publisher.validate_connection() is True
    result = publisher.publish_markdown(
        channel="hf_spaces",
        title="Title",
        content="Body",
        target="org/space",
    )
    assert result.external_id.startswith("hf-space-")
    assert calls
    assert result.url == "https://huggingface.co/spaces/org/space/blob/main/brand-studio/title.md"


def test_normalize_subreddit() -> None:
    assert _normalize_subreddit("r/Python") == "python"
    assert _normalize_subreddit("python_ai") == "python_ai"
    assert _normalize_subreddit("https://reddit.com/r/python") is None
