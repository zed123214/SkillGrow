"""Strict, dependency-free schema validation for T1 DocumentIR artifacts."""
from __future__ import annotations

import math
import re
from typing import Any


DOCUMENT_IR_SCHEMA_VERSION = "findoc.document_ir.v1"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$")

_REQUIRED_KEYS = {
    "schema_version",
    "run_id",
    "document_id",
    "input",
    "backend",
    "execution_kind",
    "page_count",
    "pages",
    "blocks",
    "normalized_tables",
    "financial_summary",
    "financial_metrics",
    "evidence_refs",
    "warnings",
    "sanitized_errors",
    "provenance",
}
_TABLE_KEYS = {"table_id", "statement", "table_type", "columns", "rows"}
_METRIC_KEYS = {"statement", "item", "period", "value", "normalized_value", "table_id"}
_SUMMARY_KEYS = {"company", "document_type", "period", "metric_statistics"}
_METRIC_STATISTICS_KEYS = {"metric_record_count"}
_PROVENANCE_KEYS = {
    "adapter",
    "source_kind",
    "legacy_output_profile",
    "legacy_execution_backend",
    "legacy_output_source",
    "legacy_is_synthetic",
    "legacy_count_as_real_evaluation",
    "input_binding",
    "source_artifact_hashes",
    "quality_checks_sha256",
}
_REQUIRED_SOURCE_ARTIFACTS = {
    "raw/parsed.md",
    "normalized/normalized_tables.json",
    "normalized/financial_summary.json",
    "evaluation/quality_checks.json",
    "meta/run_meta.json",
}
_OPTIONAL_SOURCE_ARTIFACTS = {"raw/pages_detail.json"}
_INPUT_BINDINGS = {
    "not_verifiable_from_legacy_run_meta",
    "verified_by_legacy_input_sha256",
    "mismatch_with_legacy_input_sha256",
}


