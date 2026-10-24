"""T6 current 20-case synthetic fixture-replay baseline tests."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from findoc.baseline.runner import BaselineError, run_t6_baseline
from findoc.baseline.synthetic import build_synthetic_case, t6_case_specs


class T6BaselineTests(unittest.TestCase):
    def test_generated_input_has_consistent_pdf_xref_offsets(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            generated = build_synthetic_case(Path(temp), t6_case_specs()[0])
            data = generated["pdf_path"].read_bytes()
        self.assertTrue(data.startswith(b"%PDF-1.4"))
        self.assertTrue(data.endswith(b"%%EOF\n"))
        xref_offset = int(data.split(b"startxref\n", 1)[1].splitlines()[0])
        self.assertTrue(data[xref_offset:].startswith(b"xref\n0 6\n"))
        xref_lines = data[xref_offset:].splitlines()[2:8]
        for object_number, line in enumerate(xref_lines[1:], start=1):
            object_offset = int(line[:10])
            self.assertTrue(data[object_offset:].startswith(f"{object_number} 0 obj\n".encode("ascii")))

    def test_twenty_case_baseline_is_hashed_cohorted_and_honest(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "baseline"
            manifest = run_t6_baseline(output, verification_id="T6-test")
            serialized = json.dumps(manifest, ensure_ascii=False)
            self.assertEqual(len(manifest["cases"]), 20)
            self.assertEqual(manifest["summary"]["case_definitions"]["numerator"], 20)
            self.assertEqual(manifest["summary"]["completion"]["numerator"], 19)
            self.assertEqual(manifest["summary"]["gate_decision_distribution"], {"accepted": 15, "needs_review": 4, "not_reached": 1})
            self.assertEqual(manifest["summary"]["cohort_accuracy"]["synthetic_oracle"]["accuracy"], None)
            self.assertEqual(manifest["summary"]["external_calls"], 0)
            self.assertFalse(manifest["summary"]["network_accessed"])
            self.assertEqual(manifest["summary"]["stale_artifact_probes"]["rejected"], 2)
            self.assertEqual(manifest["summary"]["stale_artifact_probes"]["ignored"], 1)
            self.assertTrue(all(case["provenance"]["oracle_frozen_before_fixture"] for case in manifest["cases"]))
            self.assertTrue(all(len(case["pdf_sha256"]) == 64 and len(case["gt_sha256"]) == 64 for case in manifest["cases"]))
            self.assertNotIn(str(Path(temp)), serialized)
            self.assertTrue((output / "baseline_manifest.json").is_file())
            self.assertTrue((output / "baseline_summary.md").is_file())

    def test_nonempty_output_is_never_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "baseline"
            output.mkdir()
            (output / "sentinel.txt").write_text("preserve", encoding="utf-8")
            with self.assertRaises(BaselineError):
                run_t6_baseline(output)
            self.assertEqual((output / "sentinel.txt").read_text(encoding="utf-8"), "preserve")


if __name__ == "__main__":
    unittest.main()
