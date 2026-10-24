"""T3 Evidence Pack and structured Judge coverage."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from findoc.guarded_workflow import execute_guarded_fixture_run
from findoc.judge.models import validate_judge_result
from tests.findoc_test_support import InvalidCitationHarnessJudge, MissingConfidenceHarnessJudge, SuccessfulHarnessJudge, make_fixture, make_pdf


class EvidenceJudgeT3Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.pdf = make_pdf(self.root)
        self.runs = self.root / "runs"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _artifacts(self, run_id: str) -> tuple[dict, dict, dict]:
        manifest = json.loads((self.runs / run_id / "manifest.json").read_text(encoding="utf-8"))
        doc = manifest["document_id"]
        directory = self.runs / run_id / "documents" / doc
        return (
            manifest,
            json.loads((directory / "evidence_pack.json").read_text(encoding="utf-8")),
            json.loads((directory / "judge_result.json").read_text(encoding="utf-8")),
        )

    def test_pack_has_content_addressed_entries_and_full_coverage(self) -> None:
        fixture = make_fixture(self.root / "full", self.pdf, include_pages=True)
        result = execute_guarded_fixture_run(input_path=self.pdf, fixture_output_dir=fixture, run_root=self.runs, run_id="t3-pack")
        _manifest, pack, judge = self._artifacts(result.run_id)
        self.assertEqual(pack["coverage"], {"eligible": 3, "evidenced": 3, "missing": 0, "ratio": 1.0, "missing_reasons": []})
        self.assertEqual({entry["type"] for entry in pack["entries"]}, {"page", "table", "metric"})
        self.assertTrue(all(entry["source_kind"] == "sut_derived" for entry in pack["entries"]))
        self.assertEqual(judge["status"], "abstained")
        self.assertEqual(judge["abstain_reason"], "live_judge_not_authorized")
        self.assertEqual(judge["evidence_citations"], [])

    def test_injected_harness_provider_must_cite_pack_evidence(self) -> None:
        fixture = make_fixture(self.root / "judge", self.pdf)
        result = execute_guarded_fixture_run(
            input_path=self.pdf,
            fixture_output_dir=fixture,
            run_root=self.runs,
            run_id="t3-judge",
            judge_provider=SuccessfulHarnessJudge(),
        )
        _manifest, pack, judge = self._artifacts(result.run_id)
        validate_judge_result(judge, evidence_ids={entry["evidence_id"] for entry in pack["entries"]})
        self.assertEqual(judge["status"], "success")
        self.assertEqual(judge["provider"]["name"], "harness")

    def test_invalid_citation_becomes_failed_not_success(self) -> None:
        fixture = make_fixture(self.root / "invalid", self.pdf)
        result = execute_guarded_fixture_run(
            input_path=self.pdf,
            fixture_output_dir=fixture,
            run_root=self.runs,
            run_id="t3-invalid-citation",
            judge_provider=InvalidCitationHarnessJudge(),
        )
        _manifest, _pack, judge = self._artifacts(result.run_id)
        self.assertEqual(judge["status"], "failed")
        self.assertEqual(judge["verdict"], "error")
        self.assertEqual(judge["evidence_citations"], [])

    def test_success_without_confidence_becomes_failed(self) -> None:
        fixture = make_fixture(self.root / "missing-confidence", self.pdf)
        result = execute_guarded_fixture_run(
            input_path=self.pdf,
            fixture_output_dir=fixture,
            run_root=self.runs,
            run_id="t3-missing-confidence",
            judge_provider=MissingConfidenceHarnessJudge(),
        )
        _manifest, _pack, judge = self._artifacts(result.run_id)
        self.assertEqual(judge["status"], "failed")
        self.assertEqual(judge["confidence"], None)

    def test_low_coverage_abstains_before_provider_call(self) -> None:
        fixture = make_fixture(self.root / "low", self.pdf, include_pages=False)
        provider = SuccessfulHarnessJudge()
        result = execute_guarded_fixture_run(
            input_path=self.pdf,
            fixture_output_dir=fixture,
            run_root=self.runs,
            run_id="t3-low-coverage",
            judge_provider=provider,
        )
        _manifest, pack, judge = self._artifacts(result.run_id)
        self.assertLess(pack["coverage"]["ratio"], 0.8)
        self.assertEqual(judge["status"], "abstained")
        self.assertEqual(judge["abstain_reason"], "insufficient_evidence_coverage")


if __name__ == "__main__":
    unittest.main()
