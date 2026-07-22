from __future__ import annotations

import json
from pathlib import Path

from .models import Issue, IssueComment


def load_issues(path: Path) -> list[Issue]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    issues = []
    for item in payload:
        issue_data = dict(item)
        issue_data["comments"] = [
            IssueComment(**comment) for comment in issue_data.get("comments", [])
        ]
        issues.append(Issue(**issue_data))
    return issues
