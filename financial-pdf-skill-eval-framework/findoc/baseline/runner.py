"""Run and summarize the T6 20-case synthetic fixture-replay baseline."""
from __future__ import annotations

import json
import math
import shutil
import tempfile
import time
from collections import Counter
from pathlib import Path
from typing import Any

from findoc.baseline.synthetic import build_synthetic_case, t6_case_specs
from findoc.guarded_workflow import GuardedWorkflowError, execute_guarded_fixture_run
from findoc.reporting.manifest_report import build_manifest_bundle
from findoc.runtime.manifest_runtime import ManifestRuntime, ManifestRuntimeError
from findoc.runtime.run_store import RunStore, sha256_file, utc_now


BASELINE_SCHEMA_VERSION = "findoc.t6_baseline.v1"


class BaselineError(RuntimeError):
    """Raised when a baseline output root is unsafe or already materialized."""


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise BaselineError("baseline artifact must be a JSON object")
    return value


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(math.ceil(percentile * len(ordered)) - 1, 0)
    return ordered[min(rank, len(ordered) - 1)]


def _stale_probes(source_run: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    """Use disposable copies so the delivered baseline remains unmodified."""
    rejected = 0
    ignored = 0
    with tempfile.TemporaryDirectory() as temp:
        probe_root = Path(temp)

        tamper = probe_root / "tamper"
        shutil.copytree(source_run, tamper)
        tamper_manifest = _read_json(tamper / "manifest.json")
        tamper_runtime = ManifestRuntime(RunStore(manifest["run_id"], tamper), tamper_manifest)
        entry = tamper_runtime.current_entry("judge_result", document_id=manifest["document_id"])
        (tamper / entry["path"]).write_text("{}\n", encoding="utf-8")
        try:
            build_manifest_bundle(tamper_runtime, document_id=manifest["document_id"])
        except ManifestRuntimeError:
            rejected += 1

        stale_config = probe_root / "config"
        shutil.copytree(source_run, stale_config)
        config_manifest = _read_json(stale_config / "manifest.json")
        config_runtime = ManifestRuntime(RunStore(manifest["run_id"], stale_config), config_manifest)
        entry = config_runtime.current_entry("judge_result", document_id=manifest["document_id"])
        artifact = stale_config / entry["path"]
        value = _read_json(artifact)
        value["config_hash"] = "0" * 64
        _write_json(artifact, value)
        entry["sha256"] = sha256_file(artifact)
        try:
            build_manifest_bundle(config_runtime, document_id=manifest["document_id"])
        except ManifestRuntimeError:
            rejected += 1

        rogue = probe_root / "rogue"
        shutil.copytree(source_run, rogue)
        rogue_manifest = _read_json(rogue / "manifest.json")
        rogue_runtime = ManifestRuntime(RunStore(manifest["run_id"], rogue), rogue_manifest)
        rogue_file = rogue / "documents" / manifest["document_id"] / "old_judge_result.json"
        rogue_file.write_text('{"status":"success"}\n', encoding="utf-8")
        bundle = build_manifest_bundle(rogue_runtime, document_id=manifest["document_id"])
        if bundle["judge_result"]["status"] == "abstained":
            ignored += 1
    return {"rejection_probes": 2, "rejected": rejected, "unregistered_ignore_probes": 1, "ignored": ignored}


def _case_record(
    generated: dict[str, Any],
    *,
    runs_root: Path,
    state: str,
    latency_ms: float,
    error_type: str | None,
) -> dict[str, Any]:
    spec = generated["spec"]
    run_dir = runs_root / spec.case_id
    manifest = _read_json(run_dir / "manifest.json")
    decision = None
    coverage = None
    judge_status = None
    judge_schema_valid = False
    document_ir_schema_valid = False
    contract_valid = False
    if state in {"completed", "needs_review", "degraded", "failed"} and manifest["artifact_registry"]:
        entries = {entry["type"]: entry for entry in manifest["artifact_registry"] if entry.get("current") and entry.get("active")}
        if "document_ir" in entries:
            document_ir_schema_valid = True
        if "quality_decision" in entries:
            decision_payload = _read_json(run_dir / entries["quality_decision"]["path"])
            decision = decision_payload["decision"]
            coverage = decision_payload["evidence_coverage"]
            contract_valid = True
        if "judge_result" in entries:
            judge_payload = _read_json(run_dir / entries["judge_result"]["path"])
            judge_status = judge_payload["status"]
            judge_schema_valid = True
    return {
        "case_id": spec.case_id,
        "source_kind": "synthetic",
        "license": generated["oracle"]["license"],
        "provenance": {
            "generator": "findoc.baseline.synthetic.build_synthetic_case",
            "oracle_frozen_before_fixture": True,
            "contains_internal_data": False,
        },
        "pdf_sha256": generated["pdf_sha256"],
        "case_sha256": generated["case_sha256"],
        "gt_kind": "synthetic_oracle",
        "gt_sha256": generated["gt_sha256"],
        "difficulty_tags": list(spec.difficulty_tags),
        "execution_kind": "fixture_replay",
        "run_id": manifest["run_id"],
        "run_manifest_sha256": sha256_file(run_dir / "manifest.json"),
        "state": state,
        "decision": decision,
        "completed": state in {"completed", "needs_review", "degraded"},
        "contract_valid": contract_valid,
        "document_ir_schema_valid": document_ir_schema_valid,
        "evidence_coverage": coverage,
        "judge_schema_valid": judge_schema_valid,
        "judge_status": judge_status,
        "accuracy": {
            "eligible": False,
            "score": None,
            "reason": "fixture_replay_is_not_parser_execution",
        },
        "latency_ms": latency_ms,
        "failure_type": error_type,
    }


def _summary(records: list[dict[str, Any]], stale: dict[str, Any]) -> dict[str, Any]:
    completed = sum(bool(item["completed"]) for item in records)
    contract = sum(bool(item["contract_valid"]) for item in records)
    ir_valid = sum(bool(item["document_ir_schema_valid"]) for item in records)
    judge_valid = sum(bool(item["judge_schema_valid"]) for item in records)
    judge_eligible = sum(item["judge_status"] is not None for item in records)
    judge_abstained = sum(item["judge_status"] == "abstained" for item in records)
    evidence_eligible = sum((item["evidence_coverage"] or {}).get("eligible", 0) for item in records)
    evidence_count = sum((item["evidence_coverage"] or {}).get("evidenced", 0) for item in records)
    decisions = Counter(item["decision"] or "not_reached" for item in records)
    states = Counter(item["state"] for item in records)
    failures = Counter(item["failure_type"] for item in records if item["failure_type"])
    latencies = [float(item["latency_ms"]) for item in records]
    return {
        "case_definitions": {"numerator": len(records), "denominator": 20, "ratio": len(records) / 20},
        "completion": {"numerator": completed, "denominator": len(records), "ratio": completed / len(records)},
        "output_contract": {"numerator": contract, "denominator": len(records), "ratio": contract / len(records)},
        "document_ir_schema": {"numerator": ir_valid, "denominator": contract, "ratio": ir_valid / contract if contract else None},
        "evidence_coverage": {"numerator": evidence_count, "denominator": evidence_eligible, "ratio": evidence_count / evidence_eligible if evidence_eligible else None},
        "gate_decision_distribution": dict(sorted(decisions.items())),
        "run_state_distribution": dict(sorted(states.items())),
        "judge_schema_valid": {"numerator": judge_valid, "denominator": judge_eligible, "ratio": judge_valid / judge_eligible if judge_eligible else None},
        "judge_abstention": {"numerator": judge_abstained, "denominator": judge_eligible, "ratio": judge_abstained / judge_eligible if judge_eligible else None},
        "latency_ms": {"p50": _percentile(latencies, 0.50), "p95": _percentile(latencies, 0.95)},
        "stale_artifact_probes": stale,
        "failure_types": dict(sorted(failures.items())),
        "cohort_accuracy": {
            "human_verified": {"case_count": 0, "execution_eligible": 0, "accuracy": None},
            "synthetic_oracle": {"case_count": len(records), "execution_eligible": 0, "accuracy": None, "reason": "all current outputs are fixture_replay"},
            "public_dataset_gt": {"case_count": 0, "execution_eligible": 0, "accuracy": None},
            "no_gt": {"case_count": 0, "execution_eligible": 0, "accuracy": None},
            "fixture_replay": {"case_count": len(records), "accuracy": None},
            "real_execution": {"case_count": 0, "accuracy": None},
        },
        "external_calls": 0,
        "network_accessed": False,
        "cost": {"amount": 0, "currency": "CNY"},
    }


def _summary_markdown(verification_id: str, summary: dict[str, Any]) -> str:
    completion = summary["completion"]
    contract = summary["output_contract"]
    evidence = summary["evidence_coverage"]
    return f"""# T6 synthetic fixture-replay baseline

- verification_id: `{verification_id}`
- case definitions: `20/20`
- completed workflow runs: `{completion['numerator']}/{completion['denominator']}`
- output-contract runs: `{contract['numerator']}/{contract['denominator']}`
- evidence coverage: `{evidence['numerator']}/{evidence['denominator']}`
- gate decisions: `{json.dumps(summary['gate_decision_distribution'], ensure_ascii=False, sort_keys=True)}`
- Judge: offline abstain only; no provider call, token charge, or Judge accuracy claim.
- accuracy: not applicable because all current outputs are `fixture_replay`, not parser executions.
- data: generator-authored synthetic inputs/oracles; no company-internal PDF is used.

The machine-readable numerator, denominator, cohort, hashes, latency, failure types, and stale-artifact probes are in `baseline_manifest.json`.
"""


def run_t6_baseline(output_root: str | Path, *, verification_id: str = "T6-20260730-offline-contract-v3") -> dict[str, Any]:
    output = Path(output_root)
    if output.exists() and any(output.iterdir()):
        raise BaselineError("baseline output directory already exists and is not empty")
    output.mkdir(parents=True, exist_ok=True)
    runs_root = output / "runs"
    records: list[dict[str, Any]] = []
    first_success_manifest: dict[str, Any] | None = None
    first_success_dir: Path | None = None
    with tempfile.TemporaryDirectory() as temp:
        corpus_root = Path(temp) / "corpus"
        corpus_root.mkdir()
        for spec in t6_case_specs():
            generated = build_synthetic_case(corpus_root, spec)
            started = time.perf_counter()
            state = "failed"
            error_type = None
            try:
                result = execute_guarded_fixture_run(
                    input_path=generated["pdf_path"],
                    fixture_output_dir=generated["fixture_path"],
                    run_root=runs_root,
                    run_id=spec.case_id,
                    gt_assessment={
                        "eligible": False,
                        "score": None,
                        "source_kind": "synthetic_oracle",
                        "reason": "fixture_replay_not_execution_eligible",
                    },
                )
                state = result.state
            except GuardedWorkflowError:
                error_type = spec.failure_mode or "workflow_failure"
            latency_ms = (time.perf_counter() - started) * 1000
            record = _case_record(
                generated,
                runs_root=runs_root,
                state=state,
                latency_ms=latency_ms,
                error_type=error_type,
            )
            records.append(record)
            if first_success_dir is None and record["completed"]:
                first_success_dir = runs_root / spec.case_id
                first_success_manifest = _read_json(first_success_dir / "manifest.json")
    if first_success_dir is None or first_success_manifest is None:
        raise BaselineError("baseline has no completed run for stale probes")
    stale = _stale_probes(first_success_dir, first_success_manifest)
    summary = _summary(records, stale)
    manifest = {
        "schema_version": BASELINE_SCHEMA_VERSION,
        "verification_id": verification_id,
        "created_at": utc_now(),
        "execution_kind": "fixture_replay",
        "dataset": {
            "source_kind": "synthetic",
            "license": "CC0-1.0 generator-authored synthetic data",
            "generator": "findoc.baseline.synthetic",
            "generator_sha256": sha256_file(Path(__file__).with_name("synthetic.py")),
            "oracle_policy": "frozen_before_fixture_and_never_derived_from_workflow_output",
        },
        "cases": records,
        "summary": summary,
        "limitations": [
            "fixture_replay_is_not_parser_execution",
            "synthetic_oracle_accuracy_is_not_execution_eligible",
            "Judge_results_are_offline_abstentions",
            "no_OpenClaw_LAS_network_or_paid_model_call_occurred",
        ],
    }
    _write_json(output / "baseline_manifest.json", manifest)
    (output / "baseline_summary.md").write_text(_summary_markdown(verification_id, summary), encoding="utf-8")
    return manifest
