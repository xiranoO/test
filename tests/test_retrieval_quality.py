import unittest
from pathlib import Path

from issue_agent.analyzers import build_reproduction, classify_issue, locate_code, propose_fix
from issue_agent.models import Issue


ROOT = Path(__file__).resolve().parent.parent


FLASK_4455 = Issue(
    number=4455,
    title="Pinning Flask<2 requires pinning ItsDangerous<2.1 and MarkupSafe<2.1",
    body="""Since the update of itsdangerous, Flask 1.1.2 fails to run.
To replicate the bug:
1. install Flask 1.1.2
2. run flask
3. observe ImportError: cannot import name 'json' from 'itsdangerous'
Environment: Python 3.8, Flask 1.1.2""",
)


class RetrievalQualityTests(unittest.TestCase):
    def setUp(self):
        self.classification = classify_issue(FLASK_4455)
        self.files = locate_code(
            FLASK_4455,
            ROOT / "tests" / "fixtures" / "packaging_repo",
            component=self.classification.component,
        )

    def test_dependency_issue_is_classified(self):
        self.assertEqual(self.classification.issue_type, "bug")
        self.assertEqual(self.classification.component, "dependency")

    def test_replicate_phrase_counts_as_reproduction_steps(self):
        plan = build_reproduction(FLASK_4455, self.classification)
        self.assertFalse(any("复现步骤" in question for question in plan.questions))
        self.assertNotIn("软件版本", self.classification.missing_information)

    def test_dependency_manifest_ranks_before_generic_test(self):
        self.assertGreaterEqual(len(self.files), 2)
        self.assertEqual(self.files[0].path, "pyproject.toml")
        self.assertIn("配置文件加权", self.files[0].reason)

    def test_dependency_fix_plan_is_specific(self):
        plan = propose_fix(self.classification, self.files)
        self.assertIn("版本约束", plan.likely_cause)
        self.assertTrue(any("支持版本" in item for item in plan.tests))


if __name__ == "__main__":
    unittest.main()
