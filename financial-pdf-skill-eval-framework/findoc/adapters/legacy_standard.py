"""Read the legacy standard output contract without invoking its backend.

This adapter is deliberately read-only.  It does not call the legacy Skill,
LAS, OpenClaw, or the legacy pipeline; it only consumes an already-present
standard-profile fixture and preserves its provenance in DocumentIR.
"""
from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from framework.sanitization import sanitize_for_public_artifact


# This is the framework's stable five-file consumption subset.  The Skill's
# display report is deliberately not an adapter input because it carries no
# additional structured source needed for T1 DocumentIR.
LEGACY_STANDARD_REQUIRED = (
    "raw/parsed.md",
    "normalized/normalized_tables.json",
    "normalized/financial_summary.json",
    "evaluation/quality_checks.json",
    "meta/run_meta.json",
)
_PAGES_DETAIL = "raw/pages_detail.json"
_LOCATION_KEYS = {"page", "page_no", "page_number", "bbox", "bounding_box", "coordinates"}
_SCALAR_TYPES = (str, int, float, bool, type(None))


class LegacyStandardAdapterError(ValueError):
    """A safe error for a legacy standard-output compatibility problem."""


@dataclass(frozen=True)
class LegacyAdaptation:
    """Read-only data extracted from one legacy standard-profile directory."""

    normalized_tables: list[dict[str, Any]]
    financial_summary: dict[str, Any]
    financial_metrics: list[dict[str, Any]]
    page_count: int | None
    pages: dict[str, Any]
    blocks: dict[str, Any]
    warnings: list[str]
    provenance: dict[str, Any]


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path, relative: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise LegacyStandardAdapterError(f"legacy artifact {relative} is not valid JSON") from exc


def _source_page_number(page: object) -> int | None:
    if not isinstance(page, dict):
        return None
    for key in ("page_number", "page_no", "page"):
        value = page.get(key)
        if isinstance(value, int) and value >= 0:
            return value
    return None


def _summarize_pages(pages_detail: list[object] | None) -> tuple[int | None, dict[str, Any], dict[str, Any]]:
    if pages_detail is None:
        return None, {"available": False, "count": None, "items": []}, {"available": False, "count": None, "items": []}

    page_items: list[dict[str, Any]] = []
    block_items: list[dict[str, int]] = []
    for page_index, page in enumerate(pages_detail):
        raw_blocks = page.get("blocks") if isinstance(page, dict) else None
        block_count = len(raw_blocks) if isinstance(raw_blocks, list) else None
        page_items.append(
            {
                "source_page_index": page_index,
                "source_page_number": _source_page_number(page),
                "block_count": block_count,
            }
        )
        if isinstance(raw_blocks, list):
            block_items.extend(
                {"source_page_index": page_index, "source_block_index": block_index}
                for block_index, _block in enumerate(raw_blocks)
            )
    return (
        len(pages_detail),
        {"available": True, "count": len(page_items), "items": page_items},
        {"available": bool(block_items), "count": len(block_items) if block_items else 0, "items": block_items},
    )


def _optional_text(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _scalar_or_none(value: object, label: str) -> str | int | float | bool | None:
    if isinstance(value, _SCALAR_TYPES):
        return copy.deepcopy(value)
    raise LegacyStandardAdapterError(f"legacy {label} must be a JSON scalar")


def _normalize_rows(value: object) -> tuple[list[dict[str, str | int | float | bool | None]], bool]:
    if not isinstance(value, list):
        raise LegacyStandardAdapterError("legacy table rows must be a list")
    rows: list[dict[str, str | int | float | bool | None]] = []
    removed_any = False
    for row in value:
        if not isinstance(row, dict):
            raise LegacyStandardAdapterError("legacy table row must be an object")
        normalized: dict[str, str | int | float | bool | None] = {}
        for key, cell in row.items():
            if not isinstance(key, str):
                raise LegacyStandardAdapterError("legacy table row keys must be strings")
            if key in _LOCATION_KEYS:
                removed_any = True
                continue
            normalized[key] = _scalar_or_none(cell, "table cell")
        rows.append(normalized)
    return rows, removed_any


def _normalize_tables(value: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, str], bool]:
    """Project legacy tables into the strict T1 subset without location claims."""
    normalized_tables: list[dict[str, Any]] = []
    table_id_map: dict[str, str] = {}
    removed_any = False
    for table_index, table in enumerate(value, start=1):
        columns = table.get("columns", [])
        if not isinstance(columns, list) or not all(isinstance(column, str) for column in columns):
            raise LegacyStandardAdapterError("legacy table columns must be a list of strings")
        rows, row_locations_removed = _normalize_rows(table.get("rows", []))
        source_table_id = table.get("table_id")
        document_table_id = f"table_{table_index:03d}"
        if isinstance(source_table_id, str):
            table_id_map[source_table_id] = document_table_id
        if any(key in table for key in _LOCATION_KEYS) or row_locations_removed:
            removed_any = True
        normalized_tables.append(
            {
                "table_id": document_table_id,
                "statement": _optional_text(table.get("statement")),
                "table_type": _optional_text(table.get("table_type")),
                "columns": list(columns),
                "rows": rows,
            }
        )
    return normalized_tables, table_id_map, removed_any


def _normalize_metrics(value: list[dict[str, Any]], table_id_map: dict[str, str]) -> tuple[list[dict[str, Any]], bool]:
    metrics: list[dict[str, Any]] = []
    removed_any = False
    for metric in value:
        if any(key in metric for key in _LOCATION_KEYS):
            removed_any = True
        source_table_id = metric.get("table_id")
        metrics.append(
            {
                "statement": _optional_text(metric.get("statement")),
                "item": _optional_text(metric.get("item")),
                "period": _optional_text(metric.get("period")),
                "value": _scalar_or_none(metric.get("value"), "metric value"),
                "normalized_value": _scalar_or_none(metric.get("normalized_value"), "normalized metric value"),
                "table_id": table_id_map.get(source_table_id) if isinstance(source_table_id, str) else None,
            }
        )
    return metrics, removed_any


