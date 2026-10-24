"""Versioned quality policy with deterministic and GT precedence."""
from __future__ import annotations

import re
from typing import Any

from findoc.evidence.models import validate_evidence_pack
from findoc.judge.models import validate_judge_result
from findoc.runtime.run_store import canonical_sha256, utc_now


QUALITY_DECISION_SCHEMA_VERSION = "findoc.quality_decision.v1"
HUMAN_REVIEW_SCHEMA_VERSION = "findoc.human_review_request.v1"
DEFAULT_POLICY = {
    "schema_version": "findoc.quality_policy.v1",
    "policy_version": "2026-07-30.v1",
    "minimum_evidence_coverage": 0.9,
    "minimum_gt_score": 0.95,
    "judge_failure_is_degraded": True,
    "high_confidence_judge_fail_review": 0.8,
}
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_DECISIONS = {"accepted", "rejected", "needs_review", "degraded"}
_ROUTES = {"complete", "stop", "human_review", "degraded_complete"}
_CHECK_STATES = {"pass", "fail", "unknown"}


class QualityDecisionValidationError(ValueError):
    """Raised for invalid policies or quality artifacts."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise QualityDecisionValidationError(message)


def _is_hash(value: object) -> bool:
    return isinstance(value, str) and bool(_SHA256_RE.fullmatch(value))


def _policy(value: dict[str, Any] | None) -> dict[str, Any]:
    resolved = dict(DEFAULT_POLICY)
    if value:
        unknown = set(value) - set(DEFAULT_POLICY)
        _require(not unknown, "quality policy contains unsupported fields")
        resolved.update(value)
    _require(resolved["schema_version"] == "findoc.quality_policy.v1", "unsupported quality policy schema")
    _require(isinstance(resolved["policy_version"], str) and resolved["policy_version"], "policy_version is required")
    for field in ("minimum_evidence_coverage", "minimum_gt_score", "high_confidence_judge_fail_review"):
        _require(isinstance(resolved[field], (int, float)) and not isinstance(resolved[field], bool) and 0 <= resolved[field] <= 1, f"{field} is invalid")
    _require(isinstance(resolved["judge_failure_is_degraded"], bool), "judge_failure_is_degraded must be boolean")
    return resolved


def resolve_quality_policy(value: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return the complete policy that must be included in the run config hash."""
    return _policy(value)


def _validated_gt(value: dict[str, Any] | None) -> dict[str, Any]:
    gt = value or {
        "eligible": False,
        "score": None,
        "source_kind": "no_gt",
        "reason": "no_execution_eligible_ground_truth",
    }
    _require(isinstance(gt, dict) and set(gt) == {"eligible", "score", "source_kind", "reason"}, "GT assessment shape is invalid")
    _require(isinstance(gt["eligible"], bool), "GT eligibility must be boolean")
    _require(gt["score"] is None or (isinstance(gt["score"], (int, float)) and not isinstance(gt["score"], bool) and 0 <= gt["score"] <= 1), "GT score is invalid")
    _require(not gt["eligible"] or gt["score"] is not None, "eligible GT requires a score")
    _require(isinstance(gt["source_kind"], str) and isinstance(gt["reason"], str), "GT source/reason must be text")
    return dict(gt)