class DocumentIRValidationError(ValueError):
    """Raised when a candidate DocumentIR violates the T1 contract."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise DocumentIRValidationError(message)


def _is_non_negative_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _is_json_scalar(value: object) -> bool:
    if value is None or isinstance(value, (str, bool, int)):
        return True
    return isinstance(value, float) and math.isfinite(value)


def _validate_optional_text(value: object, field: str) -> None:
    _require(value is None or isinstance(value, str), f"{field} must be a string or null")


def _validate_availability(value: object, field: str) -> None:
    _require(isinstance(value, dict), f"{field} must be an object")
    _require(set(value) == {"available", "count", "items"}, f"{field} must contain only available, count, items")
    _require(isinstance(value["available"], bool), f"{field}.available must be boolean")
    _require(value["count"] is None or _is_non_negative_int(value["count"]), f"{field}.count must be a non-negative integer or null")
    _require(isinstance(value["items"], list), f"{field}.items must be a list")
    if value["available"]:
        _require(value["count"] == len(value["items"]), f"{field}.count must equal the number of available items")
    else:
        _require(not value["items"], f"{field}.items must be empty when unavailable")
        _require(value["count"] is None or value["count"] == 0, f"{field}.count must be null or zero when unavailable")


def _validate_page_items(items: list[object]) -> None:
    for page_index, item in enumerate(items):
        _require(isinstance(item, dict), "pages.items entries must be objects")
        _require(set(item) == {"source_page_index", "source_page_number", "block_count"}, "pages.items entries must contain only source-page metadata")
        _require(item["source_page_index"] == page_index, "pages.items.source_page_index must preserve sequential source order")
        _require(_is_non_negative_int(item["source_page_index"]), "pages.items.source_page_index must be a non-negative integer")
        _require(item["source_page_number"] is None or _is_non_negative_int(item["source_page_number"]), "pages.items.source_page_number must be a non-negative integer or null")
        _require(item["block_count"] is None or _is_non_negative_int(item["block_count"]), "pages.items.block_count must be a non-negative integer or null")


def _validate_block_items(items: list[object], page_items: list[object]) -> None:
    block_counts: dict[int, int] = {}
    valid_page_indexes = set(range(len(page_items)))
    for item in items:
        _require(isinstance(item, dict), "blocks.items entries must be objects")
        _require(set(item) == {"source_page_index", "source_block_index"}, "blocks.items entries must contain only source indexes")
        _require(_is_non_negative_int(item["source_page_index"]), "blocks.items.source_page_index must be a non-negative integer")
        _require(_is_non_negative_int(item["source_block_index"]), "blocks.items.source_block_index must be a non-negative integer")
        _require(item["source_page_index"] in valid_page_indexes, "blocks.items.source_page_index must reference an available page")
        page_index = item["source_page_index"]
        expected_block_index = block_counts.get(page_index, 0)
        _require(item["source_block_index"] == expected_block_index, "blocks.items.source_block_index must preserve sequential source order")
        block_counts[page_index] = expected_block_index + 1
    for page in page_items:
        if page["block_count"] is not None:
            _require(page["block_count"] == block_counts.get(page["source_page_index"], 0), "pages.items.block_count must match the block projection")


def _validate_tables(value: object) -> set[str]:
    _require(isinstance(value, list), "normalized_tables must be a list")
    table_ids: set[str] = set()
    for table in value:
        _require(isinstance(table, dict), "normalized_tables entries must be objects")
        _require(set(table) == _TABLE_KEYS, "normalized_tables entries must match the strict T1 table schema")
        table_id = table["table_id"]
        _require(isinstance(table_id, str) and bool(_IDENTIFIER_RE.fullmatch(table_id)), "normalized_tables.table_id must be a safe identifier")
        _require(table_id not in table_ids, "normalized_tables.table_id values must be unique")
        table_ids.add(table_id)
        _validate_optional_text(table["statement"], "normalized_tables.statement")
        _validate_optional_text(table["table_type"], "normalized_tables.table_type")
        _require(isinstance(table["columns"], list) and all(isinstance(column, str) for column in table["columns"]), "normalized_tables.columns must be a list of strings")
        _require(isinstance(table["rows"], list), "normalized_tables.rows must be a list")
        for row in table["rows"]:
            _require(isinstance(row, dict) and all(isinstance(key, str) for key in row), "normalized_tables row keys must be strings")
            _require(all(_is_json_scalar(cell) for cell in row.values()), "normalized_tables cells must be finite JSON scalars")
    return table_ids


def _validate_metrics(value: object, table_ids: set[str]) -> None:
    _require(isinstance(value, list), "financial_metrics must be a list")
    for metric in value:
        _require(isinstance(metric, dict), "financial_metrics entries must be objects")
        _require(set(metric) == _METRIC_KEYS, "financial_metrics entries must match the strict T1 metric schema")
        _validate_optional_text(metric["statement"], "financial_metrics.statement")
        _validate_optional_text(metric["item"], "financial_metrics.item")
        _validate_optional_text(metric["period"], "financial_metrics.period")
        _require(_is_json_scalar(metric["value"]), "financial_metrics.value must be a finite JSON scalar")
        _require(_is_json_scalar(metric["normalized_value"]), "financial_metrics.normalized_value must be a finite JSON scalar")
        _require(metric["table_id"] is None or (isinstance(metric["table_id"], str) and metric["table_id"] in table_ids), "financial_metrics.table_id must reference a projected table or be null")


def _validate_financial_summary(value: object, metric_count: int) -> None:
    _require(isinstance(value, dict), "financial_summary must be an object")
    _require(set(value) == _SUMMARY_KEYS, "financial_summary must match the strict T1 summary schema")
    _validate_optional_text(value["company"], "financial_summary.company")
    _validate_optional_text(value["document_type"], "financial_summary.document_type")
    _validate_optional_text(value["period"], "financial_summary.period")
    statistics = value["metric_statistics"]
    _require(isinstance(statistics, dict) and set(statistics) == _METRIC_STATISTICS_KEYS, "financial_summary.metric_statistics must contain only metric_record_count")
    _require(_is_non_negative_int(statistics["metric_record_count"]), "financial_summary.metric_statistics.metric_record_count must be a non-negative integer")
    _require(statistics["metric_record_count"] == metric_count, "financial_summary.metric_record_count must equal the metric projection size")


def _validate_provenance(value: object) -> None:
    _require(isinstance(value, dict), "provenance must be an object")
    _require(set(value) == _PROVENANCE_KEYS, "provenance must match the strict T1 provenance schema")
    _require(value["adapter"] == "legacy_standard_fixture_v1", "provenance.adapter must identify the T1 adapter")
    _require(value["source_kind"] == "fixture_replay", "provenance.source_kind must be fixture_replay")
    _require(value["legacy_output_profile"] == "framework_standard_subset_v1", "provenance.legacy_output_profile must identify the consumed subset")
    _validate_optional_text(value["legacy_execution_backend"], "provenance.legacy_execution_backend")
    _validate_optional_text(value["legacy_output_source"], "provenance.legacy_output_source")
    _require(value["legacy_is_synthetic"] is None or isinstance(value["legacy_is_synthetic"], bool), "provenance.legacy_is_synthetic must be boolean or null")
    _require(value["legacy_count_as_real_evaluation"] is None or isinstance(value["legacy_count_as_real_evaluation"], bool), "provenance.legacy_count_as_real_evaluation must be boolean or null")
    _require(value["input_binding"] in _INPUT_BINDINGS, "provenance.input_binding is unsupported")
    source_hashes = value["source_artifact_hashes"]
    _require(isinstance(source_hashes, dict), "provenance.source_artifact_hashes must be an object")
    source_paths = set(source_hashes)
    _require(_REQUIRED_SOURCE_ARTIFACTS <= source_paths <= (_REQUIRED_SOURCE_ARTIFACTS | _OPTIONAL_SOURCE_ARTIFACTS), "provenance.source_artifact_hashes contains an unsupported source artifact")
    _require(all(isinstance(path, str) and isinstance(digest, str) and bool(_SHA256_RE.fullmatch(digest)) for path, digest in source_hashes.items()), "provenance.source_artifact_hashes must contain supported paths and SHA-256 digests")
    _require(value["quality_checks_sha256"] == source_hashes["evaluation/quality_checks.json"], "provenance.quality_checks_sha256 must match the source quality-check artifact")


def validate_document_ir(value: object) -> dict[str, Any]:
    """Validate and return a strict T1 DocumentIR mapping.

    T1 stores only source-neutral summaries.  It refuses unprovenanced page or
    coordinate fields, and rejects all unknown fields until a later task
    deliberately versions the contract.
    """
    _require(isinstance(value, dict), "DocumentIR must be an object")
    _require(set(value) == _REQUIRED_KEYS, "DocumentIR top-level keys do not match the strict T1 schema")
    _require(value["schema_version"] == DOCUMENT_IR_SCHEMA_VERSION, "unsupported DocumentIR schema_version")
    _require(isinstance(value["run_id"], str) and bool(_IDENTIFIER_RE.fullmatch(value["run_id"])), "run_id must be a safe identifier")
    _require(isinstance(value["document_id"], str) and bool(_IDENTIFIER_RE.fullmatch(value["document_id"])), "document_id must be a safe identifier")

    input_info = value["input"]
    _require(isinstance(input_info, dict) and set(input_info) == {"identifier", "sha256", "size_bytes"}, "input must contain only identifier, sha256, size_bytes")
    _require(isinstance(input_info["identifier"], str) and input_info["identifier"].startswith("sha256:"), "input.identifier must be a safe SHA-256 identifier")
    _require(isinstance(input_info["sha256"], str) and bool(_SHA256_RE.fullmatch(input_info["sha256"])), "input.sha256 must be a SHA-256 digest")
    _require(input_info["identifier"] == f"sha256:{input_info['sha256'][:16]}", "input.identifier must match the input SHA-256 prefix")
    _require(_is_non_negative_int(input_info["size_bytes"]), "input.size_bytes must be a non-negative integer")

    _require(value["backend"] == "fixture", "T1 DocumentIR only supports the fixture backend")
    _require(value["execution_kind"] == "fixture_replay", "T1 DocumentIR must be marked fixture_replay")
    _require(value["page_count"] is None or _is_non_negative_int(value["page_count"]), "page_count must be a non-negative integer or null")
    _validate_availability(value["pages"], "pages")
    _validate_availability(value["blocks"], "blocks")
    page_items = value["pages"]["items"]
    block_items = value["blocks"]["items"]
    _validate_page_items(page_items)
    _validate_block_items(block_items, page_items)
    if value["blocks"]["available"]:
        _require(value["pages"]["available"], "blocks cannot be available when pages are unavailable")
    if value["pages"]["available"]:
        _require(value["page_count"] == value["pages"]["count"], "page_count must match the available page projection")

    table_ids = _validate_tables(value["normalized_tables"])
    _validate_metrics(value["financial_metrics"], table_ids)
    _validate_financial_summary(value["financial_summary"], len(value["financial_metrics"]))
    _require(value["evidence_refs"] == [], "T1 DocumentIR must not claim an unimplemented evidence pack")
    _require(isinstance(value["warnings"], list) and all(isinstance(item, str) for item in value["warnings"]), "warnings must be a list of strings")
    _require(isinstance(value["sanitized_errors"], list) and all(isinstance(item, str) for item in value["sanitized_errors"]), "sanitized_errors must be a list of strings")
    _validate_provenance(value["provenance"])
    return value
