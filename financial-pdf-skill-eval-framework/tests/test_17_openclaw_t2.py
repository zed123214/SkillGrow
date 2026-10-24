"""T2 local readiness and adapter-contract tests; no OpenClaw turn occurs."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from findoc.adapters.openclaw_runtime import (
    OpenClawAdapterError,
    OpenClawE2EAdapter,
    OpenClawInvocation,
    OpenClawRuntimeCheck,
    discover_openclaw,
)
from findoc.runtime.run_store import sha256_file


class HarnessTransport:
    execution_kind = "test_harness"

    def invoke(self, request: OpenClawInvocation) -> dict:
        artifact = request.run_dir / "documents" / request.document_id / "result.json"
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text("{}\n", encoding="utf-8")
        return {
            "runtime": "openclaw",
            "execution_kind": self.execution_kind,
            "skill_name": request.skill_name,
            "run_id": request.run_id,
            "document_id": request.document_id,
            "input_sha256": request.input_sha256,
            "artifact_registry": [
                {
                    "path": artifact.relative_to(request.run_dir).as_posix(),
                    "sha256": sha256_file(artifact),
                }
            ],
        }


class OpenClawT2Tests(unittest.TestCase):
    def test_discovery_requires_cli_gateway_and_project_skill(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp)
            (workspace / "skills" / "financial-pdf-parse-doubao-eval").mkdir(parents=True)
            (workspace / "skills" / "financial-pdf-parse-doubao-eval" / "SKILL.md").write_text("test", encoding="utf-8")
            with patch("findoc.adapters.openclaw_runtime._find_cli", return_value="openclaw.cmd"), patch(
                "findoc.adapters.openclaw_runtime._read_version", return_value="OpenClaw test"
            ), patch("findoc.adapters.openclaw_runtime._gateway_reachable", return_value=True):
                check = discover_openclaw(workspace=workspace)
        self.assertTrue(check.ready)
        self.assertEqual(check.blockers, ())
        self.assertNotIn("credential", str(check.to_dict()).lower())

    def test_not_ready_never_calls_transport(self) -> None:
        runtime = OpenClawRuntimeCheck(False, None, "127.0.0.1", 18789, False, "skill", False, False, ("blocked",))
        transport = Mock()
        transport.execution_kind = "real_openclaw"
        adapter = OpenClawE2EAdapter(runtime, transport)
        with self.assertRaises(OpenClawAdapterError):
            adapter.invoke(Mock())
        transport.invoke.assert_not_called()

    def test_harness_contract_retains_non_real_execution_kind(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pdf = root / "input.pdf"
            pdf.write_bytes(b"%PDF-1.4\n%%EOF\n")
            run_dir = root / "run"
            run_dir.mkdir()
            runtime = OpenClawRuntimeCheck(True, "OpenClaw test", "127.0.0.1", 18789, True, "financial-pdf-parse-doubao-eval", True, True, ())
            request = OpenClawInvocation(
                input_pdf=pdf,
                run_dir=run_dir,
                run_id="t2-harness",
                document_id="doc_t2",
                input_sha256=sha256_file(pdf),
            )
            result = OpenClawE2EAdapter(runtime, HarnessTransport()).invoke(request)
        self.assertEqual(result["execution_kind"], "test_harness")
        self.assertNotEqual(result["execution_kind"], "real_openclaw")


if __name__ == "__main__":
    unittest.main()
