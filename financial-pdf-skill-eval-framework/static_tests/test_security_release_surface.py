"""Standard-library tests for the T0 release-surface scanner.

Run without PyYAML or pytest:
``python -m unittest static_tests.test_security_release_surface -v``.
"""
from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from framework.sanitization import sanitize_for_public_artifact
from static_tests.checks_security import run


FRAMEWORK_DIRNAME = "financial-pdf-skill-eval-framework"


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class ReleaseSecurityScanTests(unittest.TestCase):
    def test_full_scope_finds_literals_without_exposing_them(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            secret = "sk-" + ("a" * 32)
            private_endpoint = "https://" + "10.0.0.7" + "/api"
            local_path = "C:" + chr(92) + "Users" + chr(92) + "example" + chr(92) + "file.txt"
            _write(root / "skills" / "demo" / "examples" / "bad.md", f"token={secret}\n")
            _write(root / FRAMEWORK_DIRNAME / "reports" / "bad.md", private_endpoint + "\n")
            _write(root / FRAMEWORK_DIRNAME / "dashboard" / "bad.py", "# " + local_path + "\n")
            _write(root / FRAMEWORK_DIRNAME / "data" / "fixture.txt", "fixture\n")
            _write(root / "deliverables" / "note.txt", "delivery\n")
            _write(root / ".hidden", "hidden scope\n")

            report = run(root)
            serialized = json.dumps(report, ensure_ascii=False)

            self.assertFalse(report["passed"])
            self.assertIn("source", report["scope_counts"])
            self.assertIn("reports", report["scope_counts"])
            self.assertIn("dashboard", report["scope_counts"])
            self.assertIn("data", report["scope_counts"])
            self.assertIn("skill_examples", report["scope_counts"])
            self.assertIn("delivery_packages", report["scope_counts"])
            self.assertNotIn(secret, serialized)
            self.assertNotIn(private_endpoint, serialized)
            self.assertNotIn(local_path, serialized)
            for finding in report["findings"]:
                self.assertEqual(
                    set(finding),
                    {"path", "rule_id", "line", "count", "fingerprint", "remediation_status"},
                )

    def test_archive_member_and_pdf_inventory_are_covered(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            secret = "sk-" + ("b" * 32)
            archive = root / "deliverables" / "bundle.zip"
            archive.parent.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(archive, "w") as out:
                out.writestr("nested/config.txt", "token=" + secret)

            pdf = root / FRAMEWORK_DIRNAME / "data" / "samples" / "synthetic.pdf"
            pdf.parent.mkdir(parents=True, exist_ok=True)
            pdf.write_bytes(b"%PDF-1.4\nsynthetic test\n")
            _write(
                root / FRAMEWORK_DIRNAME / "testcases" / "pdf_cases" / "synthetic.yaml",
                "input_pdf: data/samples/synthetic.pdf\ntags:\n- synthetic_pdf\n",
            )

            report = run(root)
            paths = {item["path"] for item in report["findings"]}

            self.assertIn("deliverables/bundle.zip!nested/config.txt", paths)
            self.assertEqual(report["archive_member_count"], 1)
            self.assertEqual(report["pdf_inventory"]["total"], 1)
            self.assertEqual(report["pdf_inventory"]["records"][0]["classification"], "synthetic_declared")

    def test_raw_http_body_pattern_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            raw_body_line = "error_payload = exc." + "read().decode('utf-8')"
            _write(
                root / FRAMEWORK_DIRNAME / "framework" / "unsafe_http.py",
                "try:\n    pass\nexcept HTTPError as exc:\n    " + raw_body_line + "\n",
            )

            report = run(root)
            rules = {item["rule_id"] for item in report["findings"]}

            self.assertIn("SEC_RAW_HTTP_ERROR_BODY", rules)
            self.assertFalse(report["no_raw_http_error_body"])

    def test_public_sanitizer_redacts_common_runtime_diagnostics(self) -> None:
        secret = "sk-" + ("c" * 32)
        private_endpoint = "https://" + "192.168.1.50" + "/inference"
        local_path = "C:" + chr(92) + "Users" + chr(92) + "example" + chr(92) + "report.json"
        value = f"token={secret}; endpoint={private_endpoint}; path={local_path}"

        sanitized = sanitize_for_public_artifact(value)

        self.assertNotIn(secret, sanitized)
        self.assertNotIn(private_endpoint, sanitized)
        self.assertNotIn(local_path, sanitized)
        self.assertIn("[REDACTED_SECRET]", sanitized)
        self.assertIn("[REDACTED_PRIVATE_ENDPOINT]", sanitized)
        self.assertIn("[REDACTED_PATH]", sanitized)


if __name__ == "__main__":
    unittest.main()
