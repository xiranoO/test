from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .github_readonly import API_VERSION, validate_repository


class GitHubPublishError(RuntimeError):
    pass


class GitHubCommentPublisher:
    """A deliberately narrow client that can only create one Issue comment."""

    def __init__(
        self,
        repository: str,
        token: str,
        api_base: str = "https://api.github.com",
        timeout: float = 20.0,
    ) -> None:
        if not token.strip():
            raise ValueError("GITHUB_WRITE_TOKEN 不能为空。")
        self.owner, self.repo = validate_repository(repository)
        self.repository = f"{self.owner}/{self.repo}"
        self.token = token
        self.api_base = api_base.rstrip("/")
        self.timeout = timeout

    def create_issue_comment(self, issue_number: int, body: str) -> dict[str, Any]:
        body = body.strip()
        if issue_number < 1:
            raise ValueError("Issue 编号必须大于 0。")
        if not body:
            raise ValueError("GitHub 回复不能为空。")
        request = Request(
            f"{self.api_base}/repos/{self.owner}/{self.repo}/issues/{issue_number}/comments",
            data=json.dumps({"body": body}, ensure_ascii=False).encode("utf-8"),
            method="POST",
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
                "X-GitHub-Api-Version": API_VERSION,
                "User-Agent": "github-issue-agent-mvp",
            },
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            try:
                message = json.loads(exc.read().decode("utf-8")).get("message", str(exc))
            except (UnicodeDecodeError, json.JSONDecodeError):
                message = str(exc)
            raise GitHubPublishError(f"GitHub API {exc.code}: {message}") from exc
        except URLError as exc:
            raise GitHubPublishError(f"无法连接 GitHub API: {exc.reason}") from exc
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise GitHubPublishError("GitHub 返回了无法解析的发布结果。") from exc
        return {
            "comment_id": payload.get("id"),
            "comment_url": payload.get("html_url"),
            "created_at": payload.get("created_at"),
        }