def validate_quality_decision(value: object) -> dict[str, Any]:
    keys = {
        "schema_version",
        "run_id",
        "document_id",
        "input_hash",
        "config_hash",
        "policy_version",
        "policy_hash",
        "decision",
        "route",
        "reasons",
        "critical_checks",
        "gt_assessment",
        "evidence_coverage",
        "judge_status",
        "evidence_ids",
        "created_at",
    }
    _require(isinstance(value, dict) and set(value) == keys, "quality decision shape is invalid")
    _require(value["schema_version"] == QUALITY_DECISION_SCHEMA_VERSION, "unsupported quality decision schema")
    for field in ("input_hash", "config_hash", "policy_hash"):
        _require(_is_hash(value[field]), f"{field} must be SHA-256")
    _require(value["decision"] in _DECISIONS, "quality decision is invalid")
    _require(value["route"] in _ROUTES, "quality route is invalid")
    expected_route = {
        "accepted": "complete",
        "rejected": "stop",
        "needs_review": "human_review",
        "degraded": "degraded_complete",
    }
    _require(value["route"] == expected_route[value["decision"]], "quality decision and route are inconsistent")
    _require(isinstance(value["reasons"], list) and all(isinstance(item, str) for item in value["reasons"]), "quality reasons must be text")
    checks = value["critical_checks"]
    _require(isinstance(checks, dict) and checks and all(isinstance(key, str) and state in _CHECK_STATES for key, state in checks.items()), "critical checks are invalid")
    gt = value["gt_assessment"]
    _require(isinstance(gt, dict) and set(gt) == {"eligible", "score", "source_kind", "reason"}, "GT assessment shape is invalid")
    _require(isinstance(gt["eligible"], bool), "GT eligibility must be boolean")
    _require(gt["score"] is None or (isinstance(gt["score"], (int, float)) and not isinstance(gt["score"], bool) and 0 <= gt["score"] <= 1), "GT score is invalid")
    _require(not gt["eligible"] or gt["score"] is not None, "eligible GT requires a score")
    _require(isinstance(gt["source_kind"], str) and isinstance(gt["reason"], str), "GT source/reason must be text")
    coverage = value["evidence_coverage"]
    _require(isinstance(coverage, dict) and set(coverage) == {"eligible", "evidenced", "missing", "ratio"}, "evidence coverage shape is invalid")
    _require(isinstance(value["judge_status"], str), "judge_status is required")
    _require(isinstance(value["evidence_ids"], list) and all(isinstance(item, str) for item in value["evidence_ids"]), "evidence_ids must be text")
    _require(isinstance(value["created_at"], str) and value["created_at"], "created_at is required")
    return value


