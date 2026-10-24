"""T4 deterministic precedence, routing, and review-artifact tests."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from findoc.guarded_workflow import execute_guarded_fixture_run
from findoc.quality.gate import validate_human_review_request
from tests.findoc_test_support import InvalidCitationHarnessJudge, SuccessfulHarnessJudge, make_fixture, make_pdf


class QualityGateT4Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.pdf = make_pdf(self.root)
        self.runs = self.root / "runs"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _read(self, run_id: str, name: str) -> dict:
        manifest = json.loads((self.runs / run_id / "manifest.json").read_text(encoding="utf-8"))
        return json.loads((self.runs / run_id / "documents" / manifest["document_id"] / name).read_text(encoding="utf-8"))

    def test_deterministic_pass_can_accept_when_optional_judge_abstains(self) -> None:
        fixture = make_fixture(self.root / "accepted", self.pdf, include_pages=True)
        result = execute_guarded_fixture_run(input_path=self.pdf, fixture_output_dir=fixture, run_root=self.runs, run_id="t4-accepted")
        decision = self._read(result.run_id, "quality_decision.json")
        self.assertEqual((result.state, decision["decision"], decision["route"]), ("completed", "accepted", "complete"))
        self.assertIn("judge_not_used_for_acceptance", decision["reasons"])

    def test_low_evidence_routes_to_local_human_review_artifact(self) -> None:
        fixture = make_fixture(self.root / "review", self.pdf, include_pages=False)
        result = execute_guarded_fixture_run(input_path=self.pdf, fixture_output_dir=fixture, run_root=self.runs, run_id="t4-review")
        decision = self._read(result.run_id, "quality_decision.json")
        review = self._read(result.run_id, "human_review_request.json")
        self.assertEqual((result.state, decision["decision"], decision["route"]), ("needs_review", "needs_review", "human_review"))
        self.assertEqual(review["originating_run_id"], result.run_id)
        self.assertEqual(review["status"], "open")
        validate_human_review_request(review, decision=decision)
        self.assertNotIn("ticket", json.dumps(review).lower())

    def test_unknown_input_binding_never_passes(self) -> None:
        fixture = make_fixture(self.root / "unknown", self.pdf, input_binding="unknown")
        result = execute_guarded_fixture_run(input_path=self.pdf, fixture_output_dir=fixture, run_root=self.runs, run_id="t4-unknown")
        decision = self._read(result.run_id, "quality_decision.json")
        self.assertEqual(decision["critical_checks"]["input_hash_binding"], "unknown")
        self.assertEqual(decision["decision"], "needs_review")

    def test_deterministic_hash_mismatch_rejects_even_with_good_judge(self) -> None:
        fixture = make_fixture(self.root / "mismatch", self.pdf, input_binding="mismatch")
        result = execute_guarded_fixture_run(
            input_path=self.pdf,
            fixture_output_dir=fixture,
            run_root=self.runs,
            run_id="t4-rejected",
            judge_provider=SuccessfulHarnessJudge(),
        )
        decision = self._read(result.run_id, "quality_decision.json")
        self.assertEqual(result.state, "failed")
        self.assertEqual(decision["decision"], "rejected")
        self.assertEqual(decision["critical_checks"]["input_hash_binding"], "fail")

    def test_eligible_gt_below_threshold_precedes_good_judge(self) -> None:
        fixture = make_fixture(self.root / "gt", self.pdf)
        result = execute_guarded_fixture_run(
            input_path=self.pdf,
            fixture_output_dir=fixture,
            run_root=self.runs,
            run_id="t4-gt-rejected",
            judge_provider=SuccessfulHarnessJudge(),
            gt_assessment={"eligible": True, "score": 0.5, "source_kind": "synthetic_oracle", "reason": "independent oracle comparison"},
        )
        decision = self._read(result.run_id, "quality_decision.json")
        self.assertEqual(decision["decision"], "rejected")
        self.assertIn("eligible_ground_truth_below_threshold", decision["reasons"])

    def test_optional_judge_failure_marks_degraded(self) -> None:
        fixture = make_fixture(self.root / "degraded", self.pdf)
        result = execute_guarded_fixture_run(
            input_path=self.pdf,
            fixture_output_dir=fixture,
            run_root=self.runs,
            run_id="t4-degraded",
            judge_provider=InvalidCitationHarnessJudge(),
        )
        decision = self._read(result.run_id, "quality_decision.json")
        self.assertEqual((result.state, decision["decision"]), ("degraded", "degraded"))


if __name__ == "__main__":
    unittest.main()
