"""P0 pipeline 编排器。

链路：
    static_check（可选） → invoke → collect_artifacts → assert_outputs
    → evaluate_ground_truth（过滤后） → compute_score → 写 score_result.json

本模块**只做编排**：所有校验 / 评估 / 评分逻辑都在各自模块。每个 stage 的结果
统一封装成 ``StageResult`` 写入 ``CaseRunResult.stages``，便于 report 层引用。
"""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from framework import (
    artifact_collector,
    gt_evaluator,
    run_trace,
    scoring_model,
    skill_invoker,
)
from framework.assertion_engine import run_validations
from framework.context import FRAMEWORK_ROOT
from framework.logger import get_logger
from framework.sanitization import repository_relative_path, sanitize_exception_message
from framework.stage_result import StageResult, failed, skipped, success, warning

LOG = get_logger("pipeline")


@dataclass
class CaseRunResult:
    case_id: str
    backend: str
    status: str  # success | failed | skipped
    stages: list[StageResult] = field(default_factory=list)
    output_dir: Path | None = None
    score_result: dict | None = None
    invocation: dict | None = None
    assertions: list[dict] = field(default_factory=list)
    gt_eval: dict | None = None
    artifacts: dict | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["output_dir"] = str(self.output_dir) if self.output_dir else None
        d["stages"] = [s.to_dict() for s in self.stages]
        return d


def _resolve(p: str | Path) -> Path:
    path = Path(p)
    if path.is_absolute():
        return path
    return (FRAMEWORK_ROOT / path).resolve()


def _stage_static_check(case: dict, *, fail_fast: bool) -> StageResult:
    """Run static_tests.run_static so a single case-runner can self-gate."""
    try:
        from static_tests.run_static import run_all as _run_all
    except Exception as exc:  # pragma: no cover - import error means bigger problem
        return warning("static_check", f"static_tests unavailable: {sanitize_exception_message(exc)}")
    try:
        report = _run_all()
    except Exception as exc:
        return warning("static_check", f"static_tests crashed: {sanitize_exception_message(exc)}")
    if report.get("overall_pass"):
        return success("static_check", payload=report)
    if fail_fast:
        return failed("static_check", errors=[report.get("message", "static_tests overall_pass=false")], payload=report)
    return warning(
        "static_check",
        message=report.get("message", "static_tests overall_pass=false (continuing)"),
        payload=report,
    )


def _stage_invoke(case: dict, *, dry_run: bool) -> tuple[StageResult, dict]:
    inv = skill_invoker.invoke(case, dry_run=dry_run)
    if inv.get("status") == "skipped":
        return skipped("invoke", reason=inv.get("skip_reason") or "skipped", payload=dict(inv)), inv
    if inv.get("status") == "failed":
        return failed("invoke", errors=[inv.get("error_summary") or "invocation failed"], payload=dict(inv)), inv
    return success("invoke", payload=dict(inv)), inv


def _stage_collect(case: dict, invocation: dict) -> tuple[StageResult, dict]:
    out_dir = invocation.get("output_dir") or case.get("output_dir")
    info = artifact_collector.collect(case, out_dir)
    if not info.get("artifact_ok"):
        return failed(
            "collect_artifacts",
            errors=[f"missing required artifacts: {info.get('missing')}"],
            payload=info,
        ), info
    return success("collect_artifacts", payload=info), info


def _stage_assert(case: dict, invocation: dict) -> tuple[StageResult, list[dict]]:
    assertions = run_validations(case, invocation)
    bad = [a for a in assertions if not a.get("passed")]
    payload = {"assertions": assertions, "passed": len(assertions) - len(bad), "failed": len(bad)}
    if bad:
        return failed(
            "assert_outputs",
            errors=[f"{a.get('type')}: {a.get('message')}" for a in bad],
            payload=payload,
        ), assertions
    return success("assert_outputs", payload=payload), assertions


def _stage_gt(case: dict, invocation: dict) -> tuple[StageResult, dict]:
    out_dir = invocation.get("output_dir") or case.get("output_dir")
    gt_path = case.get("ground_truth")
    if not gt_path:
        gt = {"status": "skipped", "reason": "no ground_truth declared", "no_ground_truth": True}
        return skipped("evaluate_ground_truth", reason=gt["reason"], payload=gt), gt
    gt = gt_evaluator.evaluate(out_dir, gt_path)
    if gt.get("status") == "success":
        return success("evaluate_ground_truth", payload=gt), gt
    if gt.get("no_ground_truth"):
        return skipped("evaluate_ground_truth", reason=gt.get("reason", "no_ground_truth"), payload=gt), gt
    if gt.get("status") == "failed":
        return failed("evaluate_ground_truth", errors=[gt.get("reason", "gt eval failed")], payload=gt), gt
    return skipped("evaluate_ground_truth", reason=gt.get("reason") or "skipped", payload=gt), gt


