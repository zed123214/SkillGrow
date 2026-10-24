"""Unique run allocation and content-addressed artifact registration."""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$")


class RunCollisionError(FileExistsError):
    """Raised when an explicitly requested run_id already exists."""


class RunStoreError(ValueError):
    """Raised for an invalid public run identifier or artifact location."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_sha256(value: object) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def validate_identifier(value: str, label: str) -> str:
    if not _IDENTIFIER_RE.fullmatch(value):
        raise RunStoreError(f"{label} must contain only letters, digits, underscores, or hyphens")
    return value


def _new_run_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"run_{stamp}_{uuid.uuid4().hex[:10]}"


@dataclass(frozen=True)
class RunStore:
    """A newly allocated run root; every stored path is run-root-relative."""

    run_id: str
    run_dir: Path

    @classmethod
    def create(cls, run_root: str | Path, requested_run_id: str | None = None) -> "RunStore":
        root = Path(run_root)
        root.mkdir(parents=True, exist_ok=True)
        if requested_run_id is not None:
            run_id = validate_identifier(requested_run_id, "run_id")
            run_dir = root / run_id
            try:
                run_dir.mkdir()
            except FileExistsError as exc:
                raise RunCollisionError("requested run_id already exists; refusing to overwrite") from exc
            return cls(run_id=run_id, run_dir=run_dir)

        for _ in range(20):
            run_id = _new_run_id()
            run_dir = root / run_id
            try:
                run_dir.mkdir()
            except FileExistsError:
                continue
            return cls(run_id=run_id, run_dir=run_dir)
        raise RunStoreError("unable to allocate a unique run_id")

    @property
    def manifest_path(self) -> Path:
        return self.run_dir / "manifest.json"

    def relative_path(self, path: str | Path) -> str:
        try:
            return Path(path).resolve().relative_to(self.run_dir.resolve()).as_posix()
        except (OSError, ValueError) as exc:
            raise RunStoreError("artifact path must stay inside the run root") from exc

    def write_json(self, relative: str | Path, value: object) -> Path:
        path = self.run_dir / relative
        self.relative_path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return path

    def register_file(self, path: str | Path, *, artifact_type: str, producer: str, status: str = "current", active: bool = True) -> dict[str, Any]:
        candidate = Path(path)
        relative = self.relative_path(candidate)
        if not candidate.is_file():
            raise RunStoreError("artifact file is unavailable for registration")
        return {
            "path": relative,
            "type": artifact_type,
            "sha256": sha256_file(candidate),
            "producer": producer,
            "status": status,
            "active": active,
            "current": active,
        }
