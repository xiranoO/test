import unittest
from pathlib import Path

from issue_agent.data import load_issues
from issue_agent.models import WorkflowStatus
from issue_agent.workflow import ApprovalRequiredError, IssueWorkflow


ROOT = Path(__file__).resolve().parent.parent


class IssueWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.issues = load_issues(ROOT / "data" / "demo" / "issues.json")
        self.workflow = IssueWorkflow(ROOT / "demo_repo", self.issues)
        self.result = self.workflow.analyze(self.issues[0])

    def test_analysis_returns_structured_evidence(self):
        self.assertEqual(self.result.classification.issue_type, "bug")
        self.assertEqual(self.result.classification.component, "authentication")
        self.assertEqual(self.result.duplicates[0].issue_number, 42)
        self.assertTrue(any(item.path.endswith("token.py") for item in self.result.related_files))
        self.assertIn("human review", self.result.reply_draft)
        self.assertNotIn('f"', self.result.reply_draft)

    def test_submission_is_blocked_without_approval(self):
        with self.assertRaises(ApprovalRequiredError):
            self.workflow.submit(self.result)

    def test_approved_draft_can_be_submitted(self):
        token = self.workflow.request_approval(self.result)
        self.workflow.approve(self.result, token)
        receipt = self.workflow.submit(self.result)
        self.assertEqual(receipt["mode"], "simulation")
        self.assertEqual(self.result.status, WorkflowStatus.SUBMITTED)

    def test_modified_draft_invalidates_approval_token(self):
        token = self.workflow.request_approval(self.result)
        self.result.reply_draft += " changed"
        with self.assertRaises(ApprovalRequiredError):
            self.workflow.approve(self.result, token)


if __name__ == "__main__":
    unittest.main()
