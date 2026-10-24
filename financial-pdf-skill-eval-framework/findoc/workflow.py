"""T1 orchestration: legacy standard fixture -> DocumentIR -> isolated manifest."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from findoc.adapters.legacy_standard import LegacyStandardAdapter, LegacyStandardAdapterError
from findoc.models.document_ir import DOCUMENT_IR_SCHEMA_VERSION, DocumentIRValidationError, validate_document_ir
from findoc.runtime.run_store import RunCollisionError, RunStore, RunStoreError, canonical_sha256, sha256_file, utc_now, validate_identifier
from framework.sanitization import sanitize_exception_message


RUN_MANIFEST_SCHEMA_VERSION = "findoc.run_manifest.v1"


class FindocWorkflowError(RuntimeError):
    """A safe, user-facing T1 workflow failure."""


@dataclass(frozen=True)
class AnalysisResult:
    """A public, path-safe summary of one completed T1 analysis run."""

    run_id: str
    document_id: str
    state: str
    manifest_path: str
    document_ir_path: str

    def to_dict(self) -> dict[str, str]:
        return {
            "run_id": self.run_id,
            "document_id": self.document_id,
            "state": self.state,
            "manifest_path": self.manifest_path,
            "document_ir_path": self.document_ir_path,
        }


def _input_info(input_path: Path) -> dict[str, Any]:
    if not input_path.is_file():
        raise FindocWorkflowError("input PDF is unavailable")
    if input_path.suffix.lower() != ".pdf":
        raise FindocWorkflowError("input must use the .pdf extension")
    try:
        digest = sha256_file(input_path)
        size_bytes = input_path.stat().st_size
    except OSError as exc:
        # Do not retain the operating-system message: it can include an input
        # path outside the run root and would otherwise reach the CLI traceback.
        raise FindocWorkflowError("input PDF cannot be read") from exc
    return {
        "identifier": f"sha256:{digest[:16]}",
        "sha256": digest,
        "size_bytes": size_bytes,
    }


def _manifest_base(
    store: RunStore,
    document_id: str,
    input_info: dict[str, Any],
    backend: str,
    config: dict[str, Any],
    parent_run_id: str | None,
) -> dict[str, Any]:
    now = utc_now()
    return {
        "schema_version": RUN_MANIFEST_SCHEMA_VERSION,
        "run_id": store.run_id,
        "parent_run_id": parent_run_id,
        "state": "created",
        "created_at": now,
        "updated_at": now,
        "document_id": document_id,
        "input": input_info,
        "input_hash": input_info["sha256"],
        "config": config,
        "config_hash": canonical_sha256(config),
        "backend": backend,
        "execution_kind": "fixture_replay",
        "stage_attempts": [],
        "sanitized_errors": [],
        "artifact_registry": [],
    }


def _write_manifest(store: RunStore, manifest: dict[str, Any]) -> None:
    manifest["updated_at"] = utc_now()
    store.write_json("manifest.json", manifest)


def _safe_failure(exc: BaseException | object) -> str:
    return sanitize_exception_message(exc, max_chars=256)


def analyze_document(
    *,
    input_path: str | Path,
    backend: str = "fixture",
    fixture_output_dir: str | Path | None = None,
    run_root: str | Path = "runs",
    run_id: str | None = None,
    document_id: str | None = None,
    parent_run_id: str | None = None,
) -> AnalysisResult:
    """Create a fresh T1 run from an already-present legacy fixture output.

    This function never invokes a parser or network-backed service.  A fixture
    source is read-only and its hashes/provenance are copied into DocumentIR.
    """
    if backend != "fixture":
        raise FindocWorkflowError("T1 supports only the offline fixture backend")
    if fixture_output_dir is None:
        raise FindocWorkflowError("fixture backend requires --fixture-output-dir")

    input_info = _input_info(Path(input_path))
    resolved_document_id = document_id or f"doc_{input_info['sha256'][:16]}"
    try:
        validate_identifier(resolved_document_id, "document_id")
        if parent_run_id is not None:
            validate_identifier(parent_run_id, "parent_run_id")
    except RunStoreError as exc:
        raise FindocWorkflowError(_safe_failure(exc)) from exc
    if parent_run_id is not None and not (Path(run_root) / parent_run_id / "manifest.json").is_file():
        raise FindocWorkflowError("parent_run_id does not reference an existing manifest in this run root")

    try:
        store = RunStore.create(run_root, requested_run_id=run_id)
    except (RunCollisionError, RunStoreError, OSError) as exc:
        raise FindocWorkflowError(_safe_failure(exc)) from exc

    config = {
        "workflow_schema_version": RUN_MANIFEST_SCHEMA_VERSION,
        "document_ir_schema_version": DOCUMENT_IR_SCHEMA_VERSION,
        "backend": "fixture",
        "adapter": "legacy_standard_fixture_v1",
    }
    manifest = _manifest_base(store, resolved_document_id, input_info, backend, config, parent_run_id)
    manifest["state"] = "running"
    _write_manifest(store, manifest)

    started_at = utc_now()
    try:
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
        document_ir_relative = Path("documents") / resolved_document_id / "document_ir.json"
        document_ir_path = store.write_json(document_ir_relative, document_ir)
        manifest["artifact_registry"].append(
            store.register_file(
                document_ir_path,
                artifact_type="document_ir",
                producer="findoc.workflow.analyze_document",
            )
        )
        manifest["stage_attempts"].append(
            {
                "stage": "adapt_legacy_standard_output",
                "attempt": 1,
                "status": "completed",
                "started_at": started_at,
                "finished_at": utc_now(),
                "sanitized_error": None,
            }
        )
        manifest["state"] = "completed"
        _write_manifest(store, manifest)
        return AnalysisResult(
            run_id=store.run_id,
            document_id=resolved_document_id,
            state="completed",
            manifest_path="manifest.json",
            document_ir_path=document_ir_relative.as_posix(),
        )
    except (LegacyStandardAdapterError, DocumentIRValidationError, RunStoreError, OSError, ValueError) as exc:
        failure = _safe_failure(exc)
        manifest["stage_attempts"].append(
            {
                "stage": "adapt_legacy_standard_output",
                "attempt": 1,
                "status": "failed",
                "started_at": started_at,
                "finished_at": utc_now(),
                "sanitized_error": failure,
            }
        )
        manifest["sanitized_errors"].append(failure)
        manifest["state"] = "failed"
        _write_manifest(store, manifest)
        raise FindocWorkflowError(failure) from exc
