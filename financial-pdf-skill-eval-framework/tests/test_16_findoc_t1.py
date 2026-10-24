"""Dependency-free T1 coverage for the isolated FinDoc fixture workflow."""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from findoc.models.document_ir import DocumentIRValidationError, validate_document_ir
from findoc.workflow import FindocWorkflowError, analyze_document


FRAMEWORK_ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def _make_legacy_standard_fixture(root: Path, *, include_pages_detail: bool = False) -> Path:
    """Create harness-only source artifacts; never derived from the SUT output."""
    fixture = root / "legacy_standard_fixture"
    (fixture / "raw").mkdir(parents=True)
    (fixture / "raw" / "parsed.md").write_text("# Synthetic financial statement\n", encoding="utf-8")
    _write_json(
        fixture / "normalized" / "normalized_tables.json",
        {
            "tables": [
                {
                    "table_id": "synthetic_table_001",
                    "page": 1,
                    "table_type": "financial_statement",
                    "statement": "income_statement",
                    "columns": ["item", "value"],
                    "rows": [
                        {
                            "item": "revenue",
                            "value": "100",
                            "page": 1,
                            "bbox": [10, 20, 30, 40],
                        }
                    ],
                }
            ]
        },
    )
    _write_json(
        fixture / "normalized" / "financial_summary.json",
        {
            "company": "Synthetic Co",
            "document_type": "synthetic_financial_statement",
            "period": "2026Q1",
            "metric_statistics": {"metric_record_count": 1},
            "metrics": [
                {
                    "item": "revenue",
                    "period": "2026Q1",
                    "value": "100",
                    "table_id": "synthetic_table_001",
                    "page_number": 1,
                    "coordinates": [10, 20, 30, 40],
                }
            ],
        },
    )
    _write_json(
        fixture / "evaluation" / "quality_checks.json",
        {"data_authenticity": {"execution_backend": "official_output_mock", "is_synthetic": True}},
    )
    _write_json(
        fixture / "meta" / "run_meta.json",
        {
            "execution_backend": "official_output_mock",
            "output_source": "external_official_output",
            "is_synthetic": True,
            "count_as_real_evaluation": False,
            "page_count": 1,
        },
    )
    if include_pages_detail:
        _write_json(
            fixture / "raw" / "pages_detail.json",
            [
                {
                    "page_number": 1,
                    "bbox": [0, 0, 600, 800],
                    "blocks": [
                        {"type": "text", "bbox": [10, 20, 30, 40]},
                        {"type": "table", "bbox": [50, 60, 70, 80]},
                    ],
                }
            ],
        )
    return fixture


