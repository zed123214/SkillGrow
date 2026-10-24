"""Stable user CLI for isolated and guarded FinDoc workflows."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from findoc.adapters.openclaw_runtime import discover_openclaw
from findoc.guarded_workflow import GuardedWorkflowError, execute_guarded_fixture_run_from_policy_file
from findoc.workflow import FindocWorkflowError, analyze_document
from framework.sanitization import sanitize_exception_message


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="findoc",
        description="Create isolated DocumentIR or guarded offline fixture-replay runs.",
    )
    parser.add_argument("--version", action="version", version="findoc 1.0")
    subparsers = parser.add_subparsers(dest="command", required=True)
    analyze = subparsers.add_parser("analyze", help="adapt one legacy standard fixture into DocumentIR")
    analyze.add_argument("--input", required=True, type=Path, help="public or synthetic PDF used as the run input")
    analyze.add_argument("--backend", choices=["fixture"], default="fixture", help="T1 supports offline fixture replay only")
    analyze.add_argument("--fixture-output-dir", required=True, type=Path, help="existing legacy standard-profile output directory; read only")
    analyze.add_argument("--run-root", type=Path, default=Path("runs"), help="directory that will receive a unique run subdirectory")
    analyze.add_argument("--run-id", help="optional safe run identifier; an existing id is never overwritten")
    analyze.add_argument("--parent-run-id", help="optional prior run identifier for an explicit retry lineage")
    analyze.add_argument("--document-id", help="optional safe document identifier")
    guard = subparsers.add_parser("guard", help="run DocumentIR, evidence, Judge abstain, and quality gate offline")
    guard.add_argument("--input", required=True, type=Path, help="public or synthetic PDF used as the run input")
    guard.add_argument("--fixture-output-dir", required=True, type=Path, help="existing legacy standard-profile output directory; read only")
    guard.add_argument("--run-root", type=Path, default=Path("runs"), help="directory that will receive a unique run subdirectory")
    guard.add_argument("--run-id", help="optional safe run identifier; an existing id is never overwritten")
    guard.add_argument("--parent-run-id", help="optional prior run identifier for an explicit retry lineage")
    guard.add_argument("--document-id", help="optional safe document identifier")
    guard.add_argument("--policy", type=Path, help="optional JSON quality-policy file")
    check = subparsers.add_parser("openclaw-check", help="report non-secret local OpenClaw readiness facts")
    check.add_argument("--gateway-port", type=int, default=18789)
    check.add_argument("--workspace", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "openclaw-check":
        check = discover_openclaw(workspace=args.workspace, gateway_port=args.gateway_port)
        print(json.dumps(check.to_dict(), ensure_ascii=False, sort_keys=True))
        return 0 if check.ready else 3
    if args.command == "guard":
        try:
            result = execute_guarded_fixture_run_from_policy_file(
                input_path=args.input,
                fixture_output_dir=args.fixture_output_dir,
                run_root=args.run_root,
                run_id=args.run_id,
                document_id=args.document_id,
                parent_run_id=args.parent_run_id,
                policy_path=args.policy,
            )
        except GuardedWorkflowError as exc:
            print(json.dumps({"status": "failed", "error": sanitize_exception_message(exc)}, ensure_ascii=False))
            return 2
        print(json.dumps({"status": result.state, **result.to_dict()}, ensure_ascii=False))
        return 0 if result.state != "failed" else 2
    if args.command != "analyze":
        return 2
    try:
        result = analyze_document(
            input_path=args.input,
            backend=args.backend,
            fixture_output_dir=args.fixture_output_dir,
            run_root=args.run_root,
            run_id=args.run_id,
            document_id=args.document_id,
            parent_run_id=args.parent_run_id,
        )
    except FindocWorkflowError as exc:
        print(json.dumps({"status": "failed", "error": sanitize_exception_message(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps({"status": "completed", **result.to_dict()}, ensure_ascii=False))
    return 0
