import unittest

from evaluate import evaluate_cases


class EvaluationTests(unittest.TestCase):
    def test_offline_quality_gate_passes(self):
        report = evaluate_cases()
        self.assertGreaterEqual(report["score"], 0.8)
        self.assertEqual(report["total_assertions"], 10)


if __name__ == "__main__":
    unittest.main()
