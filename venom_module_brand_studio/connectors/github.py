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
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "venom-brand-studio/1.0",
            "Content-Type": "application/json",
        },
    )
    try:
        with urlopen(request, timeout=15) as response:  # nosec B310
            body = response.read().decode("utf-8")
            return json.loads(body) if body else {}
    except HTTPError as exc:  # pragma: no cover - integration path
        details = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"GitHub API error {exc.code}: {details}") from exc


@dataclass
class GitHubPublishResult:
    external_id: str
    url: str | None
    message: str


class GitHubPublisher:
    def __init__(
        self,
        *,
        token: str,
        target_repo: str,
        mode: str = "commit",
        default_branch: str | None = None,
    ) -> None:
        self.token = token
        self.target_repo = target_repo
        self.mode = mode
        self.default_branch = default_branch

    @classmethod
    def from_env(cls) -> "GitHubPublisher | None":
        token = (os.getenv("GITHUB_TOKEN_BRAND") or "").strip()
        repo = (os.getenv("BRAND_TARGET_REPO") or "").strip()
        if not token or not repo:
            return None
        mode = (os.getenv("BRAND_GITHUB_PUBLISH_MODE") or "commit").strip().lower()
        default_branch = (os.getenv("BRAND_GITHUB_BASE_BRANCH") or "").strip() or None
        return cls(token=token, target_repo=repo, mode=mode, default_branch=default_branch)

    def publish_markdown(
        self,
        *,
        path: str,
        content: str,
        title: str,
    ) -> GitHubPublishResult:
        mode = self.mode if self.mode in {"commit", "pr"} else "commit"
        if mode == "pr":
            return self._publish_via_pr(path=path, content=content, title=title)
        return self._publish_via_commit(path=path, content=content, title=title)

    def validate_connection(self) -> bool:
        self._repo()
        return True

    def _repo(self) -> dict[str, object]:
        return _request_json(
            "GET",
            f"https://api.github.com/repos/{self.target_repo}",
            token=self.token,
        )

    def _base_branch(self) -> str:
        if self.default_branch:
            return self.default_branch
        repo = self._repo()
        branch = repo.get("default_branch")
        return str(branch) if isinstance(branch, str) and branch else "main"

    def _publish_via_commit(self, *, path: str, content: str, title: str) -> GitHubPublishResult:
        branch = self._base_branch()
        content_url = f"https://api.github.com/repos/{self.target_repo}/contents/{path}"

        existing_sha: str | None = None
        try:
            existing = _request_json(
                "GET",
                f"{content_url}?ref={branch}",
                token=self.token,
            )
            existing_sha = str(existing.get("sha")) if existing.get("sha") else None
        except RuntimeError:
            existing_sha = None

        payload: dict[str, object] = {
            "message": f"brand-studio: {title}",
            "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
            "branch": branch,
        }
        if existing_sha:
            payload["sha"] = existing_sha

        published = _request_json("PUT", content_url, token=self.token, payload=payload)
        commit = published.get("commit") if isinstance(published.get("commit"), dict) else {}
        commit_sha = str(commit.get("sha") or "")
        html_url = str(
            (commit.get("html_url") if isinstance(commit, dict) else "")
            or published.get("content", {}).get("html_url", "")
        )
        return GitHubPublishResult(
            external_id=commit_sha or "commit",
            url=html_url or None,
            message="Published via commit",
        )

    def _publish_via_pr(self, *, path: str, content: str, title: str) -> GitHubPublishResult:
        branch = self._base_branch()
        ref = _request_json(
            "GET",
            f"https://api.github.com/repos/{self.target_repo}/git/ref/heads/{branch}",
            token=self.token,
        )
        base_sha = str(ref.get("object", {}).get("sha", ""))
        if not base_sha:
            raise RuntimeError("Cannot resolve base branch sha for PR publish")

        pr_branch = f"brand-studio-{title.lower().replace(' ', '-')[:24]}"
        _request_json(
            "POST",
            f"https://api.github.com/repos/{self.target_repo}/git/refs",
            token=self.token,
            payload={"ref": f"refs/heads/{pr_branch}", "sha": base_sha},
        )

        content_url = f"https://api.github.com/repos/{self.target_repo}/contents/{path}"
        _request_json(
            "PUT",
            content_url,
            token=self.token,
            payload={
                "message": f"brand-studio: {title}",
                "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
                "branch": pr_branch,
            },
        )

        pr = _request_json(
            "POST",
            f"https://api.github.com/repos/{self.target_repo}/pulls",
            token=self.token,
            payload={
                "title": f"Brand Studio: {title}",
                "head": pr_branch,
                "base": branch,
                "body": "Automated draft publication from Brand Studio module.",
            },
        )
        pr_id = str(pr.get("number") or "pr")
        pr_url = str(pr.get("html_url") or "")
        return GitHubPublishResult(
            external_id=f"pr-{pr_id}",
            url=pr_url or None,
            message="Published via pull request",
        )
