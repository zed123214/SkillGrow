"""Dependency-free harness builders; never execution or accuracy evidence."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def make_pdf(root: Path, name: str = "synthetic.pdf") -> Path:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"%PDF-1.4\n% dependency-free synthetic harness\n%%EOF\n")
    return path


def make_fixture(
    root: Path,
    input_pdf: Path,
    *,
    include_pages: bool = True,
    input_binding: str = "match",
    missing_artifact: str | None = None,
) -> Path:
    fixture = root / "fixture"
    (fixture / "raw").mkdir(parents=True, exist_ok=True)
    (fixture / "raw" / "parsed.md").write_text("# Synthetic fixture\n", encoding="utf-8")
    write_json(
        fixture / "normalized" / "normalized_tables.json",
        {
            "tables": [
                {
                    "table_id": "source_table_1",
                    "statement": "income_statement",
                    "table_type": "financial_statement",
                    "columns": ["item", "value"],
                    "rows": [{"item": "revenue", "value": "100"}],
                }
            ]
        },
    )
    write_json(
        fixture / "normalized" / "financial_summary.json",
        {
            "company": "Synthetic Co",
            "document_type": "synthetic_statement",
            "period": "2026Q1",
            "metrics": [
                {
                    "statement": "income_statement",
                    "item": "revenue",
                    "period": "2026Q1",
                    "value": "100",
                    "normalized_value": 100,
                    "table_id": "source_table_1",
                }
            ],
        },
    )
    write_json(fixture / "evaluation" / "quality_checks.json", {"contract": {"passed": True}})
    run_meta = {
        "execution_backend": "synthetic_fixture_generator",
        "output_source": "independent_synthetic_spec",
        "is_synthetic": True,
        "count_as_real_evaluation": False,
        "page_count": 1,
    }
    if input_binding == "match":
        run_meta["input_sha256"] = sha256(input_pdf)
    elif input_binding == "mismatch":
        run_meta["input_sha256"] = "0" * 64
    write_json(fixture / "meta" / "run_meta.json", run_meta)
    if include_pages:
        write_json(
            fixture / "raw" / "pages_detail.json",
            [{"page_number": 1, "blocks": [{"type": "table"}]}],
        )
    if missing_artifact:
        (fixture / missing_artifact).unlink()
    return fixture


class SuccessfulHarnessJudge:
    name = "harness"
    model = "deterministic-test-double"
    model_version = "v1"

    def evaluate(self, payload: dict) -> dict:
        evidence_id = payload["evidence"][0]["evidence_id"]
        return {
            "verdict": "pass",
            "dimensions": {
                "reading_order": {"score": 1.0, "reason": "harness-only"},
                "table_structure": {"score": 1.0, "reason": "harness-only"},
                "evidence_alignment": {"score": 1.0, "reason": "harness-only"},
            },
            "confidence": 0.9,
            "evidence_citations": [evidence_id],
            "deductions": [],
            "tokens": {"input": 1, "output": 1, "total": 2},
            "cost": {"amount": 0, "currency": "TEST"},
        }


class InvalidCitationHarnessJudge(SuccessfulHarnessJudge):
    def evaluate(self, payload: dict) -> dict:
        value = super().evaluate(payload)
        value["evidence_citations"] = ["ev_" + "0" * 24]
        return value


class MissingConfidenceHarnessJudge(SuccessfulHarnessJudge):
    def evaluate(self, payload: dict) -> dict:
        value = super().evaluate(payload)
        value["confidence"] = None
        return value
