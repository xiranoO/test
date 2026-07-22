from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from issue_agent.data import load_issues
from issue_agent.workflow import IssueWorkflow


ROOT = Path(__file__).resolve().parent


def evaluate_cases(
    cases_path: Path = ROOT / "data" / "eval" / "cases.json",
) -> dict[str, Any]:
    cases = json.loads(cases_path.read_text(encoding="utf-8"))
    issues = load_issues(ROOT / "data" / "demo" / "issues.json")
    issue_by_number = {issue.number: issue for issue in issues}
    workflow = IssueWorkflow(ROOT / "demo_repo", issues)
    checks: list[dict[str, Any]] = []

    for case in cases:
        issue_number = int(case["issue_number"])
        issue = issue_by_number[issue_number]
        result = workflow.analyze(issue)
        actual_files = [item.path.replace("\\", "/") for item in result.related_files]
        actual_duplicates = [item.issue_number for item in result.duplicates]
        assertions = {
            "type": result.classification.issue_type == case["expected_type"],
            "component": result.classification.component == case["expected_component"],
        }
        if "expected_duplicate" in case:
            assertions["duplicate"] = case["expected_duplicate"] in actual_duplicates
        if "expected_file_suffix" in case:
            expected = case["expected_file_suffix"].replace("\\", "/")
            assertions["related_file"] = any(path.endswith(expected) for path in actual_files)
        checks.append(
            {
                "issue_number": issue_number,
                "passed": all(assertions.values()),
                "assertions": assertions,
                "actual": {
                    "type": result.classification.issue_type,
                    "component": result.classification.component,
                    "duplicates": actual_duplicates,
                    "related_files": actual_files,
                },
            }
        )

    total_assertions = sum(len(item["assertions"]) for item in checks)
    passed_assertions = sum(
        sum(item["assertions"].values()) for item in checks
    )
    return {
        "score": round(passed_assertions / total_assertions, 4),
        "passed_assertions": passed_assertions,
        "total_assertions": total_assertions,
        "cases": checks,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate deterministic Issue triage quality.")
    parser.add_argument("--cases", type=Path, default=ROOT / "data" / "eval" / "cases.json")
    parser.add_argument("--min-score", type=float, default=0.8)
    args = parser.parse_args()
    report = evaluate_cases(args.cases)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["score"] < args.min_score:
        raise SystemExit(
            f"Evaluation score {report['score']:.2%} is below {args.min_score:.2%}."
        )


if __name__ == "__main__":
    main()
