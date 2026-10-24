"""Read only active, hash-matching artifacts named by the run manifest."""
from __future__ import annotations

from typing import Any

from findoc.evidence.models import EVIDENCE_PACK_SCHEMA_VERSION, validate_evidence_pack
from findoc.judge.models import JUDGE_RESULT_SCHEMA_VERSION, validate_judge_result
from findoc.models.document_ir import DOCUMENT_IR_SCHEMA_VERSION, validate_document_ir
from findoc.quality.gate import QUALITY_DECISION_SCHEMA_VERSION, validate_human_review_request, validate_quality_decision
from findoc.runtime.manifest_runtime import ManifestRuntime


def build_manifest_bundle(runtime: ManifestRuntime, *, document_id: str) -> dict[str, Any]:
    """Build a safe dashboard/report bundle without directory globbing."""
    document_ir = runtime.load_current_json("document_ir", document_id=document_id, schema_version=DOCUMENT_IR_SCHEMA_VERSION)
    evidence_pack = runtime.load_current_json("evidence_pack", document_id=document_id, schema_version=EVIDENCE_PACK_SCHEMA_VERSION)
    judge_result = runtime.load_current_json("judge_result", document_id=document_id, schema_version=JUDGE_RESULT_SCHEMA_VERSION)
    quality_decision = runtime.load_current_json("quality_decision", document_id=document_id, schema_version=QUALITY_DECISION_SCHEMA_VERSION)
    validate_document_ir(document_ir)
    validate_evidence_pack(evidence_pack)
    evidence_ids = {entry["evidence_id"] for entry in evidence_pack["entries"]}
    validate_judge_result(judge_result, evidence_ids=evidence_ids)
    validate_quality_decision(quality_decision)
    review = None
    if quality_decision["route"] == "human_review":
        review = runtime.load_current_json("human_review_request", document_id=document_id, schema_version="findoc.human_review_request.v1")
        validate_human_review_request(review, decision=quality_decision)
    return {
        "run_id": runtime.manifest["run_id"],
        "document_id": document_id,
        "state": runtime.manifest["state"],
        "document_ir": document_ir,
        "evidence_pack": evidence_pack,
        "judge_result": judge_result,
        "quality_decision": quality_decision,
        "human_review_request": review,
    }


def render_run_report(bundle: dict[str, Any]) -> str:
    decision = bundle["quality_decision"]
    coverage = bundle["evidence_pack"]["coverage"]
    judge = bundle["judge_result"]
    lines = [
        f"# Findoc run {bundle['run_id']}",
        "",
        f"- document_id: `{bundle['document_id']}`",
        f"- state: `{bundle['state']}`",
        f"- quality decision: `{decision['decision']}`",
        f"- route: `{decision['route']}`",
        f"- evidence coverage: `{coverage['evidenced']}/{coverage['eligible']}` ({coverage['ratio']:.3f})",
        f"- Judge status: `{judge['status']}`",
        "- Ground Truth note: this report does not convert DocumentIR, Evidence Pack, or Judge output into GT.",
        "",
        "## Reasons",
        "",
    ]
    lines.extend(f"- {reason}" for reason in decision["reasons"])
    if bundle["human_review_request"]:
        lines.extend(
            [
                "",
                "## Human review",
                "",
                "A local `human_review_request.json` artifact was created. No external ticketing integration is claimed.",
            ]
        )
    return "\n".join(lines) + "\n"
