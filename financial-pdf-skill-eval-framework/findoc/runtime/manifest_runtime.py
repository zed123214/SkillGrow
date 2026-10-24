"""State, attempt, and current-artifact management for one isolated run."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from findoc.runtime.run_store import RunStore, RunStoreError, sha256_file, utc_now
from findoc.runtime.state_machine import require_transition


class ManifestRuntimeError(ValueError):
    """Raised when a manifest or registered artifact is stale or inconsistent."""


class ManifestRuntime:
    def __init__(self, store: RunStore, manifest: dict[str, Any]) -> None:
        if manifest.get("run_id") != store.run_id:
            raise ManifestRuntimeError("manifest run_id does not match its run directory")
        if not isinstance(manifest.get("stage_attempts"), list) or not isinstance(manifest.get("artifact_registry"), list):
            raise ManifestRuntimeError("manifest attempts or artifact registry is invalid")
        self.store = store
        self.manifest = manifest
        self.manifest.setdefault("state_history", [{"state": manifest["state"], "timestamp": utc_now()}])

    def save(self) -> None:
        self.manifest["updated_at"] = utc_now()
        self.store.write_json("manifest.json", self.manifest)

    def transition(self, target: str) -> None:
        require_transition(self.manifest["state"], target)
        self.manifest["state"] = target
        self.manifest["state_history"].append({"state": target, "timestamp": utc_now()})
        self.save()

    def start_attempt(self, stage: str) -> int:
        prior = [item for item in self.manifest["stage_attempts"] if item.get("stage") == stage]
        attempt = max((int(item.get("attempt", 0)) for item in prior), default=0) + 1
        self.manifest["stage_attempts"].append(
            {
                "stage": stage,
                "attempt": attempt,
                "status": "running",
                "started_at": utc_now(),
                "finished_at": None,
                "sanitized_error": None,
            }
        )
        self.save()
        return attempt

    def finish_attempt(self, stage: str, attempt: int, *, status: str, sanitized_error: str | None = None) -> None:
        matches = [item for item in self.manifest["stage_attempts"] if item.get("stage") == stage and item.get("attempt") == attempt]
        if len(matches) != 1 or matches[0]["status"] != "running":
            raise ManifestRuntimeError("stage attempt is unavailable or already finalized")
        matches[0]["status"] = status
        matches[0]["finished_at"] = utc_now()
        matches[0]["sanitized_error"] = sanitized_error
        self.save()

    def register_current(
        self,
        path: str | Path,
        *,
        artifact_type: str,
        producer: str,
        schema_version: str,
        document_id: str,
    ) -> dict[str, Any]:
        # Validate and hash the new artifact before changing current flags, so a
        # failed registration cannot leave the prior artifact superseded.
        entry = self.store.register_file(path, artifact_type=artifact_type, producer=producer)
        for existing in self.manifest["artifact_registry"]:
            if existing.get("type") == artifact_type and existing.get("document_id") == document_id and existing.get("current"):
                existing["current"] = False
                existing["active"] = False
                existing["status"] = "superseded"
        entry.update(
            {
                "schema_version": schema_version,
                "run_id": self.manifest["run_id"],
                "document_id": document_id,
                "input_hash": self.manifest["input_hash"],
                "config_hash": self.manifest["config_hash"],
            }
        )
        self.manifest["artifact_registry"].append(entry)
        self.save()
        return entry

    def current_entry(self, artifact_type: str, *, document_id: str | None = None) -> dict[str, Any]:
        matches = [
            item
            for item in self.manifest["artifact_registry"]
            if item.get("type") == artifact_type
            and item.get("active") is True
            and item.get("current") is True
            and item.get("status") == "current"
            and (document_id is None or item.get("document_id") == document_id)
        ]
        if len(matches) != 1:
            raise ManifestRuntimeError("manifest does not identify exactly one current artifact")
        return matches[0]

    def load_current_json(
        self,
        artifact_type: str,
        *,
        document_id: str | None = None,
        schema_version: str | None = None,
    ) -> dict[str, Any]:
        entry = self.current_entry(artifact_type, document_id=document_id)
        if schema_version is not None and entry.get("schema_version") != schema_version:
            raise ManifestRuntimeError("registered artifact schema version is stale")
        candidate = self.store.run_dir / entry["path"]
        try:
            self.store.relative_path(candidate)
        except RunStoreError as exc:
            raise ManifestRuntimeError("registered artifact path escapes the run") from exc
        if not candidate.is_file() or sha256_file(candidate) != entry.get("sha256"):
            raise ManifestRuntimeError("registered artifact hash is stale")
        try:
            value = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ManifestRuntimeError("registered artifact is not valid JSON") from exc
        if not isinstance(value, dict):
            raise ManifestRuntimeError("registered artifact must be a JSON object")
        bindings = {
            "run_id": self.manifest["run_id"],
            "document_id": entry["document_id"],
        }
        for field, expected in bindings.items():
            if value.get(field) != expected:
                raise ManifestRuntimeError(f"registered artifact {field} is stale")
        artifact_input_hash = value.get("input_hash")
        if artifact_input_hash is None and isinstance(value.get("input"), dict):
            artifact_input_hash = value["input"].get("sha256")
        if artifact_input_hash != self.manifest["input_hash"]:
            raise ManifestRuntimeError("registered artifact input hash is stale")
        if value.get("schema_version") != entry["schema_version"]:
            raise ManifestRuntimeError("registered artifact schema binding is stale")
        if "config_hash" in value and value["config_hash"] != self.manifest["config_hash"]:
            raise ManifestRuntimeError("registered artifact config hash is stale")
        return value
