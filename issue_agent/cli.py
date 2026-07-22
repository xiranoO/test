from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import replace
from pathlib import Path

from .data import load_issues
from .github_readonly import GitHubReadOnlyClient, GitHubReadOnlyError, PublicRepositoryCache
from .providers import DeepSeekProvider
from .workflow import IssueWorkflow


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="GitHub Issue 管理 Agent MVP")
    parser.add_argument("--issue", type=int, default=101, help="要分析的 Issue 编号")
    parser.add_argument("--repo", help="真实 GitHub 只读模式，格式为 owner/repo")
    parser.add_argument(
        "--history-limit", type=int, default=100, help="真实模式读取的最近 Issue 数量（1-300）"
    )
    parser.add_argument("--approve", action="store_true", help="仅离线模式：批准并执行模拟提交")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出完整分析结果")
    parser.add_argument(
        "--provider",
        choices=("auto", "heuristic", "deepseek"),
        help="分析器；默认读取 LLM_PROVIDER，未配置时为 auto",
    )
    parser.add_argument("--model", help="LLM 模型；默认读取 LLM_MODEL")
    return parser


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = build_parser().parse_args()
    root = Path(__file__).resolve().parent.parent
    source = {"mode": "offline-demo", "repository": None, "snapshot_reused": None}
    if args.repo:
        if args.approve:
            raise SystemExit("真实 GitHub 模式当前严格只读，不能使用 --approve。")
        try:
            client = GitHubReadOnlyClient(args.repo, token=os.getenv("GITHUB_TOKEN"))
            issue = client.get_issue(args.issue)
            issue = replace(issue, comments=client.list_issue_comments(args.issue))
            issues = client.list_issues(args.history_limit)
            if all(item.number != issue.number for item in issues):
                issues.append(issue)
            snapshot = PublicRepositoryCache(root / ".cache" / "github").ensure(args.repo)
        except (GitHubReadOnlyError, ValueError) as exc:
            raise SystemExit(str(exc)) from exc
        repo_path = snapshot.path
        source = {
            "mode": "github-read-only",
            "repository": client.repository,
            "snapshot_reused": snapshot.reused,
            "rate_remaining": client.rate_remaining,
            "comments_loaded": len(issue.comments),
        }
    else:
        issues = load_issues(root / "data" / "demo" / "issues.json")
        issue = next((item for item in issues if item.number == args.issue), None)
        if issue is None:
            choices = ", ".join(str(item.number) for item in issues)
            raise SystemExit(f"找不到 Issue #{args.issue}，可选编号：{choices}")
        repo_path = root / "demo_repo"

    provider_name = args.provider or os.getenv("LLM_PROVIDER", "auto").lower()
    if provider_name not in {"auto", "heuristic", "deepseek"}:
        raise SystemExit("LLM_PROVIDER 必须是 auto、heuristic 或 deepseek。")
    deepseek_key = os.getenv("DEEPSEEK_API_KEY")
    provider = None
    if provider_name == "deepseek" or (provider_name == "auto" and deepseek_key):
        if not deepseek_key:
            raise SystemExit("选择 DeepSeek 时必须设置 DEEPSEEK_API_KEY。")
        provider = DeepSeekProvider(
            api_key=deepseek_key,
            model=args.model or os.getenv("LLM_MODEL", "deepseek-v4-flash"),
            base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        )

    workflow = IssueWorkflow(repo_path, issues, provider=provider)
    result = workflow.analyze(issue)
    token = workflow.request_approval(result)
    receipt = None
    if args.approve:
        workflow.approve(result, token)
        receipt = workflow.submit(result)

    if args.json:
        payload = {
            "source": source,
            "analysis": result.to_dict(),
            "approval_token": token,
            "submission": receipt,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    print(f"数据源: {source['mode']}" + (f" ({source['repository']})" if source["repository"] else ""))
    print(f"分析器: {result.analysis_provider}")
    if result.provider_warning:
        print(f"模型回退提示: {result.provider_warning}")
    print(f"Issue #{issue.number}: {issue.title}")
    print(f"分类: {result.classification.issue_type} / {result.classification.priority}")
    print(f"模块: {result.classification.component}")
    print("相关文件: " + ", ".join(item.path for item in result.related_files))
    print("\n--- 回复草稿 ---\n")
    print(result.reply_draft)
    if receipt is None:
        print(f"\n状态: {result.status.value}（未提交）")
        if source["mode"] == "github-read-only":
            print(f"真实 GitHub 模式严格只读；草稿校验码: {token}")
        else:
            print(f"如确认草稿，可重新运行并添加 --approve；草稿校验码: {token}")
    else:
        print("\n--- 模拟提交结果 ---")
        print(json.dumps(receipt, ensure_ascii=False, indent=2))
