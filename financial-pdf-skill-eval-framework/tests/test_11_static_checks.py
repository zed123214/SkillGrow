"""Tests for static_tests/ offline gatekeeping checks."""
from __future__ import annotations

import json
import subprocess
import sys

import pytest

from framework.context import FRAMEWORK_ROOT
from static_tests import checks_cases, checks_ground_truth, checks_security, checks_skill_package
from static_tests.run_static import run_all


@pytest.mark.offline
def test_skill_package_check_passes():
    res = checks_skill_package.run()
    assert res["passed"], res


@pytest.mark.offline
def test_case_schema_check_no_issues():
    res = checks_cases.run()
    # No hard fail expected on the canonical case files. Surface details when failing.
    assert res["passed"], res


@pytest.mark.offline
def test_ground_truth_check_handles_todo_as_warning():
    res = checks_ground_truth.run()
    # todo_manual_verify slots are warnings, not failures.
    assert res["passed"], res
    # Ensure the BYD todo template is reported as a warning, not a hard error.
    paths = " ".join(w.lower() for w in res.get("warnings", []))
    # We at least expect *one* todo_manual_verify slot to be flagged as warning,
    # since the dataset ships with BYD / Huadian / Pioneer templates today.
    if any(f.get("source") == "todo_manual_verify" for f in res.get("files", [])):
        assert res["warnings"], "todo_manual_verify slots should appear in warnings"


@pytest.mark.offline
def test_security_check_passes_on_clean_repo():
    res = checks_security.run()
    assert res["no_secret_leak"], res
    assert res["no_absolute_path"], res


@pytest.mark.offline
def test_run_static_aggregates_overall_pass():
    report = run_all()
    assert isinstance(report, dict)
    assert "overall_pass" in report
    assert isinstance(report["checks"], dict)
    for key in ("skill_package_ok", "case_schema_ok", "ground_truth_ok", "no_secret_leak", "no_absolute_path", "security_release_ok"):
        assert key in report["checks"], f"missing check: {key}"


@pytest.mark.offline
def test_run_static_cli_outputs_json():
    proc = subprocess.run(
        [sys.executable, str(FRAMEWORK_ROOT / "static_tests" / "run_static.py"), "--pretty"],
        capture_output=True,
        text=True,
        timeout=60,
        encoding="utf-8",
        errors="replace",
    )
    assert proc.returncode in (0, 1), proc.stderr
    data = json.loads(proc.stdout)
    assert "overall_pass" in data
    assert "checks" in data
    for key in ("skill_package_ok", "case_schema_ok", "ground_truth_ok", "no_secret_leak", "no_absolute_path", "security_release_ok"):
        assert key in data["checks"]
