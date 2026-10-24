"""Dependency-free, release-surface security scan.

Unlike the former framework-only check, this scanner starts at the primary
repository root and inventories source, configuration, documentation, data,
reports, dashboards, Allure attachments, fixtures, Skill examples, hidden
files, and archive members.  Its report is intentionally body-free: a finding
contains only a repository-relative path, rule id, line (when applicable),
count, non-reversible fingerprint, and remediation state.

It does not establish that a PDF is publicly distributable.  PDFs are
inventoried and receive either a ``synthetic_declared`` or ``review_required``
classification; compressed PDF content and licensing/provenance still require
human review before release.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import zipfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable


PRIMARY_REPO_ROOT = Path(__file__).resolve().parents[2]
FRAMEWORK_DIRNAME = "financial-pdf-skill-eval-framework"
REPORT_SCHEMA_VERSION = "t0-release-security-scan-v1"

# These are development caches, not prospective release artifacts.  They are
# still reported as excluded inventory so a manual archive cannot overlook them.
NON_RELEASE_DIRS = {
    ".git",
    ".venv",
    "venv",
    "env",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".tox",
    ".understand-anything",
}
ARCHIVE_SUFFIXES = {".zip", ".whl"}
PDF_SUFFIX = ".pdf"
OPAQUE_SUFFIXES = {".pdf", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".pyc", ".dll", ".exe", ".so"}
TEXT_SUFFIXES = {
    ".py", ".md", ".txt", ".json", ".yaml", ".yml", ".ini", ".cfg", ".toml",
    ".ps1", ".sh", ".csv", ".html", ".css", ".js", ".ts", ".xml", ".env",
}
SENSITIVE_PDF_NAME_RE = re.compile(r"(?i)(?:confidential|internal|secret|patient|medical[_ -]?record|private|机密|内部|病历)")

_KNOWN_SECRET_RE = re.compile(
    r"\b(?:sk-[A-Za-z0-9_-]{16,}|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{20,}|"
    r"ghp_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|xox[baprs]-[A-Za-z0-9-]{20,})\b"
)
_GENERIC_CREDENTIAL_RE = re.compile(
    r"(?i)\b(?:api[_-]?key|token|secret|password|cookie)\b\s*[:=]\s*[\"']?([^\s\"'#]{12,})"
)
_BEARER_RE = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._-]{16,}")
_PRIVATE_URL_RE = re.compile(
    r"(?i)https?://(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}|192\.168\.\d{1,3}\.\d{1,3}|"
    r"172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}|[^/\s]+\.(?:corp|internal|local))"
    r"(?::\d+)?(?:/[^\s\"']*)?"
)
_PRESIGNED_URL_RE = re.compile(r"(?i)[?&](?:x-amz-signature|x-goog-signature|awsaccesskeyid|signature)=[^\s&#]+")
_WINDOWS_ABS_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9_])[A-Za-z]:[\\/](?:Users|Documents and Settings|home|workspace|Windows|ProgramData|Program Files)\b"
)
_POSIX_ABS_PATH_RE = re.compile(r"(?<![A-Za-z0-9_])/(?:Users|home|private|var/folders|workspace)(?:/|\b)")
_RAW_HTTP_BODY_RE = re.compile(
    r"(?s)except\s+(?:[\w.]+\.)?HTTPError\s+as\s+\w+\s*:\s*.{0,500}?\.read\(\)\.decode"
)


def _repo_relative(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _fingerprint(rule_id: str, relative_path: str, line: int | None, matched: Iterable[str]) -> str:
    payload = "\0".join([rule_id, relative_path, str(line or 0), *sorted(matched)])
    return hashlib.sha256(payload.encode("utf-8", errors="replace")).hexdigest()[:20]


def _finding(
    relative_path: str,
    rule_id: str,
    line: int | None,
    matches: Iterable[str],
    *,
    remediation_status: str = "open",
) -> dict[str, Any]:
    values = list(matches)
    return {
        "path": relative_path,
        "rule_id": rule_id,
        "line": line,
        "count": len(values),
        "fingerprint": _fingerprint(rule_id, relative_path, line, values),
        "remediation_status": remediation_status,
    }


def _looks_like_placeholder(value: str) -> bool:
    normalized = value.strip().strip("\"'").lower()
    if normalized in {"", "...", "xxx", "changeme", "your_key", "your-key", "placeholder", "test-key"}:
        return True
    # Environment/configuration lookup expressions are not checked-in values.
    return any(marker in normalized for marker in ("os.environ", "getenv", "process.env", "${", "{{", "(", "{"))


def _is_text_file(path: Path, data: bytes) -> bool:
    if path.suffix.lower() in TEXT_SUFFIXES or path.name.startswith(".env"):
        return True
    return b"\x00" not in data[:4096]


def _scope_areas(relative_path: str) -> set[str]:
    lower = relative_path.lower()
    areas: set[str] = set()
    if relative_path.startswith("skills/"):
        areas.update({"source", "skill_examples" if "/examples/" in relative_path else "skill"})
    if f"{FRAMEWORK_DIRNAME}/framework/" in relative_path or any(
        token in relative_path
        for token in (f"{FRAMEWORK_DIRNAME}/evaluation/", f"{FRAMEWORK_DIRNAME}/judge/", f"{FRAMEWORK_DIRNAME}/optimizer/", f"{FRAMEWORK_DIRNAME}/static_tests/", f"{FRAMEWORK_DIRNAME}/tools/", f"{FRAMEWORK_DIRNAME}/tests/")
    ):
        areas.add("source")
    if f"{FRAMEWORK_DIRNAME}/dashboard/" in relative_path:
        areas.update({"source", "dashboard"})
    if relative_path.endswith((".yaml", ".yml", ".ini", ".toml", ".cfg")) or relative_path in {"Dockerfile", ".dockerignore", ".gitignore"}:
        areas.add("config")
    if "/docs/" in relative_path or relative_path.endswith(("README.md", "README_zh.md")):
        areas.add("docs")
    if any(token in relative_path for token in (f"{FRAMEWORK_DIRNAME}/data/", f"{FRAMEWORK_DIRNAME}/datasets/", f"{FRAMEWORK_DIRNAME}/testcases/", f"{FRAMEWORK_DIRNAME}/evaluation/ground_truth/")):
        areas.update({"data", "fixture"})
    if f"{FRAMEWORK_DIRNAME}/reports/" in relative_path:
        areas.add("reports")
    if "allure-results/" in lower or "allure-html/" in lower:
        areas.add("allure_attachments")
    if any(relative_path.startswith(prefix) for prefix in ("dist/", "build/", "release/", "deliverables/")) or Path(relative_path).suffix.lower() in ARCHIVE_SUFFIXES:
        areas.add("delivery_packages")
    if Path(relative_path).suffix.lower() == PDF_SUFFIX:
        areas.add("pdf")
    return areas or {"other"}


def _declared_synthetic_pdf_paths(root: Path) -> set[str]:
    """Read the simple YAML shape without importing PyYAML.

    This is a release classification hint only; it does not prove provenance or
    licensing and intentionally does not turn a fixture replay into execution.
    """
    declared: set[str] = set()
    cases_dir = root / FRAMEWORK_DIRNAME / "testcases" / "pdf_cases"
    if not cases_dir.exists():
        return declared
    for case_file in cases_dir.glob("*.yaml"):
        try:
            text = case_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if "synthetic_pdf" not in text:
            continue
        match = re.search(r"(?m)^input_pdf:\s*([^\s#]+)", text)
        if match:
            declared.add(f"{FRAMEWORK_DIRNAME}/{match.group(1).strip()}")
    return declared


def _iter_release_files(root: Path) -> tuple[list[Path], list[dict[str, str]]]:
    files: list[Path] = []
    excluded: list[dict[str, str]] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        excluded_dir = next((part for part in relative.parts if part in NON_RELEASE_DIRS), None)
        if excluded_dir:
            excluded.append({"path": relative.as_posix(), "reason": f"non_release_cache:{excluded_dir}"})
            continue
        files.append(path)
    return files, excluded


def _scan_text(relative_path: str, text: str) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        known_secrets = list(_KNOWN_SECRET_RE.findall(line))
        if known_secrets:
            findings.append(_finding(relative_path, "SEC_SECRET_LITERAL", line_number, known_secrets))

        generic_values = [value for value in _GENERIC_CREDENTIAL_RE.findall(line) if not _looks_like_placeholder(value)]
        if generic_values:
            findings.append(_finding(relative_path, "SEC_CREDENTIAL_ASSIGNMENT", line_number, generic_values))

        bearer_values = list(_BEARER_RE.findall(line))
        if bearer_values:
            findings.append(_finding(relative_path, "SEC_BEARER_TOKEN", line_number, bearer_values))

        private_urls = list(_PRIVATE_URL_RE.findall(line))
        if private_urls:
            findings.append(_finding(relative_path, "SEC_PRIVATE_ENDPOINT", line_number, private_urls))

        presigned = list(_PRESIGNED_URL_RE.findall(line))
        if presigned:
            findings.append(_finding(relative_path, "SEC_PRESIGNED_URL", line_number, presigned))

        windows_paths = list(_WINDOWS_ABS_PATH_RE.findall(line))
        if windows_paths:
            findings.append(_finding(relative_path, "SEC_ABSOLUTE_PATH", line_number, windows_paths))

        posix_paths = list(_POSIX_ABS_PATH_RE.findall(line))
        if posix_paths:
            findings.append(_finding(relative_path, "SEC_ABSOLUTE_PATH", line_number, posix_paths))

    for match in _RAW_HTTP_BODY_RE.finditer(text):
        line_number = text.count("\n", 0, match.start()) + 1
        findings.append(_finding(relative_path, "SEC_RAW_HTTP_ERROR_BODY", line_number, [match.group(0)]))
    return findings


def _scan_archive(path: Path, root: Path) -> tuple[list[dict[str, Any]], int, str | None]:
    relative = _repo_relative(root, path)
    findings: list[dict[str, Any]] = []
    member_count = 0
    try:
        with zipfile.ZipFile(path) as archive:
            for member in archive.infolist():
                if member.is_dir():
                    continue
                member_count += 1
                member_path = f"{relative}!{member.filename.replace(chr(92), '/') }"
                suffix = Path(member.filename).suffix.lower()
                if suffix in OPAQUE_SUFFIXES and suffix != PDF_SUFFIX:
                    continue
                try:
                    data = archive.read(member)
                except (OSError, RuntimeError, zipfile.BadZipFile):
                    continue
                if suffix == PDF_SUFFIX:
                    opaque = data.decode("latin-1", errors="ignore")
                    for secret in _KNOWN_SECRET_RE.findall(opaque):
                        findings.append(_finding(member_path, "SEC_SECRET_LITERAL", None, [secret]))
                elif _is_text_file(Path(member.filename), data):
                    findings.extend(_scan_text(member_path, data.decode("utf-8", errors="replace")))
    except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
        return findings, member_count, type(exc).__name__
    return findings, member_count, None


def _ignore_policy_warnings(root: Path, excluded: list[dict[str, str]]) -> list[dict[str, Any]]:
    if not any(".understand-anything" in item["path"] for item in excluded):
        return []
    files = [root / ".gitignore", root / ".dockerignore", root / FRAMEWORK_DIRNAME / ".gitignore"]
    warnings: list[dict[str, Any]] = []
    for policy_file in files:
        try:
            text = policy_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        if ".understand-anything" not in text:
            warnings.append({
                "path": _repo_relative(root, policy_file),
                "rule_id": "SEC_RELEASE_EXCLUSION_MISSING",
                "line": None,
                "count": 1,
                "fingerprint": _fingerprint("SEC_RELEASE_EXCLUSION_MISSING", _repo_relative(root, policy_file), None, [".understand-anything"]),
                "remediation_status": "open",
            })
    return warnings


def run(scan_root: Path | str | None = None) -> dict[str, Any]:
    """Scan a repository without network access or third-party dependencies."""
    root = Path(scan_root).resolve() if scan_root is not None else PRIMARY_REPO_ROOT
    files, excluded = _iter_release_files(root)
    synthetic_declared = _declared_synthetic_pdf_paths(root)
    findings: list[dict[str, Any]] = []
    areas: Counter[str] = Counter()
    opaque_counts: Counter[str] = Counter()
    unreadable: list[dict[str, str]] = []
    pdf_records: list[dict[str, str]] = []
    archive_members = 0

    for path in files:
        relative = _repo_relative(root, path)
        for area in _scope_areas(relative):
            areas[area] += 1
        suffix = path.suffix.lower()
        if suffix in ARCHIVE_SUFFIXES:
            archive_findings, member_count, archive_error = _scan_archive(path, root)
            findings.extend(archive_findings)
            archive_members += member_count
            if archive_error:
                unreadable.append({"path": relative, "reason": archive_error})
            continue
        try:
            data = path.read_bytes()
        except OSError as exc:
            unreadable.append({"path": relative, "reason": type(exc).__name__})
            continue
        if suffix == PDF_SUFFIX:
            opaque_counts["pdf"] += 1
            classification = "synthetic_declared" if relative in synthetic_declared else "review_required"
            if SENSITIVE_PDF_NAME_RE.search(path.name):
                classification = "synthetic_declared_sensitive_name_review_required" if classification == "synthetic_declared" else "sensitive_name_review_required"
            pdf_records.append({"path": relative, "classification": classification})
            # Byte signatures can catch accidentally embedded plaintext secrets,
            # but are not a substitute for decompression, OCR, license, or human review.
            opaque_text = data.decode("latin-1", errors="ignore")
            secret_values = _KNOWN_SECRET_RE.findall(opaque_text)
            if secret_values:
                findings.append(_finding(relative, "SEC_SECRET_LITERAL", None, secret_values))
            continue
        if suffix in OPAQUE_SUFFIXES or not _is_text_file(path, data):
            opaque_counts[suffix.lstrip(".") or "opaque"] += 1
            continue
        try:
            findings.extend(_scan_text(relative, data.decode("utf-8", errors="replace")))
        except UnicodeError as exc:
            unreadable.append({"path": relative, "reason": type(exc).__name__})

    findings.extend(_ignore_policy_warnings(root, excluded))
    findings.sort(key=lambda item: (item["path"], item["line"] or 0, item["rule_id"]))
    rule_counts = Counter(item["rule_id"] for item in findings)
    blocking_rules = {
        "SEC_SECRET_LITERAL",
        "SEC_CREDENTIAL_ASSIGNMENT",
        "SEC_BEARER_TOKEN",
        "SEC_PRIVATE_ENDPOINT",
        "SEC_PRESIGNED_URL",
        "SEC_ABSOLUTE_PATH",
        "SEC_RAW_HTTP_ERROR_BODY",
    }
    has_blocking = any(item["rule_id"] in blocking_rules for item in findings)
    review_required_pdfs = [item for item in pdf_records if "review_required" in item["classification"]]
    expected_areas = (
        "source", "config", "docs", "data", "reports", "dashboard", "allure_attachments",
        "fixture", "skill_examples", "delivery_packages", "pdf",
    )

    return {
        "name": "release_security_scan",
        "schema_version": REPORT_SCHEMA_VERSION,
        "scan_root": ".",
        "network_accessed": False,
        "passed": not has_blocking,
        "release_ready": not has_blocking and not review_required_pdfs,
        "no_secret_leak": not any(rule_counts[rule] for rule in ("SEC_SECRET_LITERAL", "SEC_CREDENTIAL_ASSIGNMENT", "SEC_BEARER_TOKEN")),
        "no_internal_url": not any(rule_counts[rule] for rule in ("SEC_PRIVATE_ENDPOINT", "SEC_PRESIGNED_URL")),
        "no_absolute_path": rule_counts["SEC_ABSOLUTE_PATH"] == 0,
        "no_raw_http_error_body": rule_counts["SEC_RAW_HTTP_ERROR_BODY"] == 0,
        "files_scanned": len(files),
        "scope_counts": {area: areas.get(area, 0) for area in expected_areas},
        "opaque_file_counts": dict(sorted(opaque_counts.items())),
        "archive_member_count": archive_members,
        "excluded_non_release_files": excluded,
        "unreadable_files": unreadable,
        "pdf_inventory": {
            "total": len(pdf_records),
            "declared_synthetic_count": sum(1 for item in pdf_records if item["classification"].startswith("synthetic_declared")),
            "review_required_count": len(review_required_pdfs),
            "text_content_scan": "byte-signature-only; human provenance/license/content review required",
            "records": pdf_records,
        },
        "rule_counts": dict(sorted(rule_counts.items())),
        "findings": findings,
        "limitations": [
            "The scan never emits matched bodies or values.",
            "PDF byte signatures cannot establish content safety, licensing, or provenance.",
            "Excluded development caches remain physically present and must stay outside any manual archive.",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the dependency-free release security scan.")
    parser.add_argument("--root", type=Path, default=None, help="Repository root to scan; defaults to the primary repository.")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print body-free JSON.")
    args = parser.parse_args(argv)
    report = run(args.root)
    print(json.dumps(report, ensure_ascii=False, indent=2 if args.pretty else None))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
