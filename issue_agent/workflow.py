from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path

from .analyzers import (
    build_reproduction,
    classify_issue,
    draft_reply,
    find_duplicates,
    locate_code,
    propose_fix,
)
from .models import AnalysisResult, Issue, WorkflowStatus
from .providers import AnalysisProvider, AnalysisProviderError


class ApprovalRequiredError(RuntimeError):
    pass


class IssueWorkflow:
    """Runs analysis and enforces explicit approval before simulated submission."""

    def __init__(
        self,
        repo_path: Path,
        issue_history: list[Issue],
        provider: AnalysisProvider | None = None,
    ) -> None:
        self.repo_path = repo_path
        self.issue_history = issue_history
        self.provider = provider

    def analyze(self, issue: Issue) -> AnalysisResult:
        classification = classify_issue(issue)
        duplicates = find_duplicates(issue, self.issue_history)
        files = locate_code(issue, self.repo_path, component=classification.component)
        reproduction = build_reproduction(issue, classification)
        fix_plan = propose_fix(classification, files)
        reply = draft_reply(
            issue,
            classification,
            duplicates,
            reproduction,
            fix_plan,
            related_files=files,
        )
        baseline = AnalysisResult(
            issue=issue,
            classification=classification,
            duplicates=duplicates,
            related_files=files,
            reproduction=reproduction,
            fix_plan=fix_plan,
            reply_draft=reply,
            status=WorkflowStatus.DRAFT_READY,
        )
        if self.provider is None:
            return baseline
        try:
            return self.provider.refine(baseline)
        except AnalysisProviderError as exc:
            return replace(
                baseline,
                analysis_provider="heuristic-fallback",
                provider_warning=str(exc),
            )

    @staticmethod
    def request_approval(result: AnalysisResult) -> str:
        result.status = WorkflowStatus.WAITING_FOR_APPROVAL
        return hashlib.sha256(result.reply_draft.encode("utf-8")).hexdigest()[:12]

    @staticmethod
    def approve(result: AnalysisResult, token: str) -> None:
        expected = hashlib.sha256(result.reply_draft.encode("utf-8")).hexdigest()[:12]
        if result.status != WorkflowStatus.WAITING_FOR_APPROVAL or token != expected:
            raise ApprovalRequiredError("草稿尚未获得有效批准，禁止提交。")
        result.status = WorkflowStatus.APPROVED

    @staticmethod
    def submit(result: AnalysisResult) -> dict[str, object]:
        if result.status != WorkflowStatus.APPROVED:
            raise ApprovalRequiredError("必须先批准草稿，才能执行提交。")
        result.status = WorkflowStatus.SUBMITTED
        return {
            "mode": "simulation",
            "issue_number": result.issue.number,
            "labels": result.classification.suggested_labels,
            "comment": result.reply_draft,
            "status": result.status.value,
        }
