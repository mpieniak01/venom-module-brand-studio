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
            "X-Restli-Protocol-Version": "2.0.0",
            "User-Agent": "venom-brand-studio/1.0",
        },
    )
    try:
        with urlopen(request, timeout=20) as response:  # nosec B310
            body = response.read().decode("utf-8")
            return json.loads(body) if body else {}
    except HTTPError as exc:  # pragma: no cover - integration path
        details = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"LinkedIn API error {exc.code}: {details}") from exc


@dataclass
class LinkedInPublishResult:
    external_id: str
    url: str | None
    message: str


class LinkedInPublisher:
    def __init__(self, *, access_token: str) -> None:
        self.access_token = access_token

    @classmethod
    def from_env(cls) -> LinkedInPublisher | None:
        token = (os.getenv("LINKEDIN_ACCESS_TOKEN") or "").strip()
        if not token:
            return None
        return cls(access_token=token)

    def _author_urn(self, target: str | None) -> str:
        raw_target = (target or "").strip()
        if raw_target.startswith("urn:li:"):
            return raw_target
        me = _request_json("GET", "https://api.linkedin.com/v2/me", token=self.access_token)
        person_id = str(me.get("id") or "").strip()
        if not person_id:
            raise RuntimeError("Missing LinkedIn member id in /me response")
        return f"urn:li:person:{person_id}"

    def validate_connection(self) -> bool:
        _request_json("GET", "https://api.linkedin.com/v2/me", token=self.access_token)
        return True

    def publish_markdown(
        self,
        *,
        title: str,
        content: str,
        target: str | None = None,
    ) -> LinkedInPublishResult:
        author_urn = self._author_urn(target)
        payload = {
            "author": author_urn,
            "lifecycleState": "PUBLISHED",
            "specificContent": {
                "com.linkedin.ugc.ShareContent": {
                    "shareCommentary": {"text": f"{title}\n\n{content}".strip()},
                    "shareMediaCategory": "NONE",
                }
            },
            "visibility": {"com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"},
        }
        response = _request_json(
            "POST",
            "https://api.linkedin.com/v2/ugcPosts",
            token=self.access_token,
            payload=payload,
        )
        post_id = str(response.get("id") or "linkedin-post")
        return LinkedInPublishResult(
            external_id=post_id,
            url=None,
            message="Published to LinkedIn",
        )
