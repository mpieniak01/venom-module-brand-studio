from __future__ import annotations

import json
import os
from dataclasses import dataclass
from urllib.error import HTTPError
from urllib.request import Request, urlopen


def _graphql(
    query: str,
    *,
    token: str,
    variables: dict[str, object] | None = None,
) -> dict[str, object]:
    payload = {"query": query, "variables": variables or {}}
    request = Request(
        url="https://gql.hashnode.com",
        method="POST",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": token,
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "venom-brand-studio/1.0",
        },
    )
    try:
        with urlopen(request, timeout=20) as response:  # nosec B310
            body = response.read().decode("utf-8")
            data = json.loads(body) if body else {}
    except HTTPError as exc:  # pragma: no cover - integration path
        details = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Hashnode API error {exc.code}: {details}") from exc
    if not isinstance(data, dict):
        raise RuntimeError("Invalid Hashnode response")
    errors = data.get("errors")
    if isinstance(errors, list) and errors:
        raise RuntimeError(f"Hashnode GraphQL errors: {errors}")
    result = data.get("data")
    if not isinstance(result, dict):
        raise RuntimeError("Missing data in Hashnode response")
    return result


@dataclass
class HashnodePublishResult:
    external_id: str
    url: str | None
    message: str


class HashnodePublisher:
    def __init__(self, *, token: str) -> None:
        self.token = token

    @classmethod
    def from_env(cls) -> HashnodePublisher | None:
        token = (os.getenv("HASHNODE_TOKEN") or "").strip()
        if not token:
            return None
        return cls(token=token)

    def validate_connection(self) -> bool:
        _graphql("query { me { username } }", token=self.token)
        return True

    def publish_markdown(
        self,
        *,
        title: str,
        content: str,
        target: str | None = None,
    ) -> HashnodePublishResult:
        if not (target or "").strip():
            raise ValueError("Hashnode publication id is required as target")
        mutation = """
            mutation PublishPost($input: PublishPostInput!) {
              publishPost(input: $input) {
                post {
                  id
                  slug
                  url
                }
              }
            }
        """
        data = _graphql(
            mutation,
            token=self.token,
            variables={
                "input": {
                    "title": title[:120],
                    "contentMarkdown": content,
                    "publicationId": target,
                }
            },
        )
        payload = data.get("publishPost")
        if not isinstance(payload, dict) or not isinstance(payload.get("post"), dict):
            raise RuntimeError("Missing publishPost.post in Hashnode response")
        post = payload["post"]
        post_id = str(post.get("id") or "hashnode-post")
        post_url = str(post.get("url") or "")
        return HashnodePublishResult(
            external_id=post_id,
            url=post_url or None,
            message="Published to Hashnode",
        )
