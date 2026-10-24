"""static_tests 入口：聚合所有离线守门检查，输出单一 JSON 报告。

调用方式：
    python static_tests/run_static.py
    python static_tests/run_static.py --output reports/static/static.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# Ensure framework root is on sys.path when invoked directly.
_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from static_tests import checks_security


def _dependency_unavailable(name: str, exc: Exception) -> dict[str, Any]:
    """Keep the security scan usable when optional test dependencies are absent."""
    return {
        "name": name,
        "passed": False,
        "dependency_unavailable": True,
        "message": f"offline dependency unavailable: {type(exc).__name__}",
    }


def run_all() -> dict[str, Any]:
    sec = checks_security.run()
    dependency_error: str | None = None
    try:
        from static_tests import checks_cases, checks_ground_truth, checks_skill_package
        skill = checks_skill_package.run()
        cases = checks_cases.run()
        gt = checks_ground_truth.run()
    except Exception as exc:
        dependency_error = type(exc).__name__
        skill = _dependency_unavailable("skill_package_ok", exc)
        cases = _dependency_unavailable("case_schema_ok", exc)
        gt = _dependency_unavailable("ground_truth_ok", exc)

    overall = (
        bool(skill["passed"]) and bool(cases["passed"])
        and bool(gt["passed"]) and bool(sec["passed"])
    )

    return {
        "overall_pass": overall,
        "checks": {
            "skill_package_ok": skill["passed"],
            "case_schema_ok": cases["passed"],
            "ground_truth_ok": gt["passed"],
            "no_secret_leak": sec.get("no_secret_leak", False),
            "no_absolute_path": sec.get("no_absolute_path", False),
            "security_release_ok": sec.get("passed", False),
        },
        "details": {
            "skill_package": skill,
            "cases": cases,
            "ground_truth": gt,
            "security": sec,
        },
        "warnings": gt.get("warnings") or [],
        "dependency_error": dependency_error,
        "message": "" if overall else "static_tests failed or dependencies are unavailable; see details",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run static (offline) gate checks for the eval framework.")
    parser.add_argument("--output", type=str, default=None, help="Optional path to write JSON report")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON to stdout")
    args = parser.parse_args()

    report = run_all()
    text = json.dumps(report, ensure_ascii=False, indent=2 if args.pretty else None)
    print(text)
    if args.output:
        out = Path(args.output)
        if not out.is_absolute():
            out = (_ROOT / out).resolve()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if report["overall_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