class FindocT1Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.input_pdf = self.root / "synthetic_input.pdf"
        self.input_pdf.write_bytes(b"%PDF-1.4\n% synthetic T1 test input\n")
        self.fixture = _make_legacy_standard_fixture(self.root)
        self.runs = self.root / "runs"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _read_run(self, run_id: str) -> tuple[dict, dict]:
        run_dir = self.runs / run_id
        manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
        document_ir = json.loads((run_dir / "documents" / f"doc_{_sha256(self.input_pdf)[:16]}" / "document_ir.json").read_text(encoding="utf-8"))
        return manifest, document_ir

    def test_cli_fixture_analysis_writes_strict_safe_artifacts(self) -> None:
        source_hashes_before = {
            path.relative_to(self.fixture).as_posix(): _sha256(path)
            for path in self.fixture.rglob("*")
            if path.is_file()
        }
        command = [
            sys.executable,
            "-m",
            "findoc",
            "analyze",
            "--input",
            str(self.input_pdf),
            "--backend",
            "fixture",
            "--fixture-output-dir",
            str(self.fixture),
            "--run-root",
            str(self.runs),
            "--run-id",
            "t1-success",
        ]
        proc = subprocess.run(command, cwd=FRAMEWORK_ROOT, capture_output=True, text=True, encoding="utf-8", check=False)

        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["status"], "completed")
        self.assertEqual(payload["run_id"], "t1-success")
        self.assertEqual(payload["manifest_path"], "manifest.json")

        manifest, document_ir = self._read_run("t1-success")
        validate_document_ir(document_ir)
        self.assertEqual(manifest["schema_version"], "findoc.run_manifest.v1")
        self.assertEqual(manifest["state"], "completed")
        self.assertEqual(manifest["execution_kind"], "fixture_replay")
        self.assertEqual(manifest["input_hash"], _sha256(self.input_pdf))
        self.assertEqual(len(manifest["artifact_registry"]), 1)
        artifact = manifest["artifact_registry"][0]
        self.assertEqual(artifact["path"], payload["document_ir_path"])
        self.assertTrue(artifact["active"])
        self.assertTrue(artifact["current"])
        document_ir_path = self.runs / "t1-success" / payload["document_ir_path"]
        self.assertEqual(artifact["sha256"], _sha256(document_ir_path))
        self.assertNotIn(str(self.root), json.dumps(manifest, ensure_ascii=False))
        self.assertNotIn(str(self.root), json.dumps(document_ir, ensure_ascii=False))
        self.assertEqual(document_ir["execution_kind"], "fixture_replay")
        self.assertEqual(document_ir["evidence_refs"], [])
        self.assertFalse(document_ir["pages"]["available"])
        self.assertFalse(document_ir["blocks"]["available"])
        self.assertEqual(document_ir["page_count"], 1)
        self.assertNotIn("page", document_ir["normalized_tables"][0])
        self.assertNotIn("page", document_ir["normalized_tables"][0]["rows"][0])
        self.assertNotIn("bbox", document_ir["normalized_tables"][0]["rows"][0])
        self.assertEqual(document_ir["financial_metrics"][0]["table_id"], "table_001")
        self.assertNotIn("page_number", document_ir["financial_metrics"][0])
        self.assertNotIn("coordinates", document_ir["financial_metrics"][0])
        self.assertIn("legacy_table_locations_not_provenanced", document_ir["warnings"])

        source_hashes_after = {
            path.relative_to(self.fixture).as_posix(): _sha256(path)
            for path in self.fixture.rglob("*")
            if path.is_file()
        }
        self.assertEqual(source_hashes_before, source_hashes_after)

    def test_page_detail_is_reduced_to_source_indexes_without_coordinates(self) -> None:
        fixture_with_pages = _make_legacy_standard_fixture(self.root / "with_pages", include_pages_detail=True)
        result = analyze_document(
            input_path=self.input_pdf,
            fixture_output_dir=fixture_with_pages,
            run_root=self.runs,
            run_id="t1-pages",
        )

        _manifest, document_ir = self._read_run(result.run_id)
        validate_document_ir(document_ir)
        self.assertEqual(document_ir["page_count"], 1)
        self.assertEqual(
            document_ir["pages"],
            {
                "available": True,
                "count": 1,
                "items": [{"source_page_index": 0, "source_page_number": 1, "block_count": 2}],
            },
        )
        self.assertEqual(
            document_ir["blocks"],
            {
                "available": True,
                "count": 2,
                "items": [
                    {"source_page_index": 0, "source_block_index": 0},
                    {"source_page_index": 0, "source_block_index": 1},
                ],
            },
        )
        self.assertIn("raw/pages_detail.json", document_ir["provenance"]["source_artifact_hashes"])
        self.assertNotIn("bbox", json.dumps(document_ir, ensure_ascii=False))

        document_ir["blocks"]["count"] = 1
        with self.assertRaises(DocumentIRValidationError):
            validate_document_ir(document_ir)

    def test_each_run_is_isolated_and_can_record_parent_lineage(self) -> None:
        first = analyze_document(
            input_path=self.input_pdf,
            fixture_output_dir=self.fixture,
            run_root=self.runs,
            run_id="t1-first",
        )
        retry = analyze_document(
            input_path=self.input_pdf,
            fixture_output_dir=self.fixture,
            run_root=self.runs,
            run_id="t1-retry",
            parent_run_id=first.run_id,
        )

        self.assertNotEqual(first.run_id, retry.run_id)
        retry_manifest, retry_ir = self._read_run(retry.run_id)
        self.assertEqual(retry_manifest["parent_run_id"], first.run_id)
        self.assertEqual(retry_manifest["artifact_registry"][0]["path"], retry.document_ir_path)
        self.assertEqual(retry_ir["run_id"], retry.run_id)

    def test_default_run_ids_are_unique(self) -> None:
        first = analyze_document(
            input_path=self.input_pdf,
            fixture_output_dir=self.fixture,
            run_root=self.runs,
        )
        second = analyze_document(
            input_path=self.input_pdf,
            fixture_output_dir=self.fixture,
            run_root=self.runs,
        )

        self.assertNotEqual(first.run_id, second.run_id)
        self.assertTrue((self.runs / first.run_id / "manifest.json").is_file())
        self.assertTrue((self.runs / second.run_id / "manifest.json").is_file())

    def test_existing_run_id_is_never_overwritten(self) -> None:
        analyze_document(
            input_path=self.input_pdf,
            fixture_output_dir=self.fixture,
            run_root=self.runs,
            run_id="t1-collision",
        )
        sentinel = self.runs / "t1-collision" / "sentinel.txt"
        sentinel.write_text("preserve", encoding="utf-8")

        with self.assertRaises(FindocWorkflowError):
            analyze_document(
                input_path=self.input_pdf,
                fixture_output_dir=self.fixture,
                run_root=self.runs,
                run_id="t1-collision",
            )

        self.assertEqual(sentinel.read_text(encoding="utf-8"), "preserve")
        self.assertEqual(json.loads((self.runs / "t1-collision" / "manifest.json").read_text(encoding="utf-8"))["state"], "completed")

    def test_missing_parent_run_is_rejected_before_creating_a_child(self) -> None:
        with self.assertRaises(FindocWorkflowError):
            analyze_document(
                input_path=self.input_pdf,
                fixture_output_dir=self.fixture,
                run_root=self.runs,
                run_id="t1-orphan",
                parent_run_id="missing-parent",
            )

        self.assertFalse((self.runs / "t1-orphan").exists())

    def test_non_directory_run_root_is_safely_rejected(self) -> None:
        file_root = self.root / "run-root-file"
        file_root.write_text("not a directory", encoding="utf-8")

        with self.assertRaises(FindocWorkflowError) as caught:
            analyze_document(
                input_path=self.input_pdf,
                fixture_output_dir=self.fixture,
                run_root=file_root,
                run_id="t1-bad-root",
            )

        self.assertNotIn(str(self.root), str(caught.exception))

    def test_unreadable_input_is_safely_rejected_before_run_creation(self) -> None:
        with patch("findoc.workflow.sha256_file", side_effect=OSError(f"cannot read {self.input_pdf}")):
            with self.assertRaises(FindocWorkflowError) as caught:
                analyze_document(
                    input_path=self.input_pdf,
                    fixture_output_dir=self.fixture,
                    run_root=self.runs,
                    run_id="t1-unreadable",
                )

        self.assertEqual(str(caught.exception), "input PDF cannot be read")
        self.assertFalse((self.runs / "t1-unreadable").exists())

    def test_adapter_failure_records_safe_failed_manifest(self) -> None:
        missing_fixture = self.root / "missing_fixture"
        with self.assertRaises(FindocWorkflowError) as caught:
            analyze_document(
                input_path=self.input_pdf,
                fixture_output_dir=missing_fixture,
                run_root=self.runs,
                run_id="t1-failed",
            )

        manifest = json.loads((self.runs / "t1-failed" / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["state"], "failed")
        self.assertEqual(manifest["stage_attempts"][0]["status"], "failed")
        self.assertTrue(manifest["sanitized_errors"])
        self.assertNotIn(str(self.root), json.dumps(manifest, ensure_ascii=False))
        self.assertNotIn(str(self.root), str(caught.exception))
        self.assertFalse((self.runs / "t1-failed" / "documents").exists())

    def test_document_ir_validator_rejects_schema_drift(self) -> None:
        result = analyze_document(
            input_path=self.input_pdf,
            fixture_output_dir=self.fixture,
            run_root=self.runs,
            run_id="t1-schema",
        )
        _manifest, document_ir = self._read_run(result.run_id)
        document_ir["unknown_field"] = True

        with self.assertRaises(DocumentIRValidationError):
            validate_document_ir(document_ir)

    def test_document_ir_validator_rejects_mismatched_input_identifier(self) -> None:
        result = analyze_document(
            input_path=self.input_pdf,
            fixture_output_dir=self.fixture,
            run_root=self.runs,
            run_id="t1-input-id",
        )
        _manifest, document_ir = self._read_run(result.run_id)
        document_ir["input"]["identifier"] = "sha256:" + ("0" * 16)

        with self.assertRaises(DocumentIRValidationError):
            validate_document_ir(document_ir)

    def test_cli_help_and_unsupported_backend_are_local_only(self) -> None:
        help_proc = subprocess.run(
            [sys.executable, "-m", "findoc", "analyze", "--help"],
            cwd=FRAMEWORK_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        self.assertEqual(help_proc.returncode, 0)
        self.assertIn("fixture-output-dir", help_proc.stdout)

        rejected = subprocess.run(
            [
                sys.executable,
                "-m",
                "findoc",
                "analyze",
                "--input",
                str(self.input_pdf),
                "--backend",
                "real_las",
                "--fixture-output-dir",
                str(self.fixture),
                "--run-root",
                str(self.runs),
            ],
            cwd=FRAMEWORK_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        self.assertEqual(rejected.returncode, 2)
        self.assertFalse(self.runs.exists())


if __name__ == "__main__":
    unittest.main()