def _safe_provenance_text(value: object) -> str | None:
    return sanitize_for_public_artifact(value, max_chars=128) if isinstance(value, str) else None


class LegacyStandardAdapter:
    """Translate the stable legacy standard contract into source-neutral fields."""

    def __init__(self, output_dir: str | Path) -> None:
        self.output_dir = Path(output_dir)

    def adapt(self, *, input_sha256: str) -> LegacyAdaptation:
        if not self.output_dir.is_dir():
            raise LegacyStandardAdapterError("fixture output directory is unavailable")
        required_paths = {relative: self.output_dir / relative for relative in LEGACY_STANDARD_REQUIRED}
        missing = [relative for relative, path in required_paths.items() if not path.is_file()]
        if missing:
            raise LegacyStandardAdapterError(f"legacy standard output is missing required artifact {missing[0]}")

        tables_payload = _read_json(required_paths["normalized/normalized_tables.json"], "normalized/normalized_tables.json")
        summary_payload = _read_json(required_paths["normalized/financial_summary.json"], "normalized/financial_summary.json")
        quality_payload = _read_json(required_paths["evaluation/quality_checks.json"], "evaluation/quality_checks.json")
        run_meta = _read_json(required_paths["meta/run_meta.json"], "meta/run_meta.json")
        if not isinstance(tables_payload, dict) or not isinstance(tables_payload.get("tables"), list):
            raise LegacyStandardAdapterError("legacy normalized_tables.json does not contain a tables list")
        if not all(isinstance(table, dict) for table in tables_payload["tables"]):
            raise LegacyStandardAdapterError("legacy normalized_tables.json contains a non-object table")
        if not isinstance(summary_payload, dict):
            raise LegacyStandardAdapterError("legacy financial_summary.json must be an object")
        source_metrics = summary_payload.get("metrics", [])
        if not isinstance(source_metrics, list) or not all(isinstance(metric, dict) for metric in source_metrics):
            raise LegacyStandardAdapterError("legacy financial_summary.json contains invalid metrics")
        if not isinstance(quality_payload, dict) or not isinstance(run_meta, dict):
            raise LegacyStandardAdapterError("legacy quality_checks or run_meta must be an object")

        warnings = [
            "fixture_replay_not_real_execution",
            "evidence_pack_not_attached_at_adapter_stage",
        ]
        normalized_tables, table_id_map, table_locations_removed = _normalize_tables(tables_payload["tables"])
        financial_metrics, metric_locations_removed = _normalize_metrics(source_metrics, table_id_map)
        if table_locations_removed or metric_locations_removed:
            warnings.append("legacy_table_locations_not_provenanced")
        pages_detail_path = self.output_dir / _PAGES_DETAIL
        if pages_detail_path.exists():
            raw_pages = _read_json(pages_detail_path, _PAGES_DETAIL)
            if not isinstance(raw_pages, list):
                raise LegacyStandardAdapterError("legacy pages_detail.json must be a list when present")
            page_count, pages, blocks = _summarize_pages(raw_pages)
        else:
            page_count, pages, blocks = _summarize_pages(None)
            declared_count = run_meta.get("page_count")
            if isinstance(declared_count, int) and declared_count >= 0:
                page_count = declared_count
            else:
                warnings.append("page_count_unavailable_in_legacy_output")
            warnings.append("pages_detail_unavailable_in_legacy_standard_profile")

        declared_input_hash = run_meta.get("input_sha256")
        if declared_input_hash is None:
            input_binding = "not_verifiable_from_legacy_run_meta"
            warnings.append("fixture_input_binding_not_verifiable")
        elif declared_input_hash == input_sha256:
            input_binding = "verified_by_legacy_input_sha256"
        else:
            input_binding = "mismatch_with_legacy_input_sha256"
            warnings.append("fixture_input_hash_mismatch")

        source_hashes = {relative: _sha256_file(path) for relative, path in required_paths.items()}
        if pages_detail_path.is_file():
            source_hashes[_PAGES_DETAIL] = _sha256_file(pages_detail_path)
        financial_summary = {
            "company": _optional_text(summary_payload.get("company")),
            "document_type": _optional_text(summary_payload.get("document_type")),
            "period": _optional_text(summary_payload.get("period")),
            "metric_statistics": {"metric_record_count": len(financial_metrics)},
        }
        provenance = {
            "adapter": "legacy_standard_fixture_v1",
            "source_kind": "fixture_replay",
            "legacy_output_profile": "framework_standard_subset_v1",
            "legacy_execution_backend": _safe_provenance_text(run_meta.get("execution_backend")),
            "legacy_output_source": _safe_provenance_text(run_meta.get("output_source")),
            "legacy_is_synthetic": run_meta.get("is_synthetic") if isinstance(run_meta.get("is_synthetic"), bool) else None,
            "legacy_count_as_real_evaluation": run_meta.get("count_as_real_evaluation") if isinstance(run_meta.get("count_as_real_evaluation"), bool) else None,
            "input_binding": input_binding,
            "source_artifact_hashes": source_hashes,
            "quality_checks_sha256": source_hashes["evaluation/quality_checks.json"],
        }
        return LegacyAdaptation(
            normalized_tables=normalized_tables,
            financial_summary=financial_summary,
            financial_metrics=financial_metrics,
            page_count=page_count,
            pages=pages,
            blocks=blocks,
            warnings=list(dict.fromkeys(warnings)),
            provenance=provenance,
        )
