"""标准 profile 输出契约验证。

必需项 (标准 profile)：
  raw/parsed.md
  normalized/normalized_tables.json
  normalized/financial_summary.json
  evaluation/quality_checks.json
  meta/run_meta.json

可选项，但 Skill 自身的验证器也期望包含：
  evaluation/evaluation_report.md

我们倾向于委托给 Skill 自身的 validate_outputs.py 来处理；如果该脚本不可用，则回退到使用本地文件检查。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from framework.context import load_config
from framework.logger import get_logger
from framework.sanitization import process_output_metadata, sanitize_exception_message

LOG = get_logger("output_contract")

STANDARD_REQUIRED = [
    "raw/parsed.md",
    "normalized/normalized_tables.json",
    "normalized/financial_summary.json",
    "evaluation/quality_checks.json",
    "meta/run_meta.json",
]

STANDARD_OPTIONAL = [
    "evaluation/evaluation_report.md",
    "evaluation/gt_eval_result.json",
    "raw/pages_detail.json",
]


def _read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        LOG.warning("failed to read JSON artifact: %s", sanitize_exception_message(e))
        return default


def read_run_meta(output_dir: Path) -> dict:
    return _read_json(Path(output_dir) / "meta" / "run_meta.json", {}) or {}


def read_quality_checks(output_dir: Path) -> dict:
    return _read_json(Path(output_dir) / "evaluation" / "quality_checks.json", {}) or {}


def read_financial_summary(output_dir: Path) -> dict:
    return _read_json(Path(output_dir) / "normalized" / "financial_summary.json", {}) or {}


def _local_validate(output_dir: Path, profile: str) -> dict:
    output_dir = Path(output_dir)
    if profile != "standard":
        return {"output_profile": profile, "passed": False, "missing_files": [], "checked_files": [], "message": f"local validator only supports standard, got {profile}"}
    missing = [p for p in STANDARD_REQUIRED if not (output_dir / p).exists()]
    return {
        "output_profile": profile,
        "passed": not missing,
        "missing_files": missing,
        "checked_files": STANDARD_REQUIRED,
        "message": "Output validation passed for standard profile." if not missing else f"Missing files: {missing}",
    }


def validate_standard_output(output_dir: Path, profile: str = "standard") -> dict:
    cfg = load_config()
    script = cfg.skill.validate_script
    if not script.exists():
        LOG.info("skill validate_outputs.py not found; falling back to local check")
        return _local_validate(output_dir, profile)
    cmd = [sys.executable, str(script), "--output-dir", str(output_dir), "--output-profile", profile]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False, timeout=60)
    except Exception as e:
        LOG.warning("skill validate_outputs.py failed (%s); falling back", sanitize_exception_message(e))
        return _local_validate(output_dir, profile)
    out = proc.stdout.strip()
    try:
        return json.loads(out)
    except Exception:
        return {
            "output_profile": profile,
            "passed": proc.returncode == 0,
            "missing_files": [],
            "checked_files": [],
            "message": "validator returned non-JSON output; process output redacted",
            "process_output": process_output_metadata(proc.stdout, proc.stderr),
        }
