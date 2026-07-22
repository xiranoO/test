import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from fastapi.testclient import TestClient

from issue_agent.audit import ReviewStateError, ReviewStore
from issue_agent.models import Issue
from issue_agent.web import create_app


ROOT = Path(__file__).resolve().parent.parent


class ReviewStoreTests(unittest.TestCase):
    def test_edited_draft_requires_matching_confirmation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ReviewStore(Path(temp_dir) / "reviews.sqlite3")
            run = store.create_run(
                None,
                101,
                {"mode": "offline-demo"},
                {
                    "reply_draft": "original",
                    "analysis_provider": "heuristic",
                    "classification": {"suggested_labels": ["bug"]},
                },
            )
            prepared = store.prepare_approval(run["id"], "edited")
            with self.assertRaises(ReviewStateError):
                store.confirm(run["id"], "000000000000")
            receipt = store.confirm(run["id"], prepared["approval_token"])
            self.assertEqual(receipt["comment"], "edited")
            self.assertEqual(receipt["mode"], "simulation")
            self.assertEqual(len(store.list_events(run["id"])), 3)
            history = store.list_runs(limit=10)
            self.assertEqual(history[0]["issue_number"], 101)
            self.assertEqual(history[0]["status"], "simulated_submitted")


class WebAppTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        app = create_app(ROOT, Path(self.temp_dir.name) / "reviews.sqlite3")
        self.client = TestClient(app)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_home_and_health_are_available(self):
        page = self.client.get("/")
        self.assertEqual(page.status_code, 200)
        self.assertIn("Issue Lens", page.text)
        self.assertIn('id="phrase-field" hidden', page.text)
        stylesheet = self.client.get("/static/styles.css")
        self.assertEqual(stylesheet.status_code, 200)
        self.assertIn("[hidden]{display:none!important}", stylesheet.text)
        with patch.dict("os.environ", {}, clear=True):
            health = self.client.get("/health").json()
        self.assertEqual(health["status"], "ok")
        self.assertFalse(health["github_write_enabled"])

    def test_offline_analysis_and_double_confirmation(self):
        response = self.client.post(
            "/api/analyze",
            json={"repository": None, "issue_number": 101, "provider": "heuristic"},
        )
        self.assertEqual(response.status_code, 200)
        run = response.json()
        self.assertEqual(run["source"]["mode"], "offline-demo")
        prepared = self.client.post(
            f"/api/runs/{run['id']}/prepare-approval",
            json={"draft": "Reviewed reply"},
        ).json()
        receipt = self.client.post(
            f"/api/runs/{run['id']}/confirm",
            json={"approval_token": prepared["approval_token"]},
        )
        self.assertEqual(receipt.status_code, 200)
        self.assertEqual(receipt.json()["status"], "simulated_submitted")

    @patch("issue_agent.web.GitHubReadOnlyClient")
    def test_repository_issue_list_returns_open_issue_summaries(self, client_class):
        client_class.return_value.repository = "octocat/Hello-World"
        client_class.return_value.rate_remaining = 4999
        client_class.return_value.list_issues.return_value = [
            Issue(8, "Newer bug", "Details", ["bug"], "open"),
            Issue(7, "Open bug", "Details", ["bug"], "open"),
        ]
        response = self.client.post(
            "/api/repository-issues",
            json={"repository": "octocat/Hello-World", "limit": 50},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["issues"][0]["number"], 7)
        self.assertEqual(payload["issues"][1]["number"], 8)
        self.assertEqual(payload["selection_limit"], 10)
        client_class.return_value.list_issues.assert_called_once_with(50, state="open")

    def test_analysis_history_can_be_filtered_by_repository(self):
        store = self.client.app.state.review_store
        store.create_run(
            "octocat/Hello-World",
            7,
            {"mode": "github-read-only"},
            {
                "issue": {"title": "Open bug"},
                "reply_draft": "Reviewed reply",
                "analysis_provider": "heuristic",
                "classification": {
                    "issue_type": "bug",
                    "priority": "high",
                    "suggested_labels": ["bug"],
                },
            },
        )
        response = self.client.get(
            "/api/runs", params={"repository": "octocat/Hello-World"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["runs"][0]["issue_title"], "Open bug")

    def test_real_publish_is_disabled_by_default(self):
        response = self.client.post(
            "/api/analyze",
            json={"repository": None, "issue_number": 101, "provider": "heuristic"},
        )
        run = response.json()
        prepared = self.client.post(
            f"/api/runs/{run['id']}/prepare-approval",
            json={"draft": "Reviewed reply"},
        ).json()
        with patch.dict("os.environ", {}, clear=True):
            blocked = self.client.post(
                f"/api/runs/{run['id']}/confirm",
                json={
                    "approval_token": prepared["approval_token"],
                    "mode": "github",
                    "confirmation_phrase": "PUBLISH anything#101",
                },
            )
        self.assertEqual(blocked.status_code, 409)
        self.assertIn("未启用", blocked.json()["detail"])

    def test_real_authorization_requires_exact_target_phrase(self):
        store = self.client.app.state.review_store
        run = store.create_run(
            "octocat/Hello-World",
            7,
            {"mode": "github-read-only"},
            {
                "reply_draft": "Reviewed reply",
                "analysis_provider": "heuristic",
                "classification": {"suggested_labels": ["bug"]},
            },
        )
        prepared = store.prepare_approval(run["id"], "Reviewed reply")
        with self.assertRaises(ReviewStateError):
            store.authorize_real_submission(
                run["id"], prepared["approval_token"], "PUBLISH octocat/other#7"
            )
        authorized = store.authorize_real_submission(
            run["id"],
            prepared["approval_token"],
            "PUBLISH octocat/Hello-World#7",
        )
        self.assertEqual(authorized["repository"], "octocat/Hello-World")

    @patch("issue_agent.web.GitHubCommentPublisher")
    def test_real_publish_uses_separate_writer_after_all_checks(self, publisher_class):
        publisher_class.return_value.create_issue_comment.return_value = {
            "comment_id": 987,
            "comment_url": "https://github.com/octocat/Hello-World/issues/7#issuecomment-987",
            "created_at": "2026-07-23T12:00:00Z",
        }
        store = self.client.app.state.review_store
        run = store.create_run(
            "octocat/Hello-World",
            7,
            {"mode": "github-read-only"},
            {
                "reply_draft": "Reviewed reply",
                "analysis_provider": "heuristic",
                "classification": {"suggested_labels": ["bug"]},
            },
        )
        prepared = store.prepare_approval(run["id"], "Reviewed reply")
        with patch.dict(
            "os.environ",
            {"ENABLE_GITHUB_WRITE": "true", "GITHUB_WRITE_TOKEN": "write-token"},
            clear=True,
        ):
            response = self.client.post(
                f"/api/runs/{run['id']}/confirm",
                json={
                    "approval_token": prepared["approval_token"],
                    "mode": "github",
                    "confirmation_phrase": "PUBLISH octocat/Hello-World#7",
                },
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "github_submitted")
        publisher_class.assert_called_once_with("octocat/Hello-World", "write-token")
        publisher_class.return_value.create_issue_comment.assert_called_once_with(
            7, "Reviewed reply"
        )


if __name__ == "__main__":
    unittest.main()
