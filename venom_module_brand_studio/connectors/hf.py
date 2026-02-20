from __future__ import annotations

import base64
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
        raise RuntimeError(f"Hugging Face API error {exc.code}: {details}") from exc


@dataclass
class HfPublishResult:
    external_id: str
    url: str | None
    message: str


class HfPublisher:
    def __init__(self, *, token: str) -> None:
        self.token = token

    @classmethod
    def from_env(cls) -> HfPublisher | None:
        token = (os.getenv("HF_TOKEN") or "").strip()
        if not token:
            return None
        return cls(token=token)

    def validate_connection(self) -> bool:
        _request_json("GET", "https://huggingface.co/api/whoami-v2", token=self.token)
        return True

    def publish_markdown(
        self,
        *,
        channel: str,
        title: str,
        content: str,
        target: str | None,
    ) -> HfPublishResult:
        repo_id = (target or "").strip()
        if not repo_id:
            raise ValueError("Hugging Face repo id target is required")
        repo_type = "space" if channel == "hf_spaces" else "dataset"
        slug = title.lower().replace(" ", "-")[:50]
        path = f"brand-studio/{slug or 'post'}.md"
        encoded = base64.b64encode(content.encode("utf-8")).decode("utf-8")
        payload = {
            "summary": f"Brand Studio publish: {title[:120]}",
            "files": [
                {
                    "path": path,
                    "content": encoded,
                    "encoding": "base64",
                }
            ],
        }
        _request_json(
            "POST",
            f"https://huggingface.co/api/{repo_type}s/{repo_id}/commit/main",
            token=self.token,
            payload=payload,
        )
        return HfPublishResult(
            external_id=f"hf-{repo_type}-{repo_id}:{path}",
            url=f"https://huggingface.co/{repo_type}s/{repo_id}/blob/main/{path}",
            message=f"Published to Hugging Face {repo_type}",
        )
