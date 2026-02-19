from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from urllib.parse import quote_plus
from urllib.request import Request, urlopen
from xml.etree import ElementTree


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _safe_text(value: str | None, *, default: str = "") -> str:
    return (value or default).strip()


def _age_minutes_from_dt(published_at: datetime | None) -> int:
    if published_at is None:
        return 24 * 60
    aware = (
        published_at.astimezone(UTC)
        if published_at.tzinfo
        else published_at.replace(tzinfo=UTC)
    )
    delta = _utcnow() - aware
    return max(0, int(delta.total_seconds() // 60))


def _http_get_json(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: float = 8.0,
) -> object:
    request = Request(url, headers=headers or {})
    with urlopen(request, timeout=timeout) as response:  # nosec B310
        return json.loads(response.read().decode("utf-8"))


def _http_get_text(url: str, *, headers: dict[str, str] | None = None, timeout: float = 8.0) -> str:
    request = Request(url, headers=headers or {})
    with urlopen(request, timeout=timeout) as response:  # nosec B310
        return response.read().decode("utf-8")


def _parse_rfc_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
        return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    except Exception:
        return None


@dataclass
class SourceItem:
    source: str
    url: str
    topic: str
    summary: str
    language: str
    age_minutes: int

    def as_dict(self) -> dict[str, object]:
        return {
            "source": self.source,
            "url": self.url,
            "topic": self.topic,
            "summary": self.summary,
            "language": self.language,
            "age_minutes": self.age_minutes,
        }


def fetch_rss_items(urls: list[str], *, max_items_per_feed: int = 8) -> list[dict[str, object]]:
    items: list[SourceItem] = []
    for url in urls:
        try:
            xml = _http_get_text(url, headers={"User-Agent": "venom-brand-studio/1.0"})
            root = ElementTree.fromstring(xml)
            for index, entry in enumerate(root.findall(".//item")):
                if index >= max_items_per_feed:
                    break
                topic = _safe_text(entry.findtext("title"), default="RSS topic")
                summary = _safe_text(entry.findtext("description"), default=topic)[:500]
                entry_url = _safe_text(entry.findtext("link"), default=url)
                pub_date = _parse_rfc_datetime(entry.findtext("pubDate"))
                items.append(
                    SourceItem(
                        source="rss",
                        url=entry_url,
                        topic=topic,
                        summary=summary,
                        language="other",
                        age_minutes=_age_minutes_from_dt(pub_date),
                    )
                )
        except Exception:
            continue
    return [item.as_dict() for item in items]


def fetch_github_items(*, max_items: int = 12) -> list[dict[str, object]]:
    query = quote_plus("topic:ai stars:>200")
    url = f"https://api.github.com/search/repositories?q={query}&sort=updated&order=desc&per_page={max_items}"
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "venom-brand-studio/1.0",
    }
    payload = _http_get_json(url, headers=headers)
    repositories = payload.get("items", []) if isinstance(payload, dict) else []
    items: list[SourceItem] = []
    for repository in repositories:
        if not isinstance(repository, dict):
            continue
        topic = _safe_text(str(repository.get("full_name", "GitHub repository")))
        summary = _safe_text(str(repository.get("description") or topic))[:500]
        repo_url = _safe_text(str(repository.get("html_url")), default="https://github.com")
        updated_at = _safe_text(str(repository.get("updated_at")))
        published_at = None
        if updated_at:
            try:
                published_at = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
            except Exception:
                published_at = None
        items.append(
            SourceItem(
                source="github",
                url=repo_url,
                topic=topic,
                summary=summary,
                language="en",
                age_minutes=_age_minutes_from_dt(published_at),
            )
        )
    return [item.as_dict() for item in items]


def fetch_hn_items(*, max_items: int = 12) -> list[dict[str, object]]:
    top_ids = _http_get_json(
        "https://hacker-news.firebaseio.com/v0/topstories.json",
        headers={"User-Agent": "venom-brand-studio/1.0"},
    )
    ids = top_ids[:max_items] if isinstance(top_ids, list) else []
    items: list[SourceItem] = []
    for story_id in ids:
        try:
            payload = _http_get_json(
                f"https://hacker-news.firebaseio.com/v0/item/{story_id}.json",
                headers={"User-Agent": "venom-brand-studio/1.0"},
            )
            if not isinstance(payload, dict):
                continue
            topic = _safe_text(str(payload.get("title")), default="HN story")
            summary = topic
            story_url = _safe_text(
                str(payload.get("url") or ""),
                default=f"https://news.ycombinator.com/item?id={story_id}",
            )
            timestamp = payload.get("time")
            published_at = (
                datetime.fromtimestamp(timestamp, tz=UTC)
                if isinstance(timestamp, int)
                else None
            )
            items.append(
                SourceItem(
                    source="hn",
                    url=story_url,
                    topic=topic,
                    summary=summary,
                    language="en",
                    age_minutes=_age_minutes_from_dt(published_at),
                )
            )
        except Exception:
            continue
    return [item.as_dict() for item in items]


def fetch_arxiv_items(*, max_items: int = 12) -> list[dict[str, object]]:
    url = (
        "https://export.arxiv.org/api/query?"
        f"search_query=all:llm+OR+all:agent&start=0&max_results={max_items}&sortBy=submittedDate&sortOrder=descending"
    )
    xml = _http_get_text(url, headers={"User-Agent": "venom-brand-studio/1.0"})
    root = ElementTree.fromstring(xml)
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    items: list[SourceItem] = []
    for entry in root.findall("atom:entry", ns):
        topic = _safe_text(
            entry.findtext("atom:title", default="", namespaces=ns),
            default="arXiv paper",
        )
        summary = _safe_text(
            entry.findtext("atom:summary", default="", namespaces=ns),
            default=topic,
        )[:500]
        paper_url = _safe_text(
            entry.findtext("atom:id", default="", namespaces=ns),
            default="https://arxiv.org",
        )
        updated = _safe_text(entry.findtext("atom:updated", default="", namespaces=ns))
        published_at = None
        if updated:
            try:
                published_at = datetime.fromisoformat(updated.replace("Z", "+00:00"))
            except Exception:
                published_at = None
        items.append(
            SourceItem(
                source="arxiv",
                url=paper_url,
                topic=topic,
                summary=summary,
                language="en",
                age_minutes=_age_minutes_from_dt(published_at),
            )
        )
    return [item.as_dict() for item in items]
