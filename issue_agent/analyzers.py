from __future__ import annotations

import os
import re
from pathlib import Path

from .models import (
    Classification,
    DuplicateCandidate,
    FixPlan,
    Issue,
    RelatedFile,
    ReproductionPlan,
)


STOP_WORDS = {
    "a", "about", "after", "again", "all", "also", "an", "and", "any", "are", "as",
    "at", "automatically", "be", "because", "before", "but", "by", "can", "could",
    "error", "fails", "fix", "for", "from", "has", "have", "https", "in", "is", "it",
    "issue", "not", "of", "on", "or", "possible", "should", "than", "that", "the",
    "this", "to", "using", "user", "when", "which", "will", "with", "would", "you",
}

LOW_SIGNAL_TERMS = {
    "app", "application", "cannot", "code", "environment", "expected", "file", "flask",
    "import", "install", "json", "latest", "module", "name", "observe", "package",
        "deprecated", "project", "python", "requires", "run", "running", "since", "test",
        "tests", "update", "version",
}

SOURCE_EXTENSIONS = {".py", ".js", ".ts", ".tsx", ".java", ".go", ".rs", ".rb"}
CONFIG_EXTENSIONS = {".toml", ".cfg", ".ini", ".yaml", ".yml", ".json", ".lock"}
TEXT_FILENAMES = {
    "requirements.txt", "requirements-dev.txt", "constraints.txt", "pipfile",
    "dockerfile", "makefile",
}
DEPENDENCY_FILENAMES = {
    "pyproject.toml", "setup.py", "setup.cfg", "requirements.txt", "requirements-dev.txt",
    "constraints.txt", "uv.lock", "poetry.lock", "pdm.lock", "pipfile", "pipfile.lock",
}


def tokenize(text: str) -> set[str]:
    words = re.findall(r"[a-zA-Z][a-zA-Z0-9_./-]{2,}|[\u4e00-\u9fff]{2,}", text.lower())
    return {word.strip("./-") for word in words if word not in STOP_WORDS}


def classify_issue(issue: Issue) -> Classification:
    text = f"{issue.title}\n{issue.body}".lower()
    rules = {
        "bug": ("error", "exception", "crash", "fail", "broken", "500", "错误", "崩溃", "失败"),
        "feature": ("feature", "request", "support", "add", "希望", "新增", "建议"),
        "documentation": ("docs", "documentation", "readme", "typo", "文档", "说明"),
        "question": ("how", "why", "question", "如何", "为什么", "请问"),
    }
    counts = {kind: sum(term in text for term in terms) for kind, terms in rules.items()}
    issue_type = max(counts, key=counts.get) if max(counts.values()) else "question"

    if any(term in text for term in ("data loss", "security", "all users", "无法启动", "数据丢失")):
        priority = "critical"
    elif any(term in text for term in ("500", "crash", "blocked", "cannot", "无法", "崩溃")):
        priority = "high"
    elif issue_type == "bug":
        priority = "medium"
    else:
        priority = "low"

    component_rules = {
        "authentication": ("token", "login", "auth", "jwt", "登录", "认证"),
        "api": ("api", "endpoint", "request", "response", "接口"),
        "database": ("database", "sql", "migration", "数据库"),
        "ui": ("button", "page", "screen", "frontend", "页面", "按钮"),
        "dependency": (
            "dependency", "dependencies", "requirement", "requirements", "package",
            "version conflict", "pinning", "pinned", "pip", "pyproject", "setup.py",
            "importerror", "依赖", "版本冲突",
        ),
    }
    component_scores = {
        name: sum(term in text for term in terms) 
        for name, terms in component_rules.items()
    }
    component = max(component_scores, key=component_scores.get)
    if component_scores[component] == 0:
        component = "unknown"

    missing = []
    version_pattern = (
        r"(?:version|版本)\s*[:：]?\s*[\w.-]+"
        r"|\b[a-z][\w.-]*\s*(?:==|~=|>=|<=|>|<)\s*v?\d+(?:\.\d+)*"
        r"|\b(?:python|flask|django|node(?:\.js)?)\s+v?\d+(?:\.\d+)+"
    )
    if not re.search(version_pattern, text):
        missing.append("软件版本")
    if not any(term in text for term in ("windows", "linux", "macos", "environment", "环境")):
        missing.append("运行环境")
    if not any(
        term in text
        for term in ("steps", "reproduce", "reproduction", "replicate", "replication", "复现", "步骤")
    ):
        missing.append("完整复现步骤")

    evidence = [f"检测到 {counts[issue_type]} 个 {issue_type} 类信号"]
    if component != "unknown":
        evidence.append(f"内容与 {component} 模块关键词匹配")
    confidence = min(0.95, 0.55 + counts[issue_type] * 0.1 + (component != "unknown") * 0.08)
    return Classification(
        issue_type=issue_type,
        priority=priority,
        component=component,
        suggested_labels=[issue_type, f"priority:{priority}", f"component:{component}"],
        confidence=round(confidence, 2),
        evidence=evidence,
        missing_information=missing,
    )


