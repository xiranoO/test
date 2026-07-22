from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from .audit import ReviewStateError, ReviewStore
from .data import load_issues
from .github_readonly import GitHubReadOnlyClient, GitHubReadOnlyError, PublicRepositoryCache
from .github_write import GitHubCommentPublisher, GitHubPublishError
from .providers import DeepSeekProvider
from .workflow import IssueWorkflow


class AnalyzeRequest(BaseModel):
    repository: str | None = None
    issue_number: int = Field(ge=1)
    history_limit: int = Field(default=100, ge=1, le=300)
    provider: Literal["auto", "heuristic", "deepseek"] = "auto"


class RepositoryIssuesRequest(BaseModel):
    repository: str = Field(min_length=3, max_length=200)
    limit: int = Field(default=50, ge=1, le=100)


class DraftRequest(BaseModel):
    draft: str = Field(min_length=1, max_length=20_000)


class ConfirmRequest(BaseModel):
    approval_token: str = Field(min_length=12, max_length=12)
    mode: Literal["simulation", "github"] = "simulation"
    confirmation_phrase: str = Field(default="", max_length=300)


def _github_write_enabled() -> bool:
    return os.getenv("ENABLE_GITHUB_WRITE", "").lower() in {"1", "true", "yes"}


def _provider(name: str) -> DeepSeekProvider | None:
    key = os.getenv("DEEPSEEK_API_KEY")
    if name == "heuristic":
        return None
    if name == "deepseek" and not key:
        raise ValueError("选择 DeepSeek 时必须设置 DEEPSEEK_API_KEY。")
    if not key:
        return None
    return DeepSeekProvider(
        api_key=key,
        model=os.getenv("LLM_MODEL", "deepseek-v4-flash"),
        base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
    )


def create_app(root: Path | None = None, database_path: Path | None = None) -> FastAPI:
    project_root = (root or Path(__file__).resolve().parent.parent).resolve()
    store = ReviewStore(database_path or project_root / ".data" / "reviews.sqlite3")
    templates = Jinja2Templates(directory=project_root / "templates")

    app = FastAPI(title="Issue Lens", version="0.3.0")
    app.mount("/static", StaticFiles(directory=project_root / "static"), name="static")
    app.state.review_store = store

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):
        return templates.TemplateResponse(request, "index.html")

    @app.get("/health")
    def health() -> dict[str, str | bool]:
        return {
            "status": "ok",
            "github_mode": "controlled-write" if _github_write_enabled() else "read-only",
            "github_write_enabled": _github_write_enabled(),
        }

    @app.post("/api/analyze")
    def analyze(payload: AnalyzeRequest) -> dict:
        try:
            if payload.repository:
                client = GitHubReadOnlyClient(
                    payload.repository, token=os.getenv("GITHUB_TOKEN")
                )
                issue = client.get_issue(payload.issue_number)
                issue = replace(
                    issue,
                    comments=client.list_issue_comments(payload.issue_number),
                )
                issues = client.list_issues(payload.history_limit)
                if all(item.number != issue.number for item in issues):
                    issues.append(issue)
                snapshot = PublicRepositoryCache(
                    project_root / ".cache" / "github"
                ).ensure(payload.repository)
                repo_path = snapshot.path
                source = {
                    "mode": "github-read-only",
                    "repository": client.repository,
                    "snapshot_reused": snapshot.reused,
                    "rate_remaining": client.rate_remaining,
                    "comments_loaded": len(issue.comments),
                }
            else:
                issues = load_issues(project_root / "data" / "demo" / "issues.json")
                issue = next(
                    (item for item in issues if item.number == payload.issue_number), None
                )
                if issue is None:
                    raise ValueError("离线示例中找不到这个 Issue 编号。")
                repo_path = project_root / "demo_repo"
                source = {
                    "mode": "offline-demo",
                    "repository": None,
                    "snapshot_reused": None,
                    "comments_loaded": len(issue.comments),
                }
            result = IssueWorkflow(
                repo_path, issues, provider=_provider(payload.provider)
            ).analyze(issue)
            IssueWorkflow.request_approval(result)
            return store.create_run(
                payload.repository,
                payload.issue_number,
                source,
                result.to_dict(),
            )
        except (GitHubReadOnlyError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/repository-issues")
    def repository_issues(payload: RepositoryIssuesRequest) -> dict:
        try:
            client = GitHubReadOnlyClient(
                payload.repository, token=os.getenv("GITHUB_TOKEN")
            )
            issues = sorted(
                client.list_issues(payload.limit, state="open"),
                key=lambda issue: issue.number,
            )
            return {
                "repository": client.repository,
                "issues": [
                    {
                        "number": issue.number,
                        "title": issue.title,
                        "labels": issue.labels,
                        "state": issue.state,
                    }
                    for issue in issues
                ],
                "rate_remaining": client.rate_remaining,
                "selection_limit": 10,
            }
        except (GitHubReadOnlyError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/runs")
    def list_runs(repository: str | None = None, limit: int = 50) -> dict:
        try:
            return {"runs": store.list_runs(repository, limit)}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: str) -> dict:
        try:
            run = store.get_run(run_id)
            run["events"] = store.list_events(run_id)
            return run
        except ReviewStateError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/runs/{run_id}/prepare-approval")
    def prepare_approval(run_id: str, payload: DraftRequest) -> dict[str, str]:
        try:
            prepared = store.prepare_approval(run_id, payload.draft)
            run = store.get_run(run_id)
            if run["repository"]:
                prepared["real_confirmation_phrase"] = (
                    f"PUBLISH {run['repository']}#{run['issue_number']}"
                )
            return prepared
        except ReviewStateError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/runs/{run_id}/confirm")
    def confirm(run_id: str, payload: ConfirmRequest) -> dict:
        try:
            if payload.mode == "github":
                if not _github_write_enabled():
                    raise ReviewStateError("GitHub 真实写入未启用。")
                write_token = os.getenv("GITHUB_WRITE_TOKEN", "")
                if not write_token:
                    raise ReviewStateError("未设置独立的 GITHUB_WRITE_TOKEN。")
                run = store.authorize_real_submission(
                    run_id, payload.approval_token, payload.confirmation_phrase
                )
                publication = GitHubCommentPublisher(
                    run["repository"], write_token
                ).create_issue_comment(run["issue_number"], run["draft"])
                return store.record_real_submission(
                    run_id, payload.approval_token, publication
                )
            return store.confirm(run_id, payload.approval_token)
        except (ReviewStateError, GitHubPublishError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    return app


app = create_app()
