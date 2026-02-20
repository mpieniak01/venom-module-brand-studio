from __future__ import annotations

import json
import os
import re
from urllib.parse import urlencode
from urllib.request import urlopen

_SAFE_QUERY_RE = re.compile(r"[^\w\s\-.,'\"]", re.UNICODE)
_MAX_QUERY_LENGTH = 256


class GoogleCSEConnector:
    """Minimal Google Programmable Search Engine (CSE) JSON API connector."""

    BASE_URL = "https://www.googleapis.com/customsearch/v1"

    def __init__(self, *, api_key: str, cx: str) -> None:
        self._api_key = api_key
        self._cx = cx

    @classmethod
    def from_env(cls) -> "GoogleCSEConnector | None":
        api_key = (os.getenv("BRAND_STUDIO_GOOGLE_CSE_API_KEY") or "").strip()
        cx = (os.getenv("BRAND_STUDIO_GOOGLE_CSE_CX") or "").strip()
        if not api_key or not cx:
            return None
        return cls(api_key=api_key, cx=cx)

    @staticmethod
    def _sanitize_query(query: str) -> str:
        """Sanitize the search query to prevent malicious payloads."""
        sanitized = _SAFE_QUERY_RE.sub(" ", query).strip()
        return sanitized[:_MAX_QUERY_LENGTH]

    def search(self, query: str, *, num: int = 10) -> list[dict[str, object]]:
        num = max(1, min(10, num))
        safe_query = self._sanitize_query(query)
        if not safe_query:
            return []
        params = urlencode({"key": self._api_key, "cx": self._cx, "q": safe_query, "num": num})
        url = f"{self.BASE_URL}?{params}"
        with urlopen(url, timeout=15) as response:  # noqa: S310
            payload = json.loads(response.read().decode("utf-8"))
        items: list[dict[str, object]] = []
        for i, item in enumerate(payload.get("items") or [], start=1):
            items.append(
                {
                    "url": str(item.get("link") or ""),
                    "title": str(item.get("title") or ""),
                    "snippet": str(item.get("snippet") or ""),
                    "position": i,
                }
            )
        return items
