from __future__ import annotations

import json
import os
from urllib.parse import urlencode
from urllib.request import urlopen


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

    def search(self, query: str, *, num: int = 10) -> list[dict[str, object]]:
        num = max(1, min(10, num))
        params = urlencode({"key": self._api_key, "cx": self._cx, "q": query, "num": num})
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
