from __future__ import annotations

import base64
import json
import os
import re
from dataclasses import dataclass
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


def _request_json(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    form: dict[str, str] | None = None,
    payload: dict[str, object] | None = None,
) -> dict[str, object]:
    body: bytes | None = None
    effective_headers = dict(headers or {})
    if form is not None:
        body = urlencode(form).encode("utf-8")
        effective_headers["Content-Type"] = "application/x-www-form-urlencoded"
    elif payload is not None:
        body = json.dumps(payload).encode("utf-8")
        effective_headers["Content-Type"] = "application/json"

    request = Request(
        url=url,
        method=method,
        data=body,
        headers=effective_headers,
    )
    try:
        with urlopen(request, timeout=20) as response:  # nosec B310
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except HTTPError as exc:  # pragma: no cover - integration path
        details = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Reddit API error {exc.code}: {details}") from exc


def _normalize_subreddit(target: str | None) -> str | None:
    value = (target or "").strip().lower()
    if value.startswith("r/"):
        value = value[2:]
    if not value:
        return None
    if not re.fullmatch(r"[a-z0-9_]+", value):
        return None
    return value


@dataclass
class RedditPublishResult:
    external_id: str
    url: str | None
    message: str


class RedditPublisher:
    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        refresh_token: str,
        user_agent: str,
    ) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self.refresh_token = refresh_token
        self.user_agent = user_agent

    @classmethod
    def from_env(cls) -> RedditPublisher | None:
        client_id = (os.getenv("REDDIT_CLIENT_ID") or "").strip()
        client_secret = (os.getenv("REDDIT_CLIENT_SECRET") or "").strip()
        refresh_token = (os.getenv("REDDIT_REFRESH_TOKEN") or "").strip()
        if not client_id or not client_secret or not refresh_token:
            return None
        user_agent = (
            (os.getenv("REDDIT_USER_AGENT") or "").strip()
            or "venom-brand-studio/1.0 by /u/venom_brand_studio"
        )
        return cls(
            client_id=client_id,
            client_secret=client_secret,
            refresh_token=refresh_token,
            user_agent=user_agent,
        )

    def _access_token(self) -> str:
        auth = base64.b64encode(
            f"{self.client_id}:{self.client_secret}".encode("utf-8")
        ).decode("utf-8")
        response = _request_json(
            "POST",
            "https://www.reddit.com/api/v1/access_token",
            headers={
                "Authorization": f"Basic {auth}",
                "User-Agent": self.user_agent,
            },
            form={
                "grant_type": "refresh_token",
                "refresh_token": self.refresh_token,
            },
        )
        token = str(response.get("access_token") or "").strip()
        if not token:
            raise RuntimeError("Missing access_token in Reddit auth response")
        return token

    def validate_connection(self) -> bool:
        token = self._access_token()
        _request_json(
            "GET",
            "https://oauth.reddit.com/api/v1/me",
            headers={
                "Authorization": f"bearer {token}",
                "User-Agent": self.user_agent,
            },
        )
        return True

    def publish_markdown(
        self,
        *,
        title: str,
        content: str,
        subreddit: str | None,
    ) -> RedditPublishResult:
        sr = _normalize_subreddit(subreddit)
        if not sr:
            raise ValueError("Valid subreddit target is required for Reddit publish")
        token = self._access_token()
        response = _request_json(
            "POST",
            "https://oauth.reddit.com/api/submit",
            headers={
                "Authorization": f"bearer {token}",
                "User-Agent": self.user_agent,
            },
            form={
                "api_type": "json",
                "kind": "self",
                "sr": sr,
                "title": title[:300],
                "text": content,
            },
        )
        errors = (
            response.get("json", {})
            if isinstance(response.get("json"), dict)
            else {}
        )
        error_items = errors.get("errors", []) if isinstance(errors, dict) else []
        if isinstance(error_items, list) and error_items:
            raise RuntimeError(f"Reddit publish returned errors: {error_items}")
        post_name = (
            response.get("json", {}).get("data", {}).get("name")
            if isinstance(response.get("json"), dict)
            else None
        )
        external_id = str(post_name or "reddit-post")
        post_url = str(
            response.get("json", {}).get("data", {}).get("url", "")
            if isinstance(response.get("json"), dict)
            else ""
        )
        return RedditPublishResult(
            external_id=external_id,
            url=post_url or None,
            message=f"Published to Reddit r/{sr}",
        )
