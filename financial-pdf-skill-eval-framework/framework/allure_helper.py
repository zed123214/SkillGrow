"""Optional allure attachment helper. No-op if allure-pytest is unavailable."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

try:
    import allure  # type: ignore
    HAS_ALLURE = True
except Exception:
    HAS_ALLURE = False

STANDARD_ATTACHMENTS = [
    "raw/parsed.md",
    "normalized/normalized_tables.json",
    "normalized/financial_summary.json",
    "evaluation/quality_checks.json",
    "evaluation/evaluation_report.md",
    "evaluation/gt_eval_result.json",
    "meta/run_meta.json",
]


def _redacted_attachment_metadata(path: Path, relative_path: str) -> str:
    """Return content-free attachment metadata for a potentially sensitive artifact."""
    data = path.read_bytes()
    return json.dumps({
        "path": relative_path,
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "content_redacted": True,
    }, ensure_ascii=False, indent=2)


def attach_standard_outputs(output_dir: Path) -> None:
    if not HAS_ALLURE:
        return
    output_dir = Path(output_dir)
    for rel in STANDARD_ATTACHMENTS:
        p = output_dir / rel
        if not p.exists():
            continue
        try:
            # The legacy profile can contain parsed source documents and model
            # output.  Attach only a body-free inventory record by default.
            allure.attach(
                _redacted_attachment_metadata(p, rel),
                name=f"{rel}.redacted-metadata",
                attachment_type=allure.attachment_type.JSON,
            )
        except Exception:
            pass


def attach_invocation(invocation: dict) -> None:
    if not HAS_ALLURE or not invocation:
        return
    try:
        payload = {
            "case_id": invocation.get("case_id"),
            "backend": invocation.get("backend"),
            "status": invocation.get("status"),
            "return_code": invocation.get("return_code"),
            "duration_seconds": invocation.get("duration_seconds"),
            "command_metadata": invocation.get("command_metadata"),
            "process_output": invocation.get("process_output"),
            "error_summary": invocation.get("error_summary"),
            "content_redacted": True,
        }
        allure.attach(
            json.dumps(payload, ensure_ascii=False, indent=2),
            name="invocation_redacted_metadata",
            attachment_type=allure.attachment_type.JSON,
        )
    except Exception:
        pass
