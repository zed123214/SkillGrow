"""Strict result schema for an evidence-citing LLM Judge."""
from __future__ import annotations

import math
import re
from typing import Any


JUDGE_RESULT_SCHEMA_VERSION = "findoc.judge_result.v1"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$")
_DIMENSIONS = {"reading_order", "table_structure", "evidence_alignment"}
_STATUSES = {"success", "failed", "skipped", "abstained"}
_VERDICTS = {"pass", "warn", "fail", "abstain", "not_run", "error"}
_TOP_KEYS = {
    "schema_version",
    "run_id",
    "document_id",
    "input_hash",
    "config_hash",
    "status",
    "verdict",
    "dimensions",
    "confidence",
    "evidence_citations",
    "deductions",
    "abstain_reason",
    "failure_reason",
    "provider",
    "prompt_hash",
    "rubric_hash",
    "evidence_pack_hash",
    "latency_ms",
    "tokens",
    "retry",
    "cost",
    "timestamp",
}


class JudgeResultValidationError(ValueError):
    """Raised when a Judge result or its citations are invalid."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise JudgeResultValidationError(message)


def _sha(value: object) -> bool:
    return isinstance(value, str) and bool(_SHA256_RE.fullmatch(value))


def _non_negative_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value)) and value >= 0


def validate_judge_result(value: object, *, evidence_ids: set[str] | None = None) -> dict[str, Any]:
    _require(isinstance(value, dict), "Judge result must be an object")
    _require(set(value) == _TOP_KEYS, "Judge result top-level keys do not match the schema")
    _require(value["schema_version"] == JUDGE_RESULT_SCHEMA_VERSION, "unsupported Judge result schema_version")
    for field in ("run_id", "document_id"):
        _require(isinstance(value[field], str) and bool(_ID_RE.fullmatch(value[field])), f"{field} is invalid")
    for field in ("input_hash", "config_hash", "prompt_hash", "rubric_hash", "evidence_pack_hash"):
        _require(_sha(value[field]), f"{field} must be SHA-256")
    status = value["status"]
    verdict = value["verdict"]
    _require(status in _STATUSES, "Judge status is invalid")
    _require(verdict in _VERDICTS, "Judge verdict is invalid")
    allowed_verdict = {
        "success": {"pass", "warn", "fail"},
        "failed": {"error"},
        "skipped": {"not_run"},
        "abstained": {"abstain"},
    }
    _require(verdict in allowed_verdict[status], "Judge status and verdict are inconsistent")

    dimensions = value["dimensions"]
    _require(isinstance(dimensions, dict) and set(dimensions) == _DIMENSIONS, "Judge dimensions are invalid")
    for name, dimension in dimensions.items():
        _require(isinstance(dimension, dict) and set(dimension) == {"score", "reason"}, f"dimension {name} shape is invalid")
        score = dimension["score"]
        _require(score is None or (_non_negative_number(score) and score <= 1), f"dimension {name} score is invalid")
        _require(isinstance(dimension["reason"], str), f"dimension {name} reason must be text")
        if status == "success":
            _require(score is not None, "successful Judge dimensions require scores")
        else:
            _require(score is None, "non-success Judge dimensions must not claim scores")

    confidence = value["confidence"]
    _require(confidence is None or (_non_negative_number(confidence) and confidence <= 1), "confidence is invalid")
    if status == "success":
        _require(confidence is not None, "successful Judge result requires confidence")
    else:
        _require(confidence is None, "non-success Judge result must not claim confidence")
    citations = value["evidence_citations"]
    _require(isinstance(citations, list) and all(isinstance(item, str) for item in citations), "evidence_citations must be strings")
    _require(len(citations) == len(set(citations)), "evidence citations must be unique")
    if evidence_ids is not None:
        _require(set(citations) <= evidence_ids, "Judge result cites an unknown evidence_id")
    if status == "success":
        _require(bool(citations), "successful Judge result requires at least one evidence citation")
    else:
        _require(not citations, "non-success Judge result cannot retain citations")

    deductions = value["deductions"]
    _require(isinstance(deductions, list), "deductions must be a list")
    for deduction in deductions:
        _require(isinstance(deduction, dict) and set(deduction) == {"dimension", "points", "reason", "evidence_ids"}, "deduction shape is invalid")
        _require(deduction["dimension"] in _DIMENSIONS, "deduction dimension is invalid")
        _require(_non_negative_number(deduction["points"]) and deduction["points"] <= 1, "deduction points are invalid")
        _require(isinstance(deduction["reason"], str), "deduction reason must be text")
        _require(isinstance(deduction["evidence_ids"], list) and set(deduction["evidence_ids"]) <= set(citations), "deduction evidence must be cited")
    if status != "success":
        _require(not deductions, "non-success Judge result cannot retain deductions")

    _require(value["abstain_reason"] is None or isinstance(value["abstain_reason"], str), "abstain_reason is invalid")
    _require(value["failure_reason"] is None or isinstance(value["failure_reason"], str), "failure_reason is invalid")
    _require((status == "abstained") == bool(value["abstain_reason"]), "abstained status requires only abstain_reason")
    _require((status == "failed") == bool(value["failure_reason"]), "failed status requires only failure_reason")

    provider = value["provider"]
    _require(isinstance(provider, dict) and set(provider) == {"name", "model", "model_version"}, "provider audit shape is invalid")
    _require(all(item is None or isinstance(item, str) for item in provider.values()), "provider audit values must be text or null")
    if status == "success":
        _require(bool(provider["name"]) and bool(provider["model"]), "successful Judge result requires provider and model audit fields")
    _require(_non_negative_number(value["latency_ms"]), "latency_ms is invalid")
    tokens = value["tokens"]
    _require(isinstance(tokens, dict) and set(tokens) == {"input", "output", "total"}, "tokens shape is invalid")
    _require(all(item is None or (isinstance(item, int) and not isinstance(item, bool) and item >= 0) for item in tokens.values()), "token counts are invalid")
    if all(item is not None for item in tokens.values()):
        _require(tokens["total"] == tokens["input"] + tokens["output"], "token totals do not balance")
    retry = value["retry"]
    _require(isinstance(retry, dict) and set(retry) == {"attempt", "max_attempts"}, "retry shape is invalid")
    _require(isinstance(retry["max_attempts"], int) and not isinstance(retry["max_attempts"], bool) and retry["max_attempts"] >= 1, "retry max_attempts is invalid")
    _require(isinstance(retry["attempt"], int) and 1 <= retry["attempt"] <= retry["max_attempts"], "retry attempt is invalid")
    cost = value["cost"]
    _require(isinstance(cost, dict) and set(cost) == {"amount", "currency"}, "cost shape is invalid")
    _require(cost["amount"] is None or _non_negative_number(cost["amount"]), "cost amount is invalid")
    _require(cost["currency"] is None or isinstance(cost["currency"], str), "cost currency is invalid")
    _require(isinstance(value["timestamp"], str) and value["timestamp"], "timestamp is required")
    return value