def _stage_score(case: dict, assertions: list[dict], gt_eval: dict) -> tuple[StageResult, dict | None]:
    profile_path = (case.get("scoring") or {}).get("profile_path")
    try:
        score = scoring_model.compute_for_case(case, assertions, gt_eval, profile_path)
    except Exception as exc:
        return failed("compute_score", errors=[f"scoring crashed: {sanitize_exception_message(exc)}"]), None
    try:
        target = scoring_model.write_score_result(case["output_dir"], score)
        payload = {**score, "score_result_path": str(target)}
    except Exception as exc:
        return failed("compute_score", errors=[f"failed to write score_result.json: {sanitize_exception_message(exc)}"], payload=score), score
    return success("compute_score", payload=payload), score


def _stage_judge(case: dict, score: dict | None) -> StageResult:
    """可选 Assessment-Skill Judge stage（在 compute_score 之后）。

    judge.enabled=false 时：不调用 llm_judge、不写 judge_result.json，标记 skipped，
    且 score_sources.llm_judge 保持 []，与 P0 流程完全一致。
    enabled=true 时：调用 Judge，把维度名追加进 score_result.score_sources.llm_judge，
    并把更新后的 score_result 重写回盘。**不重算** weighted_score。
    """
    try:
        from judge import llm_judge
    except Exception as exc:  # pragma: no cover - judge layer optional
        return warning("run_judge", f"judge layer unavailable: {sanitize_exception_message(exc)}")

    config = llm_judge.load_judge_config()
    if not llm_judge.is_enabled(config):
        return skipped("run_judge", reason="judge.enabled=false", payload={"enabled": False})

    try:
        outcome = llm_judge.run_for_case(case, config)
    except Exception as exc:
        return warning("run_judge", f"judge crashed (non-fatal): {sanitize_exception_message(exc)}")

    dims = outcome.get("judge_dimensions") or []
    if score is not None and dims:
        sources = score.setdefault("score_sources", {"deterministic": list((score.get("dimensions") or {}).keys()), "llm_judge": []})
        merged = list(dict.fromkeys([*(sources.get("llm_judge") or []), *dims]))
        sources["llm_judge"] = merged
        try:
            scoring_model.write_score_result(case["output_dir"], score)
        except Exception as exc:  # pragma: no cover
            return warning("run_judge", f"judge ran but score_sources rewrite failed: {sanitize_exception_message(exc)}", payload=outcome)

    payload = {
        "enabled": True,
        "mode": outcome.get("mode"),
        "judge_dimensions": dims,
        "judge_result_path": outcome.get("judge_result_path"),
        "warning": outcome.get("warning"),
    }
    return success("run_judge", payload=payload)


def _new_trace_writer(case: dict, case_id: str, backend: str) -> run_trace.RunTraceWriter | None:
    output_dir = case.get("output_dir")
    if not output_dir:
        return None
    return run_trace.RunTraceWriter(
        run_trace.trace_path_for_output(_resolve(output_dir)),
        case_id=case_id,
        backend=backend,
    )


def _trace_stage_payload(stage: StageResult) -> dict[str, Any]:
    payload = stage.payload or {}
    if stage.name == "static_check":
        checks = payload.get("checks")
        return {
            "overall_pass": payload.get("overall_pass"),
            "check_count": len(checks) if isinstance(checks, list) else None,
        }
    if stage.name == "invoke":
        return {
            "backend": payload.get("backend"),
            "return_code": payload.get("return_code"),
            "output_dir": repository_relative_path(payload.get("output_dir"), FRAMEWORK_ROOT.parent),
            "duration_seconds": payload.get("duration_seconds"),
            "skip_reason": payload.get("skip_reason"),
            "error_summary": payload.get("error_summary"),
            "process_output": payload.get("process_output"),
            "command_metadata": payload.get("command_metadata"),
        }
    if stage.name == "collect_artifacts":
        return {
            "artifact_ok": payload.get("artifact_ok"),
            "artifact_count": len(payload.get("artifacts") or []),
            "missing": payload.get("missing") or [],
            "optional_missing": payload.get("optional_missing") or [],
        }
    if stage.name == "assert_outputs":
        return {
            "passed": payload.get("passed"),
            "failed": payload.get("failed"),
        }
    if stage.name == "evaluate_ground_truth":
        return {
            "status": payload.get("status"),
            "reason": payload.get("reason"),
            "no_ground_truth": payload.get("no_ground_truth"),
            "numeric_accuracy": payload.get("numeric_accuracy"),
            "failed_items_count": len(payload.get("failed_items") or []),
        }
    if stage.name == "compute_score":
        return {
            "weighted_score": payload.get("weighted_score"),
            "level": payload.get("level"),
            "score_result_path": payload.get("score_result_path"),
        }
    if stage.name == "run_judge":
        return {
            "enabled": payload.get("enabled"),
            "mode": payload.get("mode"),
            "judge_dimensions": payload.get("judge_dimensions") or [],
            "judge_result_path": payload.get("judge_result_path"),
            "warning": payload.get("warning"),
        }
    return {}


def _emit_stage(
    trace: run_trace.RunTraceWriter | None,
    stage: StageResult,
    started_at: float,
) -> None:
    if trace is None:
        return
    trace.emit(
        "stage_finished",
        stage=stage.name,
        status=stage.status,
        duration_ms=int((time.monotonic() - started_at) * 1000),
        data={
            "errors": stage.errors,
            **_trace_stage_payload(stage),
        },
    )


