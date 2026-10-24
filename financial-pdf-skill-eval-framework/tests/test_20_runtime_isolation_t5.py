"""T5 state, attempt, manifest-only reader, and stale rejection tests."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from findoc.guarded_workflow import execute_guarded_fixture_run
from findoc.reporting.manifest_report import build_manifest_bundle
from findoc.runtime.manifest_runtime import ManifestRuntime, ManifestRuntimeError
from findoc.runtime.run_store import RunStore, sha256_file
from findoc.runtime.state_machine import InvalidStateTransition
from tests.findoc_test_support import make_fixture, make_pdf


class RuntimeIsolationT5Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.pdf = make_pdf(self.root)
        self.fixture = make_fixture(self.root / "fixture-root", self.pdf)
        self.runs = self.root / "runs"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _run(self, run_id: str = "t5-run") -> tuple[dict, ManifestRuntime]:
        result = execute_guarded_fixture_run(input_path=self.pdf, fixture_output_dir=self.fixture, run_root=self.runs, run_id=run_id)
        manifest = json.loads((self.runs / run_id / "manifest.json").read_text(encoding="utf-8"))
        runtime = ManifestRuntime(RunStore(run_id, self.runs / run_id), manifest)
        return result.to_dict(), runtime

    def test_full_state_history_is_explicit_and_terminal(self) -> None:
        _result, runtime = self._run()
        self.assertEqual(
            [item["state"] for item in runtime.manifest["state_history"]],
            ["created", "running", "parsed", "evidence_ready", "judged", "gated", "completed"],
        )
        with self.assertRaises(InvalidStateTransition):
            runtime.transition("running")

    def test_shared_run_root_isolates_runs_and_refuses_collision(self) -> None:
        first = execute_guarded_fixture_run(input_path=self.pdf, fixture_output_dir=self.fixture, run_root=self.runs, run_id="t5-first")
        second = execute_guarded_fixture_run(input_path=self.pdf, fixture_output_dir=self.fixture, run_root=self.runs, run_id="t5-second", parent_run_id=first.run_id)
        self.assertNotEqual(first.run_id, second.run_id)
        with self.assertRaises(Exception):
            execute_guarded_fixture_run(input_path=self.pdf, fixture_output_dir=self.fixture, run_root=self.runs, run_id="t5-first")
        self.assertTrue((self.runs / "t5-first" / "manifest.json").is_file())
        self.assertTrue((self.runs / "t5-second" / "manifest.json").is_file())

    def test_manifest_reader_ignores_unregistered_old_judge_file(self) -> None:
        result, runtime = self._run("t5-ignore")
        rogue = runtime.store.run_dir / "documents" / result["document_id"] / "old_judge_result.json"
        rogue.write_text('{"status":"success","verdict":"pass"}\n', encoding="utf-8")
        bundle = build_manifest_bundle(runtime, document_id=result["document_id"])
        self.assertEqual(bundle["judge_result"]["status"], "abstained")

    def test_tampered_current_artifact_is_rejected_by_hash(self) -> None:
        result, runtime = self._run("t5-tamper")
        entry = runtime.current_entry("judge_result", document_id=result["document_id"])
        candidate = runtime.store.run_dir / entry["path"]
        candidate.write_text("{}\n", encoding="utf-8")
        with self.assertRaises(ManifestRuntimeError):
            build_manifest_bundle(runtime, document_id=result["document_id"])

    def test_hash_matching_but_config_stale_artifact_is_rejected(self) -> None:
        result, runtime = self._run("t5-config")
        entry = runtime.current_entry("judge_result", document_id=result["document_id"])
        candidate = runtime.store.run_dir / entry["path"]
        value = json.loads(candidate.read_text(encoding="utf-8"))
        value["config_hash"] = "0" * 64
        candidate.write_text(json.dumps(value), encoding="utf-8")
        entry["sha256"] = sha256_file(candidate)
        runtime.save()
        with self.assertRaises(ManifestRuntimeError):
            build_manifest_bundle(runtime, document_id=result["document_id"])

    def test_retry_attempts_append_without_overwriting_prior_attempt(self) -> None:
        result, runtime = self._run("t5-attempts")
        first = runtime.start_attempt("report_refresh")
        runtime.finish_attempt("report_refresh", first, status="failed", sanitized_error="safe failure")
        second = runtime.start_attempt("report_refresh")
        runtime.finish_attempt("report_refresh", second, status="completed")
        attempts = [item for item in runtime.manifest["stage_attempts"] if item["stage"] == "report_refresh"]
        self.assertEqual([item["attempt"] for item in attempts], [1, 2])
        self.assertEqual([item["status"] for item in attempts], ["failed", "completed"])

    def test_failed_new_registration_preserves_prior_current_artifact(self) -> None:
        result, runtime = self._run("t5-register-atomic")
        prior = runtime.current_entry("judge_result", document_id=result["document_id"])
        with self.assertRaises(Exception):
            runtime.register_current(
                runtime.store.run_dir / "missing.json",
                artifact_type="judge_result",
                producer="test",
                schema_version=prior["schema_version"],
                document_id=result["document_id"],
            )
        self.assertIs(runtime.current_entry("judge_result", document_id=result["document_id"]), prior)

    def test_manifest_run_id_must_match_run_directory(self) -> None:
        result, runtime = self._run("t5-manifest-binding")
        manifest = dict(runtime.manifest)
        manifest["run_id"] = "different-run"
        with self.assertRaises(ManifestRuntimeError):
            ManifestRuntime(RunStore(result["run_id"], runtime.store.run_dir), manifest)

    def test_hash_matching_but_malformed_review_request_is_rejected(self) -> None:
        review_fixture = make_fixture(self.root / "review-fixture", self.pdf, include_pages=False)
        result = execute_guarded_fixture_run(
            input_path=self.pdf,
            fixture_output_dir=review_fixture,
            run_root=self.runs,
            run_id="t5-review-schema",
        )
        manifest = json.loads((self.runs / result.run_id / "manifest.json").read_text(encoding="utf-8"))
        runtime = ManifestRuntime(RunStore(result.run_id, self.runs / result.run_id), manifest)
        entry = runtime.current_entry("human_review_request", document_id=result.document_id)
        candidate = runtime.store.run_dir / entry["path"]
        value = json.loads(candidate.read_text(encoding="utf-8"))
        value.pop("required_action")
        candidate.write_text(json.dumps(value), encoding="utf-8")
        entry["sha256"] = sha256_file(candidate)
        runtime.save()
        with self.assertRaises(Exception):
            build_manifest_bundle(runtime, document_id=result.document_id)

    def test_resolved_policy_is_part_of_config_hash(self) -> None:
        default_result = execute_guarded_fixture_run(
            input_path=self.pdf,
            fixture_output_dir=self.fixture,
            run_root=self.runs,
            run_id="t5-policy-default",
        )
        changed_result = execute_guarded_fixture_run(
            input_path=self.pdf,
            fixture_output_dir=self.fixture,
            run_root=self.runs,
            run_id="t5-policy-changed",
            policy={"policy_version": "test.changed", "minimum_evidence_coverage": 0.8},
        )
        default_manifest = json.loads((self.runs / default_result.run_id / "manifest.json").read_text(encoding="utf-8"))
        changed_manifest = json.loads((self.runs / changed_result.run_id / "manifest.json").read_text(encoding="utf-8"))
        self.assertIsInstance(default_manifest["config"]["quality_policy"], dict)
        self.assertEqual(default_manifest["config"]["quality_policy"]["policy_version"], "2026-07-30.v1")
        self.assertNotEqual(default_manifest["config_hash"], changed_manifest["config_hash"])


if __name__ == "__main__":
    unittest.main()
