from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from urllib.error import HTTPError
from urllib.request import Request, urlopen


def _request_json(
    method: str,
    url: str,
    *,
    api_key: str,
    payload: dict[str, object] | None = None,
) -> dict[str, object]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = Request(
        url=url,
        method=method,
        data=data,
        headers={
            "api-key": api_key,
            "Accept": "application/json",
            "User-Agent": "venom-brand-studio/1.0",
            "Content-Type": "application/json",
        },
    )
    try:
        with urlopen(request, timeout=20) as response:  # nosec B310
            body = response.read().decode("utf-8")
            return json.loads(body) if body else {}
    except HTTPError as exc:  # pragma: no cover - integration path
        details = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Dev.to API error {exc.code}: {details}") from exc


@dataclass
class DevtoPublishResult:
    external_id: str
    url: str | None
    message: str


class DevtoPublisher:
    def __init__(self, *, api_key: str) -> None:
        self.api_key = api_key

    @classmethod
    def from_env(cls) -> DevtoPublisher | None:
        api_key = (os.getenv("DEVTO_API_KEY") or "").strip()
        if not api_key:
            return None
        return cls(api_key=api_key)

    def validate_connection(self) -> bool:
        _request_json(
            "GET",
            "https://dev.to/api/articles/me/all?per_page=1",
            api_key=self.api_key,
        )
        return True

    def publish_markdown(
        self,
        *,
        title: str,
        content: str,
        target: str | None = None,
    ) -> DevtoPublishResult:
        tags = [
            item.strip().lower()
            for item in (os.getenv("DEVTO_DEFAULT_TAGS") or "ai,engineering").split(",")
            if item.strip()
        ][:4]

        article_payload: dict[str, object] = {
            "title": title,
            "body_markdown": content,
            "published": True,
            "tags": tags,
        }
        # Optional hint for operator-facing grouping (not a secret).
        if target:
            normalized_target = _normalize_devto_target(target)
            if normalized_target:
                article_payload["canonical_url"] = f"https://dev.to/{normalized_target}"

        response = _request_json(
            "POST",
            "https://dev.to/api/articles",
            api_key=self.api_key,
            payload={"article": article_payload},
        )
        article_id = str(response.get("id") or "devto")
        article_url = str(response.get("url") or "")
        return DevtoPublishResult(
            external_id=f"devto-{article_id}",
            url=article_url or None,
            message="Published to Dev.to",
        )


def _normalize_devto_target(target: str) -> str | None:
    value = target.strip().strip("/")
    if not value:
        return None
    # Allow only safe dev.to username/path slugs.
    if not re.fullmatch(r"[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*", value):
        return None
    return value