def run_pipeline(case: dict, *, static_first: bool = True, dry_run: bool = False, static_fail_fast: bool = False) -> CaseRunResult:
    """Run the P0 pipeline for one case.

    Args:
        case: normalized case dict (from ``case_loader``).
        static_first: if True, run static_tests before invoking; default ``True``.
            Failures are recorded as ``warning`` (not fatal) unless
            ``static_fail_fast`` is True.
        dry_run: forwarded to ``skill_invoker.invoke``.
        static_fail_fast: when ``True`` static_check failure aborts the pipeline.
    """
    case_id = case.get("case_id", "<unknown>")
    backend = case.get("backend", "fixture")
    result = CaseRunResult(case_id=case_id, backend=backend, status="success")
    trace = _new_trace_writer(case, case_id, backend)
    if trace is not None:
        trace.emit(
            "run_started",
            status="running",
            data={
                "case_name": case.get("name", case_id),
                "output_profile": case.get("output_profile"),
                "static_first": static_first,
                "dry_run": dry_run,
            },
        )

    if static_first:
        if trace is not None:
            trace.emit("stage_started", stage="static_check", status="running")
        t0 = time.monotonic()
        sc = _stage_static_check(case, fail_fast=static_fail_fast)
        result.stages.append(sc)
        _emit_stage(trace, sc, t0)
        if sc.status == "failed":
            result.status = "failed"
            if trace is not None:
                trace.emit("run_finished", status=result.status, data={"stage_count": len(result.stages)})
            return result

    if trace is not None:
        trace.emit("stage_started", stage="invoke", status="running")
    t0 = time.monotonic()
    inv_stage, invocation = _stage_invoke(case, dry_run=dry_run)
    result.stages.append(inv_stage)
    result.invocation = invocation
    _emit_stage(trace, inv_stage, t0)
    if inv_stage.status == "skipped":
        result.status = "skipped"
        result.output_dir = Path(invocation.get("output_dir")) if invocation.get("output_dir") else None
        if trace is not None:
            trace.emit("run_finished", status=result.status, data={"stage_count": len(result.stages)})
        return result
    if inv_stage.status == "failed":
        result.status = "failed"
        result.output_dir = Path(invocation.get("output_dir")) if invocation.get("output_dir") else None
        if trace is not None:
            trace.emit("run_finished", status=result.status, data={"stage_count": len(result.stages)})
        return result

    result.output_dir = Path(invocation["output_dir"]) if invocation.get("output_dir") else None

    if trace is not None:
        trace.emit("stage_started", stage="collect_artifacts", status="running")
    t0 = time.monotonic()
    collect_stage, artifacts = _stage_collect(case, invocation)
    result.stages.append(collect_stage)
    result.artifacts = artifacts
    _emit_stage(trace, collect_stage, t0)
    if collect_stage.status == "failed":
        result.status = "failed"
        if trace is not None:
            trace.emit("run_finished", status=result.status, data={"stage_count": len(result.stages)})
        return result

    if trace is not None:
        trace.emit("stage_started", stage="assert_outputs", status="running")
    t0 = time.monotonic()
    assert_stage, assertions = _stage_assert(case, invocation)
    result.stages.append(assert_stage)
    result.assertions = assertions
    _emit_stage(trace, assert_stage, t0)
    # Even if some assertions fail, we still compute downstream signals so the
    # report can show *why* the case scored poorly.

    if trace is not None:
        trace.emit("stage_started", stage="evaluate_ground_truth", status="running")
    t0 = time.monotonic()
    gt_stage, gt_eval = _stage_gt(case, invocation)
    result.stages.append(gt_stage)
    result.gt_eval = gt_eval
    _emit_stage(trace, gt_stage, t0)

    if trace is not None:
        trace.emit("stage_started", stage="compute_score", status="running")
    t0 = time.monotonic()
    score_stage, score = _stage_score(case, assertions, gt_eval)
    result.stages.append(score_stage)
    result.score_result = score
    _emit_stage(trace, score_stage, t0)

    # Optional Assessment-Skill Judge stage. Default (judge.enabled=false) is a no-op
    # skipped stage and never touches judge_result.json or weighted_score.
    if trace is not None:
        trace.emit("stage_started", stage="run_judge", status="running")
    t0 = time.monotonic()
    judge_stage = _stage_judge(case, score)
    result.stages.append(judge_stage)
    _emit_stage(trace, judge_stage, t0)

    # Overall pipeline status: failed if any non-static stage failed.
    if any(s.status == "failed" for s in result.stages if s.name != "static_check"):
        result.status = "failed"
    if trace is not None:
        trace.emit("run_finished", status=result.status, data={"stage_count": len(result.stages)})
    return result


def run_pipeline_from_yaml(case_path: str | Path, *, static_first: bool = True, dry_run: bool = False) -> CaseRunResult:
    from framework import case_loader
    case = case_loader.load_case(case_path)
    return run_pipeline(case, static_first=static_first, dry_run=dry_run)
