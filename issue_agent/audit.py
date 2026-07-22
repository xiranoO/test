from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


class ReviewStateError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class ReviewStore:
    """SQLite-backed review state and append-only audit events."""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS review_runs (
                    id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    repository TEXT,
                    issue_number INTEGER NOT NULL,
                    source_json TEXT NOT NULL,
                    analysis_json TEXT NOT NULL,
                    draft TEXT NOT NULL,
                    draft_hash TEXT,
                    status TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS audit_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    action TEXT NOT NULL,
                    details_json TEXT NOT NULL,
                    FOREIGN KEY (run_id) REFERENCES review_runs(id)
                )
                """
            )

    def create_run(
        self,
        repository: str | None,
        issue_number: int,
        source: dict[str, Any],
        analysis: dict[str, Any],
    ) -> dict[str, Any]:
        run_id = uuid.uuid4().hex[:12]
        created_at = _now()
        draft = str(analysis["reply_draft"])
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO review_runs
                (id, created_at, repository, issue_number, source_json,
                 analysis_json, draft, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    created_at,
                    repository,
                    issue_number,
                    json.dumps(source, ensure_ascii=False),
                    json.dumps(analysis, ensure_ascii=False),
                    draft,
                    "waiting_for_approval",
                ),
            )
            self._event(
                connection,
                run_id,
                "analysis_created",
                {"provider": analysis.get("analysis_provider")},
            )
        return self.get_run(run_id)

    def get_run(self, run_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM review_runs WHERE id = ?", (run_id,)
            ).fetchone()
        if row is None:
            raise ReviewStateError("找不到这次分析记录。")
        return {
            "id": row["id"],
            "created_at": row["created_at"],
            "repository": row["repository"],
            "issue_number": row["issue_number"],
            "source": json.loads(row["source_json"]),
            "analysis": json.loads(row["analysis_json"]),
            "draft": row["draft"],
            "status": row["status"],
        }

    def list_runs(
        self, repository: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        if not 1 <= limit <= 100:
            raise ValueError("历史记录数量必须在 1 到 100 之间。")
        query = "SELECT * FROM review_runs"
        parameters: tuple[Any, ...] = ()
        if repository:
            query += " WHERE repository = ?"
            parameters = (repository,)
        query += " ORDER BY created_at DESC LIMIT ?"
        parameters += (limit,)
        with self._connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        runs = []
        for row in rows:
            analysis = json.loads(row["analysis_json"])
            issue = analysis.get("issue", {})
            classification = analysis.get("classification", {})
            runs.append(
                {
                    "id": row["id"],
                    "created_at": row["created_at"],
                    "repository": row["repository"],
                    "issue_number": row["issue_number"],
                    "issue_title": issue.get("title", f"Issue #{row['issue_number']}"),
                    "issue_type": classification.get("issue_type", "unknown"),
                    "priority": classification.get("priority", "unknown"),
                    "analysis_provider": analysis.get("analysis_provider", "unknown"),
                    "status": row["status"],
                }
            )
        return runs

    def prepare_approval(self, run_id: str, draft: str) -> dict[str, str]:
        draft = draft.strip()
        if not draft:
            raise ReviewStateError("回复草稿不能为空。")
        token = hashlib.sha256(draft.encode("utf-8")).hexdigest()[:12]
        with self._connect() as connection:
            row = connection.execute(
                "SELECT status FROM review_runs WHERE id = ?", (run_id,)
            ).fetchone()
            if row is None:
                raise ReviewStateError("找不到这次分析记录。")
            if row["status"] in {"simulated_submitted", "github_submitted"}:
                raise ReviewStateError("这次分析已经完成提交。")
            connection.execute(
                """
                UPDATE review_runs
                SET draft = ?, draft_hash = ?, status = ?
                WHERE id = ?
                """,
                (draft, token, "waiting_for_confirmation", run_id),
            )
            self._event(
                connection,
                run_id,
                "approval_requested",
                {"draft_hash": token, "draft_length": len(draft)},
            )
        return {"status": "waiting_for_confirmation", "approval_token": token}

    def confirm(self, run_id: str, token: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM review_runs WHERE id = ?", (run_id,)
            ).fetchone()
            if row is None:
                raise ReviewStateError("找不到这次分析记录。")
            if row["status"] != "waiting_for_confirmation" or token != row["draft_hash"]:
                raise ReviewStateError("批准校验码无效或草稿尚未进入确认状态。")
            connection.execute(
                "UPDATE review_runs SET status = ? WHERE id = ?",
                ("simulated_submitted", run_id),
            )
            self._event(
                connection, run_id, "simulation_submitted", {"draft_hash": token}
            )
            analysis = json.loads(row["analysis_json"])
            issue_number = row["issue_number"]
            draft = row["draft"]
        return {
            "mode": "simulation",
            "run_id": run_id,
            "issue_number": issue_number,
            "labels": analysis["classification"]["suggested_labels"],
            "comment": draft,
            "status": "simulated_submitted",
        }

    def authorize_real_submission(
        self, run_id: str, token: str, confirmation_phrase: str
    ) -> dict[str, Any]:
        run = self.get_run(run_id)
        repository = run["repository"]
        if not repository:
            raise ReviewStateError("离线分析不能发布到 GitHub。")
        expected_phrase = f"PUBLISH {repository}#{run['issue_number']}"
        with self._connect() as connection:
            row = connection.execute(
                "SELECT draft_hash, status FROM review_runs WHERE id = ?", (run_id,)
            ).fetchone()
        if (
            row is None
            or row["status"] != "waiting_for_confirmation"
            or token != row["draft_hash"]
        ):
            raise ReviewStateError("批准校验码无效或草稿尚未进入确认状态。")
        if confirmation_phrase != expected_phrase:
            raise ReviewStateError(f"真实发布确认短语不匹配，应为：{expected_phrase}")
        return run

    def record_real_submission(
        self, run_id: str, token: str, publication: dict[str, Any]
    ) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM review_runs WHERE id = ?", (run_id,)
            ).fetchone()
            if (
                row is None
                or row["status"] != "waiting_for_confirmation"
                or token != row["draft_hash"]
            ):
                raise ReviewStateError("发布结果无法写入：审批状态已经变化。")
            connection.execute(
                "UPDATE review_runs SET status = ? WHERE id = ?",
                ("github_submitted", run_id),
            )
            self._event(connection, run_id, "github_comment_submitted", publication)
        return {
            "mode": "github",
            "run_id": run_id,
            "issue_number": row["issue_number"],
            "repository": row["repository"],
            "comment": row["draft"],
            "status": "github_submitted",
            **publication,
        }

    def list_events(self, run_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT created_at, action, details_json
                FROM audit_events WHERE run_id = ? ORDER BY id
                """,
                (run_id,),
            ).fetchall()
        return [
            {
                "created_at": row["created_at"],
                "action": row["action"],
                "details": json.loads(row["details_json"]),
            }
            for row in rows
        ]

    @staticmethod
    def _event(
        connection: sqlite3.Connection,
        run_id: str,
        action: str,
        details: dict[str, Any],
    ) -> None:
        connection.execute(
            """
            INSERT INTO audit_events (run_id, created_at, action, details_json)
            VALUES (?, ?, ?, ?)
            """,
            (run_id, _now(), action, json.dumps(details, ensure_ascii=False)),
        )
