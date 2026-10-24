"""Evidence-bound Judge runner with explicit abstain/failure paths."""
from __future__ import annotations

import hashlib
import json
import time
from typing import Any, Protocol

from findoc.evidence.models import validate_evidence_pack
from findoc.judge.models import JUDGE_RESULT_SCHEMA_VERSION, JudgeResultValidationError, validate_judge_result
from findoc.runtime.run_store import utc_now
from framework.sanitization import sanitize_exception_message


JUDGE_RUBRIC = "reading_order; table_structure; evidence_alignment; citations_required; ground_truth_unchanged"


class JudgeProvider(Protocol):
    """Provider boundary; concrete network clients live outside core workflow."""

    name: str
    model: str
    model_version: str | None

    def evaluate(self, payload: dict[str, Any]) -> dict[str, Any]: ...


def _sha(value: object) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _empty_dimensions(reason: str) -> dict[str, dict[str, Any]]:
    return {
        "reading_order": {"score": None, "reason": reason},
        "table_structure": {"score": None, "reason": reason},
        "evidence_alignment": {"score": None, "reason": reason},
    }


def _base(
    pack: dict[str, Any],
    *,
    evidence_pack_hash: str,
    prompt_hash: str,
    rubric_hash: str,
    latency_ms: float,
    attempt: int,
    max_attempts: int,
    provider: JudgeProvider | None,
) -> dict[str, Any]:
    return {
        "schema_version": JUDGE_RESULT_SCHEMA_VERSION,
        "run_id": pack["run_id"],
        "document_id": pack["document_id"],
        "input_hash": pack["input_hash"],
        "config_hash": pack["config_hash"],
        "provider": {
            "name": getattr(provider, "name", None),
            "model": getattr(provider, "model", None),
            "model_version": getattr(provider, "model_version", None),
        },
        "prompt_hash": prompt_hash,
        "rubric_hash": rubric_hash,
        "evidence_pack_hash": evidence_pack_hash,
        "latency_ms": latency_ms,
        "tokens": {"input": None, "output": None, "total": None},
        "retry": {"attempt": attempt, "max_attempts": max_attempts},
        "cost": {"amount": None, "currency": None},
        "timestamp": utc_now(),
    }


def _non_success(
    pack: dict[str, Any],
    *,
    status: str,
    reason: str,
    evidence_pack_hash: str,
    prompt_hash: str,
    rubric_hash: str,
    latency_ms: float,
    attempt: int,
    max_attempts: int,
    provider: JudgeProvider | None,
) -> dict[str, Any]:
    result = {
        **_base(
            pack,
            evidence_pack_hash=evidence_pack_hash,
            prompt_hash=prompt_hash,
            rubric_hash=rubric_hash,
            latency_ms=latency_ms,
            attempt=attempt,
            max_attempts=max_attempts,
            provider=provider,
        ),
        "status": status,
        "verdict": {"failed": "error", "skipped": "not_run", "abstained": "abstain"}[status],
        "dimensions": _empty_dimensions(reason),
        "confidence": None,
        "evidence_citations": [],
        "deductions": [],
        "abstain_reason": reason if status == "abstained" else None,
        "failure_reason": reason if status == "failed" else None,
    }
    return validate_judge_result(result, evidence_ids={entry["evidence_id"] for entry in pack["entries"]})


def run_structured_judge(
    evidence_pack: dict[str, Any],
    *,
    evidence_pack_hash: str,
    provider: JudgeProvider | None = None,
    minimum_coverage: float = 0.8,
    attempt: int = 1,
    max_attempts: int = 1,
) -> dict[str, Any]:
    """Run or abstain; never silently coerce invalid provider output to success."""
    validate_evidence_pack(evidence_pack)
    prompt_payload = {
        "rubric": JUDGE_RUBRIC,
        "run_id": evidence_pack["run_id"],
        "document_id": evidence_pack["document_id"],
        "input_hash": evidence_pack["input_hash"],
        "evidence": evidence_pack["entries"],
    }
    prompt_hash = _sha(prompt_payload)
    rubric_hash = hashlib.sha256(JUDGE_RUBRIC.encode("utf-8")).hexdigest()
    started = time.perf_counter()
    if evidence_pack["coverage"]["ratio"] < minimum_coverage or not evidence_pack["entries"]:
        return _non_success(
            evidence_pack,
            status="abstained",
            reason="insufficient_evidence_coverage",
            evidence_pack_hash=evidence_pack_hash,
            prompt_hash=prompt_hash,
            rubric_hash=rubric_hash,
            latency_ms=(time.perf_counter() - started) * 1000,
            attempt=attempt,
            max_attempts=max_attempts,
            provider=provider,
        )
    if provider is None:
        return _non_success(
            evidence_pack,
            status="abstained",
            reason="live_judge_not_authorized",
            evidence_pack_hash=evidence_pack_hash,
            prompt_hash=prompt_hash,
            rubric_hash=rubric_hash,
            latency_ms=(time.perf_counter() - started) * 1000,
            attempt=attempt,
            max_attempts=max_attempts,
            provider=None,
        )
    try:
        response = provider.evaluate(prompt_payload)
        latency_ms = (time.perf_counter() - started) * 1000
        if not isinstance(response, dict):
            raise JudgeResultValidationError("provider response must be an object")
        usage = response.get("tokens") or {"input": None, "output": None, "total": None}
        cost = response.get("cost") or {"amount": None, "currency": None}
        result = {
            **_base(
                evidence_pack,
                evidence_pack_hash=evidence_pack_hash,
                prompt_hash=prompt_hash,
                rubric_hash=rubric_hash,
                latency_ms=latency_ms,
                attempt=attempt,
                max_attempts=max_attempts,
                provider=provider,
            ),
            "status": "success",
            "verdict": response.get("verdict"),
            "dimensions": response.get("dimensions"),
            "confidence": response.get("confidence"),
            "evidence_citations": response.get("evidence_citations"),
            "deductions": response.get("deductions", []),
            "abstain_reason": None,
            "failure_reason": None,
            "tokens": usage,
            "cost": cost,
        }
        return validate_judge_result(result, evidence_ids={entry["evidence_id"] for entry in evidence_pack["entries"]})
    except Exception as exc:
        return _non_success(
            evidence_pack,
            status="failed",
            reason="invalid_or_failed_provider_result:" + sanitize_exception_message(exc, max_chars=128),
            evidence_pack_hash=evidence_pack_hash,
            prompt_hash=prompt_hash,
            rubric_hash=rubric_hash,
            latency_ms=(time.perf_counter() - started) * 1000,
            attempt=attempt,
            max_attempts=max_attempts,
            provider=provider,
        )
