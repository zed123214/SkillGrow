"""Small, dependency-free safeguards for values that may reach public artifacts.

The helpers deliberately retain only bounded, redacted diagnostics.  They are
not a secret detector or a substitute for the release scanner; their job is to
keep exception messages and subprocess output from becoming a new disclosure
path in logs, traces, reports, or optional attachments.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any


_SECRET_PREFIX_RE = re.compile(
    r"\b(?:sk-[A-Za-z0-9_-]{16,}|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{20,}|"
    r"ghp_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})\b"
)
_BEARER_RE = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._-]{12,}")
_KEY_VALUE_RE = re.compile(
    r"(?i)(\b(?:api[_-]?key|token|secret|password|cookie|authorization)\b\s*[:=]\s*)"
    r"([^\s\"'#]{8,})"
)
_PRIVATE_URL_RE = re.compile(
    r"(?i)https?://(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}|192\.168\.\d{1,3}\.\d{1,3}|"
    r"172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}|[^/\s]+\.(?:corp|internal|local))"
    r"(?::\d+)?[^\s\"']*"
)
_WINDOWS_PATH_RE = re.compile(r"(?<![A-Za-z0-9_])[A-Za-z]:[\\/][^\s\"']+")
_POSIX_PATH_RE = re.compile(r"(?<![A-Za-z0-9_])/(?:Users|home|private|var/folders|workspace)(?:/[^\s\"']*)?")


def _to_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def digest_text(value: object) -> str:
    """Return a non-reversible diagnostic fingerprint for an arbitrary value."""
    return hashlib.sha256(_to_text(value).encode("utf-8", errors="replace")).hexdigest()


def sanitize_for_public_artifact(value: object, *, max_chars: int = 512) -> str:
    """Redact common credential, private-endpoint, and local-path forms.

    It is intentionally conservative: callers that have a known raw HTTP body
    or process stream must not pass it here as a report payload.  They should
    use :func:`process_output_metadata` instead.
    """
    text = _to_text(value)
    text = _KEY_VALUE_RE.sub(r"\1[REDACTED_SECRET]", text)
    text = _SECRET_PREFIX_RE.sub("[REDACTED_SECRET]", text)
    text = _BEARER_RE.sub("Bearer [REDACTED_SECRET]", text)
    text = _PRIVATE_URL_RE.sub("[REDACTED_PRIVATE_ENDPOINT]", text)
    text = _WINDOWS_PATH_RE.sub("[REDACTED_PATH]", text)
    text = _POSIX_PATH_RE.sub("[REDACTED_PATH]", text)
    text = " ".join(text.split())
    if len(text) > max_chars:
        return text[:max_chars] + " …[truncated]"
    return text


def sanitize_exception_message(exc: BaseException | object, *, max_chars: int = 256) -> str:
    """Return a bounded, redacted exception summary suitable for persisted data."""
    if isinstance(exc, BaseException):
        label = type(exc).__name__
        detail = sanitize_for_public_artifact(str(exc), max_chars=max_chars)
    else:
        label = type(exc).__name__
        detail = sanitize_for_public_artifact(exc, max_chars=max_chars)
    return f"{label}: {detail}" if detail else label


def process_output_metadata(stdout: object, stderr: object) -> dict[str, Any]:
    """Describe process streams without retaining their potentially sensitive text."""
    stdout_text = _to_text(stdout)
    stderr_text = _to_text(stderr)
    return {
        "stdout_chars": len(stdout_text),
        "stderr_chars": len(stderr_text),
        "stdout_sha256": digest_text(stdout_text),
        "stderr_sha256": digest_text(stderr_text),
        "content_redacted": True,
    }


def safe_command_metadata(command: object) -> dict[str, Any]:
    """Expose only a command fingerprint, never its arguments or local paths."""
    text = _to_text(command)
    return {
        "command_sha256": digest_text(text),
        "command_arg_count": len(text.split()),
        "content_redacted": True,
    }


def repository_relative_path(path: str | Path | None, repo_root: str | Path) -> str | None:
    """Return a repo-relative path or an explicit redaction marker.

    A caller may use temporary output directories outside the repository.  They
    must never be copied verbatim into a public report or trace.
    """
    if path is None:
        return None
    try:
        candidate = Path(path).resolve()
        root = Path(repo_root).resolve()
        return candidate.relative_to(root).as_posix()
    except (OSError, ValueError):
        return "[EXTERNAL_PATH_REDACTED]"
