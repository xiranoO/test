from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .models import Issue, IssueComment


API_VERSION = "2026-03-10"
REPOSITORY_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


class GitHubReadOnlyError(RuntimeError):
    """A user-facing error raised by the read-only GitHub integration."""


def validate_repository(repository: str) -> tuple[str, str]:
    repository = repository.strip()
    if not REPOSITORY_PATTERN.fullmatch(repository):
        raise ValueError("仓库必须使用 owner/repo 格式，只能包含字母、数字、点、横线和下划线。")
    owner, name = repository.split("/", maxsplit=1)
    if name.endswith(".git"):
        name = name[:-4]
    if not owner or not name:
        raise ValueError("仓库 owner 和 repo 均不能为空。")
    return owner, name


def _issue_from_payload(payload: dict[str, Any]) -> Issue:
    labels = [
        item["name"] if isinstance(item, dict) else str(item)
        for item in payload.get("labels", [])
    ]
    return Issue(
        number=int(payload["number"]),
        title=str(payload.get("title") or ""),
        body=str(payload.get("body") or ""),
        labels=labels,
        state=str(payload.get("state") or "open"),
    )


class GitHubReadOnlyClient:
    """Small GitHub REST client that deliberately exposes GET operations only."""

    def __init__(
        self,
        repository: str,
        token: str | None = None,
        api_base: str = "https://api.github.com",
        timeout: float = 15.0,
    ) -> None:
        self.owner, self.repo = validate_repository(repository)
        self.repository = f"{self.owner}/{self.repo}"
        self.token = token
        self.api_base = api_base.rstrip("/")
        self.timeout = timeout
        self.rate_remaining: int | None = None

    def _get_json(self, path: str, params: dict[str, object] | None = None) -> Any:
        query = f"?{urlencode(params)}" if params else ""
        request = Request(
            f"{self.api_base}{path}{query}",
            method="GET",
            headers={
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": API_VERSION,
                "User-Agent": "github-issue-agent-mvp",
                **({"Authorization": f"Bearer {self.token}"} if self.token else {}),
            },
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                remaining = response.headers.get("x-ratelimit-remaining")
                self.rate_remaining = int(remaining) if remaining is not None else None
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            remaining = exc.headers.get("x-ratelimit-remaining")
            self.rate_remaining = int(remaining) if remaining is not None else None
            try:
                payload = json.loads(exc.read().decode("utf-8"))
                message = payload.get("message", str(exc))
            except (UnicodeDecodeError, json.JSONDecodeError):
                message = str(exc)
            if exc.code in {403, 429} and self.rate_remaining == 0:
                message = "GitHub API 请求额度已用完；请稍后重试或设置 GITHUB_TOKEN。"
            raise GitHubReadOnlyError(f"GitHub API {exc.code}: {message}") from exc
        except URLError as exc:
            raise GitHubReadOnlyError(f"无法连接 GitHub API: {exc.reason}") from exc

    def get_issue(self, issue_number: int) -> Issue:
        payload = self._get_json(
            f"/repos/{self.owner}/{self.repo}/issues/{issue_number}"
        )
        if "pull_request" in payload:
            raise GitHubReadOnlyError(f"#{issue_number} 是 Pull Request，不是 Issue。")
        return _issue_from_payload(payload)

    def list_issues(self, limit: int = 100, state: str = "all") -> list[Issue]:
        if not 1 <= limit <= 300:
            raise ValueError("历史 Issue 数量必须在 1 到 300 之间。")
        if state not in {"open", "closed", "all"}:
            raise ValueError("Issue 状态必须是 open、closed 或 all。")
        issues: list[Issue] = []
        page = 1
        max_pages = min(5, (limit + 99) // 100 + 2)
        while len(issues) < limit and page <= max_pages:
            # Keep page size stable so filtering PRs cannot shift later page boundaries.
            per_page = 100
            payload = self._get_json(
                f"/repos/{self.owner}/{self.repo}/issues",
                {"state": state, "per_page": per_page, "page": page},
            )
            if not isinstance(payload, list):
                raise GitHubReadOnlyError("GitHub 返回了非预期的 Issue 列表格式。")
            issues.extend(
                _issue_from_payload(item)
                for item in payload
                if "pull_request" not in item
            )
            if len(payload) < per_page:
                break
            page += 1
        return issues[:limit]

    def list_issue_comments(
        self, issue_number: int, limit: int = 50
    ) -> list[IssueComment]:
        if not 1 <= limit <= 100:
            raise ValueError("Issue 评论数量必须在 1 到 100 之间。")
        payload = self._get_json(
            f"/repos/{self.owner}/{self.repo}/issues/{issue_number}/comments",
            {"per_page": limit, "page": 1},
        )
        if not isinstance(payload, list):
            raise GitHubReadOnlyError("GitHub 返回了非预期的 Issue 评论格式。")
        comments = []
        for item in payload[:limit]:
            user = item.get("user") or {}
            comments.append(
                IssueComment(
                    author=str(user.get("login") or "unknown"),
                    body=str(item.get("body") or ""),
                    created_at=str(item.get("created_at") or ""),
                    author_association=str(
                        item.get("author_association") or "NONE"
                    ).upper(),
                )
            )
        return comments


@dataclass(frozen=True)
class RepositorySnapshot:
    path: Path
    reused: bool


class PublicRepositoryCache:
    """Creates a shallow, read-only working snapshot for local code search."""

    def __init__(self, cache_root: Path) -> None:
        self.cache_root = cache_root.resolve()

    def ensure(self, repository: str) -> RepositorySnapshot:
        owner, repo = validate_repository(repository)
        target = (self.cache_root / owner / repo).resolve()
        if self.cache_root not in target.parents:
            raise GitHubReadOnlyError("仓库缓存路径超出允许范围。")
        if (target / ".git").is_dir():
            return RepositorySnapshot(target, reused=True)
        if target.exists():
            raise GitHubReadOnlyError(f"缓存目录已存在但不是 Git 仓库：{target}")

        target.parent.mkdir(parents=True, exist_ok=True)
        url = f"https://github.com/{owner}/{repo}.git"
        try:
            subprocess.run(
                ["git", "clone", "--depth", "1", "--", url, str(target)],
                check=True,
                capture_output=True,
                text=True,
                timeout=120,
            )
        except FileNotFoundError as exc:
            raise GitHubReadOnlyError("系统中未找到 git，无法克隆公开仓库。") from exc
        except subprocess.TimeoutExpired as exc:
            raise GitHubReadOnlyError("克隆仓库超时，请检查网络或选择更小的仓库。") from exc
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or exc.stdout or "未知 Git 错误").strip()
            raise GitHubReadOnlyError(f"克隆公开仓库失败：{detail}") from exc
        return RepositorySnapshot(target, reused=False)
