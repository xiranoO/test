from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class WorkflowStatus(str, Enum):
    ANALYZING = "analyzing"
    DRAFT_READY = "draft_ready"
    WAITING_FOR_APPROVAL = "waiting_for_approval"
    APPROVED = "approved"
    SUBMITTED = "submitted"


@dataclass(frozen=True)
class IssueComment:
    author: str
    body: str
    created_at: str
    author_association: str = "NONE"


@dataclass(frozen=True)
class Issue:
    number: int
    title: str
    body: str
    labels: list[str] = field(default_factory=list)
    state: str = "open"
    comments: list[IssueComment] = field(default_factory=list)


@dataclass(frozen=True)
class Classification:
    issue_type: str
    priority: str
    component: str
    suggested_labels: list[str]
    confidence: float
    evidence: list[str]
    missing_information: list[str]


@dataclass(frozen=True)
class DuplicateCandidate:
    issue_number: int
    title: str
    similarity: float
    shared_terms: list[str]
    differences: list[str]


@dataclass(frozen=True)
class RelatedFile:
    path: str
    score: float
    reason: str
    matched_lines: list[str]


@dataclass(frozen=True)
class ReproductionPlan:
    confirmed_facts: list[str]
    inferred_steps: list[str]
    questions: list[str]


@dataclass(frozen=True)
class FixPlan:
    likely_cause: str
    changes: list[str]
    tests: list[str]
    risks: list[str]
    confidence: float


@dataclass
class AnalysisResult:
    issue: Issue
    classification: Classification
    duplicates: list[DuplicateCandidate]
    related_files: list[RelatedFile]
    reproduction: ReproductionPlan
    fix_plan: FixPlan
    reply_draft: str
    status: WorkflowStatus = WorkflowStatus.DRAFT_READY
    analysis_provider: str = "heuristic"
    provider_warning: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        return data