def find_duplicates(issue: Issue, history: list[Issue], limit: int = 5) -> list[DuplicateCandidate]:
    title_terms = tokenize(issue.title)
    all_terms = tokenize(f"{issue.title} {issue.body}")
    candidates: list[DuplicateCandidate] = []
    for other in history:
        if other.number == issue.number:
            continue
        other_title = tokenize(other.title)
        other_all = tokenize(f"{other.title} {other.body}")
        title_union = title_terms | other_title
        body_union = all_terms | other_all
        title_score = len(title_terms & other_title) / max(1, len(title_union))
        body_score = len(all_terms & other_all) / max(1, len(body_union))
        score = 0.65 * title_score + 0.35 * body_score
        if score < 0.15:
            continue
        shared = sorted(all_terms & other_all)[:8]
        candidates.append(
            DuplicateCandidate(
                issue_number=other.number,
                title=other.title,
                similarity=round(score, 3),
                shared_terms=shared,
                differences=sorted((all_terms ^ other_all))[:5],
            )
        )
    return sorted(candidates, key=lambda item: item.similarity, reverse=True)[:limit]


def _is_searchable_file(path: Path) -> bool:
    name = path.name.lower()
    return (
        path.suffix.lower() in SOURCE_EXTENSIONS | CONFIG_EXTENSIONS
        or name in TEXT_FILENAMES
        or name.startswith("requirements") and name.endswith(".txt")
    )


def _term_weight(term: str, project_name: str) -> float:
    if term == project_name or term in LOW_SIGNAL_TERMS:
        return 0.03
    if any(char in term for char in ("_", "/", ".")) or any(char.isdigit() for char in term):
        return 0.2
    if len(term) >= 8:
        return 0.16
    return 0.1


def _dependency_file_bonus(path: Path, component: str) -> float:
    if component != "dependency":
        return 0.0
    name = path.name.lower()
    if name in DEPENDENCY_FILENAMES or name.startswith("requirements"):
        return 0.48
    if path.suffix.lower() in {".toml", ".cfg", ".lock"}:
        return 0.28
    return 0.0


def _weighted_term_score(terms: set[str], project_name: str) -> float:
    weights = [_term_weight(term, project_name) for term in terms]
    specific = sum(weight for weight in weights if weight >= 0.1)
    low_signal = min(0.09, sum(weight for weight in weights if weight < 0.1))
    return specific + low_signal


def locate_code(
    issue: Issue,
    repo_path: Path,
    limit: int = 5,
    component: str | None = None,
) -> list[RelatedFile]:
    terms = tokenize(f"{issue.title} {issue.body}")
    ignored = {".git", ".venv", "node_modules", "__pycache__"}
    component = component or classify_issue(issue).component
    project_name = repo_path.name.lower()
    matches: list[RelatedFile] = []
    for current_root, directories, filenames in os.walk(repo_path):
        directories[:] = [name for name in directories if name not in ignored]
        for filename in filenames:
            path = Path(current_root) / filename
            try:
                if not _is_searchable_file(path) or path.stat().st_size > 1_000_000:
                    continue
                lines = path.read_text(encoding="utf-8").splitlines()
            except (OSError, UnicodeDecodeError):
                continue

            relative = path.relative_to(repo_path).as_posix()
            name_hits = terms & tokenize(relative)
            line_candidates: list[tuple[float, int, str]] = []
            content_hits: set[str] = set()
            for number, line in enumerate(lines, start=1):
                hits = terms & tokenize(line)
                if hits:
                    content_hits.update(hits)
                    line_candidates.append(
                        (_weighted_term_score(hits, project_name), number, line.strip()[:120])
                    )

            structural_bonus = _dependency_file_bonus(path, component)
            if not content_hits and not name_hits:
                continue
            content_score = _weighted_term_score(content_hits, project_name)
            name_score = 1.5 * _weighted_term_score(name_hits, project_name)
            score = min(0.99, content_score + name_score + structural_bonus)
            hit_lines = [
                f"L{number}: {line}"
                for _, number, line in sorted(line_candidates, reverse=True)[:3]
            ]
            reason_terms = sorted(
                name_hits | content_hits,
                key=lambda term: _term_weight(term, project_name),
                reverse=True,
            )[:6]
            reason_parts = [f"匹配 Issue 术语：{', '.join(reason_terms)}"]
            if structural_bonus:
                reason_parts.append("依赖/构建配置文件加权")
            matches.append(
                RelatedFile(
                    path=relative,
                    score=round(score, 2),
                    reason="；".join(reason_parts),
                    matched_lines=hit_lines,
                )
            )
    return sorted(matches, key=lambda item: item.score, reverse=True)[:limit]