def evaluate_quality(
    *,
    evidence_pack: dict[str, Any],
    judge_result: dict[str, Any],
    critical_checks: dict[str, str],
    gt_assessment: dict[str, Any] | None = None,
    policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Apply deterministic/GT precedence; unknown is never treated as pass."""
    validate_evidence_pack(evidence_pack)
    evidence_ids = {entry["evidence_id"] for entry in evidence_pack["entries"]}
    validate_judge_result(judge_result, evidence_ids=evidence_ids)
    resolved = _policy(policy)
    if not critical_checks or any(state not in _CHECK_STATES for state in critical_checks.values()):
        raise QualityDecisionValidationError("critical checks must use pass/fail/unknown")
    gt = _validated_gt(gt_assessment)
    reasons: list[str] = []
    decision = "accepted"
    if any(state == "fail" for state in critical_checks.values()):
        decision = "rejected"
        reasons.append("deterministic_critical_check_failed")
    elif any(state == "unknown" for state in critical_checks.values()):
        decision = "needs_review"
        reasons.append("deterministic_critical_check_unknown")
    elif gt.get("eligible") and gt.get("score") < resolved["minimum_gt_score"]:
        decision = "rejected"
        reasons.append("eligible_ground_truth_below_threshold")
    elif evidence_pack["coverage"]["ratio"] < resolved["minimum_evidence_coverage"]:
        decision = "needs_review"
        reasons.append("evidence_coverage_below_threshold")
    elif judge_result["status"] == "success" and judge_result["verdict"] == "fail" and judge_result["confidence"] >= resolved["high_confidence_judge_fail_review"]:
        decision = "needs_review"
        reasons.append("high_confidence_judge_disagreement")
    elif judge_result["status"] == "failed" and resolved["judge_failure_is_degraded"]:
        decision = "degraded"
        reasons.append("optional_judge_failed")
    else:
        if judge_result["status"] in {"abstained", "skipped"}:
            reasons.append("judge_not_used_for_acceptance")
        reasons.append("deterministic_and_available_evidence_checks_passed")
    route = {
        "accepted": "complete",
        "rejected": "stop",
        "needs_review": "human_review",
        "degraded": "degraded_complete",
    }[decision]
    result = {
        "schema_version": QUALITY_DECISION_SCHEMA_VERSION,
        "run_id": evidence_pack["run_id"],
        "document_id": evidence_pack["document_id"],
        "input_hash": evidence_pack["input_hash"],
        "config_hash": evidence_pack["config_hash"],
        "policy_version": resolved["policy_version"],
        "policy_hash": canonical_sha256(resolved),
        "decision": decision,
        "route": route,
        "reasons": reasons,
        "critical_checks": dict(sorted(critical_checks.items())),
        "gt_assessment": gt,
        "evidence_coverage": {
            key: evidence_pack["coverage"][key]
            for key in ("eligible", "evidenced", "missing", "ratio")
        },
        "judge_status": judge_result["status"],
        "evidence_ids": sorted(evidence_ids),
        "created_at": utc_now(),
    }
    return validate_quality_decision(result)


def make_human_review_request(decision: dict[str, Any]) -> dict[str, Any] | None:
    """Create an artifact only; no external ticketing integration is claimed."""
    validate_quality_decision(decision)
    if decision["route"] != "human_review":
        return None
    request = {
        "schema_version": HUMAN_REVIEW_SCHEMA_VERSION,
        "run_id": decision["run_id"],
        "originating_run_id": decision["run_id"],
        "document_id": decision["document_id"],
        "input_hash": decision["input_hash"],
        "config_hash": decision["config_hash"],
        "review_reason": list(decision["reasons"]),
        "severity": "high" if "deterministic_critical_check_unknown" in decision["reasons"] else "medium",
        "evidence_ids": list(decision["evidence_ids"]),
        "required_action": "review cited evidence and record an independent disposition",
        "redacted_context": {
            "decision": decision["decision"],
            "judge_status": decision["judge_status"],
            "evidence_coverage": decision["evidence_coverage"],
        },
        "status": "open",
        "created_at": utc_now(),
    }
    return validate_human_review_request(request, decision=decision)


def validate_human_review_request(value: object, *, decision: dict[str, Any] | None = None) -> dict[str, Any]:
    keys = {
        "schema_version",
        "run_id",
        "originating_run_id",
        "document_id",
        "input_hash",
        "config_hash",
        "review_reason",
        "severity",
        "evidence_ids",
        "required_action",
        "redacted_context",
        "status",
        "created_at",
    }
    _require(isinstance(value, dict) and set(value) == keys, "human review request shape is invalid")
    _require(value["schema_version"] == HUMAN_REVIEW_SCHEMA_VERSION, "unsupported human review request schema")
    _require(value["originating_run_id"] == value["run_id"], "human review originating run is invalid")
    _require(_is_hash(value["input_hash"]) and _is_hash(value["config_hash"]), "human review hash binding is invalid")
    _require(isinstance(value["review_reason"], list) and bool(value["review_reason"]) and all(isinstance(item, str) for item in value["review_reason"]), "human review reasons are invalid")
    _require(value["severity"] in {"low", "medium", "high"}, "human review severity is invalid")
    _require(isinstance(value["evidence_ids"], list) and all(isinstance(item, str) for item in value["evidence_ids"]), "human review evidence IDs are invalid")
    _require(isinstance(value["required_action"], str) and bool(value["required_action"]), "human review required action is invalid")
    context = value["redacted_context"]
    _require(isinstance(context, dict) and set(context) == {"decision", "judge_status", "evidence_coverage"}, "human review redacted context is invalid")
    _require(value["status"] in {"open", "resolved", "cancelled"}, "human review status is invalid")
    _require(isinstance(value["created_at"], str) and value["created_at"], "human review created_at is required")
    if decision is not None:
        validate_quality_decision(decision)
        for field in ("run_id", "document_id", "input_hash", "config_hash"):
            _require(value[field] == decision[field], f"human review {field} does not match quality decision")
        _require(set(value["evidence_ids"]) <= set(decision["evidence_ids"]), "human review evidence IDs are not in the quality decision")
        _require(value["review_reason"] == decision["reasons"], "human review reasons do not match quality decision")
        _require(value["redacted_context"]["decision"] == decision["decision"], "human review context decision is stale")
    return value
