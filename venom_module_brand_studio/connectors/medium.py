from __future__ import annotations

import json
import os
from dataclasses import dataclass
from urllib.error import HTTPError
from urllib.request import Request, urlopen


def _request_json(
    method: str,
    url: str,
    *,
    token: str,
    payload: dict[str, object] | None = None,
) -> dict[str, object]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = Request(
        url=url,
        method=method,
        data=data,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "venom-brand-studio/1.0",
        },
    )
    try:
        with urlopen(request, timeout=20) as response:  # nosec B310
            body = response.read().decode("utf-8")
            return json.loads(body) if body else {}
    except HTTPError as exc:  # pragma: no cover - integration path
        details = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Medium API error {exc.code}: {details}") from exc


@dataclass
class MediumPublishResult:
    external_id: str
    url: str | None
    message: str


class MediumPublisher:
    def __init__(self, *, token: str) -> None:
        self.token = token

    @classmethod
    def from_env(cls) -> MediumPublisher | None:
        token = (os.getenv("MEDIUM_TOKEN") or "").strip()
        if not token:
            return None
        return cls(token=token)

    def _user_id(self) -> str:
        response = _request_json("GET", "https://api.medium.com/v1/me", token=self.token)
        user = response.get("data")
        if not isinstance(user, dict):
            raise RuntimeError("Missing data in Medium /me response")
        user_id = str(user.get("id") or "").strip()
        if not user_id:
            raise RuntimeError("Missing user id in Medium /me response")
        return user_id

    def validate_connection(self) -> bool:
        self._user_id()
        return True

    def publish_markdown(
        self,
        *,
        title: str,
        content: str,
        target: str | None = None,
    ) -> MediumPublishResult:
        user_id = self._user_id()
        payload: dict[str, object] = {
            "title": title[:100],
            "contentFormat": "markdown",
            "content": content,
            "publishStatus": "public",
        }
        canonical_url = (target or "").strip()
        if canonical_url.startswith("http://") or canonical_url.startswith("https://"):
            payload["canonicalUrl"] = canonical_url
        response = _request_json(
            "POST",
            f"https://api.medium.com/v1/users/{user_id}/posts",
            token=self.token,
            payload=payload,
        )
        data = response.get("data")
        if not isinstance(data, dict):
            raise RuntimeError("Missing data in Medium publish response")
        post_id = str(data.get("id") or "medium-post")
        post_url = str(data.get("url") or "")
        return MediumPublishResult(
            external_id=post_id,
            url=post_url or None,
            message="Published to Medium",
        )