def build_reproduction(issue: Issue, classification: Classification) -> ReproductionPlan:
    facts = [line.strip(" -*") for line in issue.body.splitlines() if line.strip()][:4]
    component = classification.component
    steps = [
        "在隔离的测试环境中使用 Issue 描述的条件准备输入数据。",
        f"执行与 {component} 模块相关的操作，并记录请求、响应和日志。",
        "对比实际结果与 Issue 中描述的预期结果。",
    ]
    questions = [f"请补充{item}。" for item in classification.missing_information]
    return ReproductionPlan(confirmed_facts=facts, inferred_steps=steps, questions=questions)


def propose_fix(classification: Classification, files: list[RelatedFile]) -> FixPlan:
    primary = files[0].path if files else "尚未定位到具体文件"
    if classification.component == "dependency":
        return FixPlan(
            likely_cause=f"依赖版本约束可能允许了不兼容组合；首要检查 {primary}。",
            changes=[
                f"核对 {primary} 中直接依赖和传递依赖的版本上下界。",
                "选择更新最低兼容版本或增加临时上界，并在发布说明中记录兼容范围。",
            ],
            tests=[
                "使用报告中的依赖版本组合添加安装与启动回归测试。",
                "分别验证最低支持版本和最新支持版本的依赖组合。",
            ],
            risks=["收紧依赖范围可能影响已有环境，需要验证支持矩阵并避免永久过度锁定。"],
            confidence=0.78 if files else 0.45,
        )
    likely_cause = f"问题可能位于 {classification.component} 模块；首要检查 {primary}。"
    changes = [
        f"核对 {primary} 中异常输入和边界条件的处理。",
        "在保持现有接口兼容的前提下增加明确的错误处理。",
    ]
    tests = ["添加一个能稳定复现该 Issue 的回归测试。", "补充正常路径和边界输入测试。"]
    risks = ["当前结论基于静态关键词检索，修改前需要执行代码并验证调用链。"]
    confidence = 0.72 if files else 0.4
    return FixPlan(likely_cause, changes, tests, risks, confidence)


def draft_reply(
    issue: Issue,
    classification: Classification,
    duplicates: list[DuplicateCandidate],
    reproduction: ReproductionPlan,
    fix_plan: FixPlan,
    related_files: list[RelatedFile] | None = None,
) -> str:
    issue_text = f"{issue.title}\n{issue.body}"
    chinese_count = len(re.findall(r"[\u4e00-\u9fff]", issue_text))
    latin_count = len(re.findall(r"[A-Za-z]", issue_text))
    use_chinese = chinese_count >= 10 and chinese_count >= latin_count // 4

    if not use_chinese:
        duplicate_text = "No high-confidence duplicate Issue was found."
        if duplicates and duplicates[0].similarity >= 0.25:
            duplicate_text = (
                f"Issue #{duplicates[0].issue_number} may be related "
                f"({duplicates[0].similarity:.0%} similarity); a maintainer should confirm."
            )
        missing_labels = {
            "软件版本": "software version",
            "运行环境": "runtime environment",
            "完整复现步骤": "complete reproduction steps",
        }
        questions = "\n".join(
            f"- Please provide the {missing_labels.get(item, item)}."
            for item in classification.missing_information
        ) or "- None at this time."
        primary = related_files[0].path if related_files else "the highest-ranked related file"
        if classification.component == "dependency":
            hypothesis = (
                "The dependency constraints may allow an incompatible version combination; "
                f"inspect {primary} and verify it against the reported release."
            )
        else:
            hypothesis = (
                f"The issue may involve the {classification.component} component; inspect "
                f"{primary} and validate its behavior against the report."
            )
        return (
            f"Thank you for reporting Issue #{issue.number}.\n\n"
            f"Our initial triage classifies this as `{classification.issue_type}` with "
            f"`{classification.priority}` priority, likely involving the "
            f"`{classification.component}` component.\n\n"
            f"{duplicate_text}\n\n"
            f"To continue reproducing the issue, please clarify:\n{questions}\n\n"
            f"Initial investigation: {hypothesis}\n\n"
            "> This reply was drafted by the Issue analysis agent and requires human review."
        )

    duplicate_text = "未发现高置信度的重复 Issue。"
    if duplicates and duplicates[0].similarity >= 0.25:
        duplicate_text = (
            f"发现可能相关的 Issue #{duplicates[0].issue_number} "
            f"（相似度 {duplicates[0].similarity:.0%}），需要维护者确认是否重复。"
        )
    questions = "\n".join(f"- {question}" for question in reproduction.questions) or "- 暂无"
    return (
        f"感谢提交 Issue #{issue.number}。\n\n"
        f"我们初步将其归类为 `{classification.issue_type}`，"
        f"优先级为 `{classification.priority}`，可能涉及 `{classification.component}` 模块。\n\n"
        f"{duplicate_text}\n\n"
        f"为了进一步复现，请协助确认：\n{questions}\n\n"
        f"初步排查结论：{fix_plan.likely_cause}\n\n"
        "> 此回复由 Issue 分析 Agent 草拟，提交前需人工审核。"
    )
