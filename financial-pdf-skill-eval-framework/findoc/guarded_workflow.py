"""Full offline DocumentIR -> evidence -> Judge -> quality-gate workflow."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from findoc.adapters.legacy_standard import LegacyStandardAdapter, LegacyStandardAdapterError
from findoc.evidence.builder import build_evidence_pack
from findoc.evidence.models import EVIDENCE_PACK_SCHEMA_VERSION
from findoc.judge.models import JUDGE_RESULT_SCHEMA_VERSION
from findoc.judge.runner import JudgeProvider, run_structured_judge
from findoc.models.document_ir import DOCUMENT_IR_SCHEMA_VERSION, DocumentIRValidationError, validate_document_ir
from findoc.quality.gate import HUMAN_REVIEW_SCHEMA_VERSION, QUALITY_DECISION_SCHEMA_VERSION, evaluate_quality, make_human_review_request, resolve_quality_policy
from findoc.reporting.manifest_report import build_manifest_bundle, render_run_report
from findoc.runtime.manifest_runtime import ManifestRuntime, ManifestRuntimeError
from findoc.runtime.run_store import RunCollisionError, RunStore, RunStoreError, sha256_file, validate_identifier
from findoc.workflow import FindocWorkflowError, _input_info, _manifest_base, _safe_failure


class GuardedWorkflowError(RuntimeError):
    """A safe failure from a full guarded workflow."""


@dataclass(frozen=True)
class GuardedResult:
    run_id: str
    document_id: str
    state: str
    decision: str
    manifest_path: str
    report_path: str

    def to_dict(self) -> dict[str, str]:
        return {
            "run_id": self.run_id,
            "document_id": self.document_id,
            "state": self.state,
            "decision": self.decision,
            "manifest_path": self.manifest_path,
            "report_path": self.report_path,
        }


def _load_policy(path: str | Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise GuardedWorkflowError("quality policy is unavailable or invalid") from exc
    if not isinstance(value, dict):
        raise GuardedWorkflowError("quality policy must be an object")
    return value


def execute_guarded_fixture_run(
    *,
    input_path: str | Path,
    fixture_output_dir: str | Path,
    run_root: str | Path = "runs",
    run_id: str | None = None,
    document_id: str | None = None,
    parent_run_id: str | None = None,
    policy: dict[str, Any] | None = None,
    judge_provider: JudgeProvider | None = None,
    gt_assessment: dict[str, Any] | None = None,
) -> GuardedResult:
    """Execute a dependency-free fixture-replay run with explicit boundaries."""
    try:
        input_info = _input_info(Path(input_path))
    except FindocWorkflowError as exc:
        raise GuardedWorkflowError(str(exc)) from exc
    resolved_document_id = document_id or f"doc_{input_info['sha256'][:16]}"
    try:
        validate_identifier(resolved_document_id, "document_id")
        if parent_run_id is not None:
            validate_identifier(parent_run_id, "parent_run_id")
        if parent_run_id is not None and not (Path(run_root) / parent_run_id / "manifest.json").is_file():
            raise GuardedWorkflowError("parent_run_id does not reference an existing manifest in this run root")
        store = RunStore.create(run_root, requested_run_id=run_id)
    except (RunCollisionError, RunStoreError, OSError) as exc:
        raise GuardedWorkflowError(_safe_failure(exc)) from exc

    resolved_policy = resolve_quality_policy(policy)
    config = {
        "workflow_schema_version": "findoc.guarded_workflow.v1",
        "document_ir_schema_version": DOCUMENT_IR_SCHEMA_VERSION,
        "evidence_pack_schema_version": EVIDENCE_PACK_SCHEMA_VERSION,
        "judge_result_schema_version": JUDGE_RESULT_SCHEMA_VERSION,
        "quality_decision_schema_version": QUALITY_DECISION_SCHEMA_VERSION,
        "backend": "fixture",
        "adapter": "legacy_standard_fixture_v1",
        "quality_policy": resolved_policy,
        "judge_mode": "injected_provider" if judge_provider is not None else "offline_abstain",
    }
    manifest = _manifest_base(store, resolved_document_id, input_info, "fixture", config, parent_run_id)
    manifest["execution_kind"] = "fixture_replay"
    runtime = ManifestRuntime(store, manifest)
    runtime.save()

    current_stage = "initialize"
    current_attempt: int | None = None
    try:
        runtime.transition("running")
        current_stage = "parse_fixture"
        current_attempt = runtime.start_attempt(current_stage)
        adaptation = LegacyStandardAdapter(fixture_output_dir).adapt(input_sha256=input_info["sha256"])
        document_ir = {
            "schema_version": DOCUMENT_IR_SCHEMA_VERSION,
            "run_id": store.run_id,
            "document_id": resolved_document_id,
            "input": input_info,
            "backend": "fixture",
            "execution_kind": "fixture_replay",
            "page_count": adaptation.page_count,
            "pages": adaptation.pages,
            "blocks": adaptation.blocks,
            "normalized_tables": adaptation.normalized_tables,
            "financial_summary": adaptation.financial_summary,
            "financial_metrics": adaptation.financial_metrics,
            "evidence_refs": [],
            "warnings": adaptation.warnings,
            "sanitized_errors": [],
            "provenance": adaptation.provenance,
        }
        validate_document_ir(document_ir)
        document_dir = Path("documents") / resolved_document_id
        document_ir_path = store.write_json(document_dir / "document_ir.json", document_ir)
        runtime.register_current(
            document_ir_path,
            artifact_type="document_ir",
            producer="findoc.guarded_workflow.execute_guarded_fixture_run",
            schema_version=DOCUMENT_IR_SCHEMA_VERSION,
            document_id=resolved_document_id,
        )
        runtime.finish_attempt(current_stage, current_attempt, status="completed")
        current_attempt = None
        runtime.transition("parsed")

        current_stage = "build_evidence"
        current_attempt = runtime.start_attempt(current_stage)
        document_ir_hash = sha256_file(document_ir_path)
        evidence_pack = build_evidence_pack(
            document_ir,
            document_ir_hash=document_ir_hash,
            config_hash=manifest["config_hash"],
        )
        evidence_path = store.write_json(document_dir / "evidence_pack.json", evidence_pack)
        runtime.register_current(
            evidence_path,
            artifact_type="evidence_pack",
            producer="findoc.evidence.builder.build_evidence_pack",
            schema_version=EVIDENCE_PACK_SCHEMA_VERSION,
            document_id=resolved_document_id,
        )
        runtime.finish_attempt(current_stage, current_attempt, status="completed")
        current_attempt = None
        runtime.transition("evidence_ready")

        current_stage = "judge"
        current_attempt = runtime.start_attempt(current_stage)
        judge_result = run_structured_judge(
            evidence_pack,
            evidence_pack_hash=sha256_file(evidence_path),
            provider=judge_provider,
            minimum_coverage=0.8,
            attempt=current_attempt,
            max_attempts=current_attempt,
        )
        judge_path = store.write_json(document_dir / "judge_result.json", judge_result)
        runtime.register_current(
            judge_path,
            artifact_type="judge_result",
            producer="findoc.judge.runner.run_structured_judge",
            schema_version=JUDGE_RESULT_SCHEMA_VERSION,
            document_id=resolved_document_id,
        )
        runtime.finish_attempt(current_stage, current_attempt, status="completed")
        current_attempt = None
        runtime.transition("judged")

        current_stage = "quality_gate"
        current_attempt = runtime.start_attempt(current_stage)
        input_binding = document_ir["provenance"]["input_binding"]
        critical_checks = {
            "artifact_hashes": "pass",
            "document_ir_schema": "pass",
            "input_hash_binding": (
                "pass"
                if input_binding == "verified_by_legacy_input_sha256"
                else "fail"
                if input_binding == "mismatch_with_legacy_input_sha256"
                else "unknown"
            ),
        }
        decision = evaluate_quality(
            evidence_pack=evidence_pack,
            judge_result=judge_result,
            critical_checks=critical_checks,
            gt_assessment=gt_assessment,
            policy=resolved_policy,
        )
        decision_path = store.write_json(document_dir / "quality_decision.json", decision)
        runtime.register_current(
            decision_path,
            artifact_type="quality_decision",
            producer="findoc.quality.gate.evaluate_quality",
            schema_version=QUALITY_DECISION_SCHEMA_VERSION,
            document_id=resolved_document_id,
        )
        review = make_human_review_request(decision)
        if review is not None:
            review_path = store.write_json(document_dir / "human_review_request.json", review)
            runtime.register_current(
                review_path,
                artifact_type="human_review_request",
                producer="findoc.quality.gate.make_human_review_request",
                schema_version=HUMAN_REVIEW_SCHEMA_VERSION,
                document_id=resolved_document_id,
            )
        runtime.finish_attempt(current_stage, current_attempt, status="completed")
        current_attempt = None
        runtime.transition("gated")
        terminal = {
            "accepted": "completed",
            "rejected": "failed",
            "needs_review": "needs_review",
            "degraded": "degraded",
        }[decision["decision"]]
        runtime.transition(terminal)

        bundle = build_manifest_bundle(runtime, document_id=resolved_document_id)
        report_path = store.run_dir / document_dir / "report.md"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(render_run_report(bundle), encoding="utf-8")
        runtime.register_current(
            report_path,
            artifact_type="report",
            producer="findoc.reporting.manifest_report.render_run_report",
            schema_version="findoc.report.v1",
            document_id=resolved_document_id,
        )
        return GuardedResult(
            run_id=store.run_id,
            document_id=resolved_document_id,
            state=terminal,
            decision=decision["decision"],
            manifest_path="manifest.json",
            report_path=runtime.store.relative_path(report_path),
        )
    except (LegacyStandardAdapterError, DocumentIRValidationError, ManifestRuntimeError, RunStoreError, ValueError, OSError) as exc:
        failure = _safe_failure(exc)
        if current_attempt is not None:
            try:
                runtime.finish_attempt(current_stage, current_attempt, status="failed", sanitized_error=failure)
            except ManifestRuntimeError:
                pass
        runtime.manifest["sanitized_errors"].append(failure)
        if runtime.manifest["state"] != "failed":
            try:
                runtime.transition("failed")
            except ValueError:
                runtime.save()
        raise GuardedWorkflowError(failure) from exc


def execute_guarded_fixture_run_from_policy_file(*, policy_path: str | Path | None = None, **kwargs: Any) -> GuardedResult:
    return execute_guarded_fixture_run(policy=_load_policy(policy_path), **kwargs)
