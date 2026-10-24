"""OpenClaw runtime discovery and an auditable E2E adapter contract.

The module deliberately separates local readiness checks from an invocation.
It never reads credential values and it has no built-in paid-model call.  A
real transport must be supplied explicitly; tests use an injected harness and
must not be reported as OpenClaw E2E evidence.
"""
from __future__ import annotations

import hashlib
import shutil
import socket
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol

from framework.sanitization import sanitize_exception_message


OPENCLAW_SKILL_NAME = "financial-pdf-parse-doubao-eval"


class OpenClawAdapterError(RuntimeError):
    """A safe OpenClaw readiness or response-contract failure."""


@dataclass(frozen=True)
class OpenClawRuntimeCheck:
    cli_present: bool
    cli_version: str | None
    gateway_host: str
    gateway_port: int
    gateway_reachable: bool
    skill_name: str
    skill_discovered: bool
    ready: bool
    blockers: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _find_cli(explicit: str | None = None) -> str | None:
    if explicit:
        candidate = Path(explicit)
        return str(candidate) if candidate.is_file() else None
    # Windows PowerShell may refuse the generated .ps1 shim even when the
    # adjacent .cmd shim is usable.  Prefer the command shim explicitly.
    return shutil.which("openclaw.cmd") or shutil.which("openclaw")


def _read_version(executable: str, timeout_seconds: float) -> str | None:
    try:
        completed = subprocess.run(
            [executable, "--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=timeout_seconds,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    first_line = (completed.stdout or completed.stderr).strip().splitlines()
    return first_line[0][:128] if first_line else None


def _gateway_reachable(host: str, port: int, timeout_seconds: float) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout_seconds):
            return True
    except OSError:
        return False


def discover_openclaw(
    *,
    executable: str | None = None,
    workspace: str | Path | None = None,
    gateway_host: str = "127.0.0.1",
    gateway_port: int = 18789,
    timeout_seconds: float = 2.0,
    skill_name: str = OPENCLAW_SKILL_NAME,
) -> OpenClawRuntimeCheck:
    """Return only non-secret local readiness facts.

    Credential contents, config bodies, sessions, and model calls are outside
    this check.  ``ready`` therefore means only CLI + local gateway + Skill
    discovery, not that a paid/provider-backed turn is authorized.
    """
    cli = _find_cli(executable)
    version = _read_version(cli, timeout_seconds) if cli else None
    workspace_root = Path(workspace) if workspace is not None else Path.home() / ".openclaw" / "workspace"
    skill_discovered = (workspace_root / "skills" / skill_name / "SKILL.md").is_file()
    reachable = _gateway_reachable(gateway_host, gateway_port, min(timeout_seconds, 1.0))
    blockers: list[str] = []
    if cli is None or version is None:
        blockers.append("openclaw_cli_unavailable")
    if not reachable:
        blockers.append("openclaw_gateway_unreachable")
    if not skill_discovered:
        blockers.append("project_skill_not_discovered")
    return OpenClawRuntimeCheck(
        cli_present=cli is not None,
        cli_version=version,
        gateway_host=gateway_host,
        gateway_port=gateway_port,
        gateway_reachable=reachable,
        skill_name=skill_name,
        skill_discovered=skill_discovered,
        ready=not blockers,
        blockers=tuple(blockers),
    )


@dataclass(frozen=True)
class OpenClawInvocation:
    input_pdf: Path
    run_dir: Path
    run_id: str
    document_id: str
    input_sha256: str
    skill_name: str = OPENCLAW_SKILL_NAME


class OpenClawTransport(Protocol):
    """Explicit boundary for a real OpenClaw runtime integration."""

    execution_kind: str

    def invoke(self, request: OpenClawInvocation) -> dict[str, Any]: ...


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class OpenClawE2EAdapter:
    """Validate that an injected transport produced current run artifacts.

    This adapter does not provide a mock fallback.  A caller may use a harness
    transport in unit tests, but ``execution_kind`` is retained in the result
    so harness evidence cannot be confused with ``real_openclaw``.
    """

    def __init__(self, runtime: OpenClawRuntimeCheck, transport: OpenClawTransport) -> None:
        self.runtime = runtime
        self.transport = transport

    def invoke(self, request: OpenClawInvocation) -> dict[str, Any]:
        if not self.runtime.ready:
            raise OpenClawAdapterError("OpenClaw runtime is not ready for an end-to-end invocation")
        if not request.input_pdf.is_file() or request.input_pdf.suffix.lower() != ".pdf":
            raise OpenClawAdapterError("OpenClaw input PDF is unavailable")
        if _sha256_file(request.input_pdf) != request.input_sha256:
            raise OpenClawAdapterError("OpenClaw input hash does not match the invocation contract")
        try:
            result = self.transport.invoke(request)
        except Exception as exc:  # pragma: no cover - exact transport is external
            raise OpenClawAdapterError(sanitize_exception_message(exc, max_chars=192)) from exc
        if not isinstance(result, dict):
            raise OpenClawAdapterError("OpenClaw transport returned an invalid result")
        expected = {
            "runtime": "openclaw",
            "execution_kind": self.transport.execution_kind,
            "skill_name": request.skill_name,
            "run_id": request.run_id,
            "document_id": request.document_id,
            "input_sha256": request.input_sha256,
        }
        for key, value in expected.items():
            if result.get(key) != value:
                raise OpenClawAdapterError(f"OpenClaw result does not bind {key}")
        artifacts = result.get("artifact_registry")
        if not isinstance(artifacts, list) or not artifacts:
            raise OpenClawAdapterError("OpenClaw result has no registered artifacts")
        for artifact in artifacts:
            if not isinstance(artifact, dict):
                raise OpenClawAdapterError("OpenClaw artifact entry is invalid")
            relative = artifact.get("path")
            digest = artifact.get("sha256")
            if not isinstance(relative, str) or not isinstance(digest, str):
                raise OpenClawAdapterError("OpenClaw artifact entry lacks path or hash")
            candidate = (request.run_dir / relative).resolve()
            try:
                candidate.relative_to(request.run_dir.resolve())
            except ValueError as exc:
                raise OpenClawAdapterError("OpenClaw artifact path escapes the run directory") from exc
            if not candidate.is_file() or _sha256_file(candidate) != digest:
                raise OpenClawAdapterError("OpenClaw artifact hash validation failed")
        return result
