"""Strict, dependency-free Evidence Pack schema."""
from __future__ import annotations

import math
import re
from typing import Any


EVIDENCE_PACK_SCHEMA_VERSION = "findoc.evidence_pack.v1"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$")
_EVIDENCE_ID_RE = re.compile(r"^ev_[0-9a-f]{24}$")
_SOURCE_KINDS = {"raw_source", "sut_derived", "manual", "synthetic_oracle"}
_REDACTION_STATES = {"not_required", "redacted", "withheld"}
_ENTRY_TYPES = {"page", "table", "metric", "text"}
_TOP_KEYS = {
    "schema_version",
    "run_id",
    "document_id",
    "input_hash",
    "config_hash",
    "document_ir_hash",
    "created_at",
    "entries",
    "coverage",
    "redaction_state",
    "provenance",
}
_ENTRY_KEYS = {
    "evidence_id",
    "page",
    "type",
    "source_kind",
    "span",
    "table",
    "text",
    "source_artifact_hash",
    "provenance",
    "redaction_state",
}


class EvidencePackValidationError(ValueError):
    """Raised when an Evidence Pack violates the strict contract."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise EvidencePackValidationError(message)


def _is_sha(value: object) -> bool:
    return isinstance(value, str) and bool(_SHA256_RE.fullmatch(value))


def _json_value(value: object) -> bool:
    if value is None or isinstance(value, (str, bool, int)):
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, list):
        return all(_json_value(item) for item in value)
    if isinstance(value, dict):
        return all(isinstance(key, str) and _json_value(item) for key, item in value.items())
    return False


def validate_evidence_pack(value: object) -> dict[str, Any]:
    _require(isinstance(value, dict), "Evidence Pack must be an object")
    _require(set(value) == _TOP_KEYS, "Evidence Pack top-level keys do not match the schema")
    _require(value["schema_version"] == EVIDENCE_PACK_SCHEMA_VERSION, "unsupported Evidence Pack schema_version")
    _require(isinstance(value["run_id"], str) and bool(_ID_RE.fullmatch(value["run_id"])), "run_id is invalid")
    _require(isinstance(value["document_id"], str) and bool(_ID_RE.fullmatch(value["document_id"])), "document_id is invalid")
    for field in ("input_hash", "config_hash", "document_ir_hash"):
        _require(_is_sha(value[field]), f"{field} must be SHA-256")
    _require(isinstance(value["created_at"], str) and value["created_at"], "created_at is required")
    _require(value["redaction_state"] in _REDACTION_STATES, "redaction_state is invalid")
    _require(isinstance(value["provenance"], dict) and _json_value(value["provenance"]), "provenance must be JSON-safe")

    entries = value["entries"]
    _require(isinstance(entries, list), "entries must be a list")
    seen: set[str] = set()
    for entry in entries:
        _require(isinstance(entry, dict) and set(entry) == _ENTRY_KEYS, "evidence entry shape is invalid")
        evidence_id = entry["evidence_id"]
        _require(isinstance(evidence_id, str) and bool(_EVIDENCE_ID_RE.fullmatch(evidence_id)), "evidence_id is invalid")
        _require(evidence_id not in seen, "evidence_id must be unique")
        seen.add(evidence_id)
        _require(entry["page"] is None or (isinstance(entry["page"], int) and not isinstance(entry["page"], bool) and entry["page"] >= 0), "page must be non-negative or null")
        _require(entry["type"] in _ENTRY_TYPES, "evidence type is invalid")
        _require(entry["source_kind"] in _SOURCE_KINDS, "source_kind is invalid")
        _require(entry["redaction_state"] in _REDACTION_STATES, "entry redaction_state is invalid")
        _require(_is_sha(entry["source_artifact_hash"]), "source_artifact_hash must be SHA-256")
        _require(isinstance(entry["provenance"], dict) and _json_value(entry["provenance"]), "entry provenance must be JSON-safe")
        payloads = (entry["span"], entry["table"], entry["text"])
        _require(sum(item is not None for item in payloads) == 1, "exactly one of span, table, or text must be populated")
        _require(all(_json_value(item) for item in payloads), "evidence payload must be JSON-safe")

    coverage = value["coverage"]
    _require(isinstance(coverage, dict) and set(coverage) == {"eligible", "evidenced", "missing", "ratio", "missing_reasons"}, "coverage shape is invalid")
    for field in ("eligible", "evidenced", "missing"):
        _require(isinstance(coverage[field], int) and not isinstance(coverage[field], bool) and coverage[field] >= 0, f"coverage.{field} must be non-negative")
    _require(coverage["eligible"] == coverage["evidenced"] + coverage["missing"], "coverage counts do not balance")
    _require(isinstance(coverage["ratio"], (int, float)) and not isinstance(coverage["ratio"], bool) and 0 <= coverage["ratio"] <= 1, "coverage.ratio is invalid")
    expected_ratio = 1.0 if coverage["eligible"] == 0 else coverage["evidenced"] / coverage["eligible"]
    _require(abs(float(coverage["ratio"]) - expected_ratio) < 1e-9, "coverage.ratio does not match counts")
    _require(isinstance(coverage["missing_reasons"], list) and all(isinstance(item, str) for item in coverage["missing_reasons"]), "coverage.missing_reasons must be strings")
    _require(coverage["evidenced"] == len(entries), "coverage.evidenced must equal entry count")
    return value
