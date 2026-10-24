"""Build content-addressed evidence from a validated DocumentIR."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from findoc.evidence.models import EVIDENCE_PACK_SCHEMA_VERSION, validate_evidence_pack
from findoc.models.document_ir import validate_document_ir
from findoc.runtime.run_store import utc_now


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _evidence_id(payload: object) -> str:
    return "ev_" + hashlib.sha256(_canonical(payload)).hexdigest()[:24]


def _entry(
    *,
    page: int | None,
    evidence_type: str,
    payload_kind: str,
    payload: object,
    source_artifact_hash: str,
    provenance: dict[str, Any],
) -> dict[str, Any]:
    identity = {
        "page": page,
        "type": evidence_type,
        "payload_kind": payload_kind,
        "payload": payload,
        "source_artifact_hash": source_artifact_hash,
        "provenance": provenance,
    }
    return {
        "evidence_id": _evidence_id(identity),
        "page": page,
        "type": evidence_type,
        "source_kind": "sut_derived",
        "span": payload if payload_kind == "span" else None,
        "table": payload if payload_kind == "table" else None,
        "text": payload if payload_kind == "text" else None,
        "source_artifact_hash": source_artifact_hash,
        "provenance": provenance,
        "redaction_state": "not_required",
    }


def build_evidence_pack(
    document_ir: dict[str, Any],
    *,
    document_ir_hash: str,
    config_hash: str,
) -> dict[str, Any]:
    """Build citations without treating SUT-derived content as Ground Truth."""
    validate_document_ir(document_ir)
    source_hashes = document_ir["provenance"]["source_artifact_hashes"]
    common_provenance = {
        "adapter": document_ir["provenance"]["adapter"],
        "source_kind": document_ir["provenance"]["source_kind"],
        "input_binding": document_ir["provenance"]["input_binding"],
    }
    entries: list[dict[str, Any]] = []
    missing_reasons: list[str] = []

    declared_pages = document_ir["page_count"] or 0
    if document_ir["pages"]["available"]:
        pages_hash = source_hashes.get("raw/pages_detail.json")
        if pages_hash:
            for page in document_ir["pages"]["items"]:
                entries.append(
                    _entry(
                        page=page["source_page_number"],
                        evidence_type="page",
                        payload_kind="span",
                        payload={
                            "source_page_index": page["source_page_index"],
                            "source_page_number": page["source_page_number"],
                            "block_count": page["block_count"],
                        },
                        source_artifact_hash=pages_hash,
                        provenance=common_provenance,
                    )
                )
    elif declared_pages:
        missing_reasons.append("page_evidence_unavailable")

    table_hash = source_hashes["normalized/normalized_tables.json"]
    for table in document_ir["normalized_tables"]:
        entries.append(
            _entry(
                page=None,
                evidence_type="table",
                payload_kind="table",
                payload=table,
                source_artifact_hash=table_hash,
                provenance=common_provenance,
            )
        )

    metric_hash = source_hashes["normalized/financial_summary.json"]
    for metric in document_ir["financial_metrics"]:
        entries.append(
            _entry(
                page=None,
                evidence_type="metric",
                payload_kind="table",
                payload={"metric": metric},
                source_artifact_hash=metric_hash,
                provenance=common_provenance,
            )
        )

    eligible = declared_pages + len(document_ir["normalized_tables"]) + len(document_ir["financial_metrics"])
    evidenced = len(entries)
    missing = max(eligible - evidenced, 0)
    pack = {
        "schema_version": EVIDENCE_PACK_SCHEMA_VERSION,
        "run_id": document_ir["run_id"],
        "document_id": document_ir["document_id"],
        "input_hash": document_ir["input"]["sha256"],
        "config_hash": config_hash,
        "document_ir_hash": document_ir_hash,
        "created_at": utc_now(),
        "entries": entries,
        "coverage": {
            "eligible": eligible,
            "evidenced": evidenced,
            "missing": missing,
            "ratio": 1.0 if eligible == 0 else evidenced / eligible,
            "missing_reasons": missing_reasons,
        },
        "redaction_state": "not_required",
        "provenance": {
            "producer": "findoc.evidence.builder.build_evidence_pack",
            "evidence_role": "citation_only_not_ground_truth",
            "source_kind": "sut_derived",
        },
    }
    return validate_evidence_pack(pack)
