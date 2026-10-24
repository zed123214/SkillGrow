"""Deterministic synthetic PDF, oracle, and fixture generator for T6.

The oracle is serialized and hashed before the fixture is materialized.  The
fixture is still a replay source rather than a parser execution, so the T6
runner never reports its oracle comparison as parsing accuracy.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from findoc.runtime.run_store import canonical_sha256, sha256_file


@dataclass(frozen=True)
class SyntheticCaseSpec:
    case_id: str
    amount: int
    period: str
    include_pages_detail: bool
    difficulty_tags: tuple[str, ...]
    failure_mode: str | None = None

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["difficulty_tags"] = list(self.difficulty_tags)
        return value


def t6_case_specs() -> list[SyntheticCaseSpec]:
    specs: list[SyntheticCaseSpec] = []
    tags = (
        "normal_table",
        "multi_header",
        "cross_page",
        "no_border",
        "bilingual",
        "negative_decimal",
        "unit_mixed",
        "wide_landscape",
        "header_footer_noise",
        "watermark",
    )
    for index in range(1, 21):
        include_pages = index <= 15
        difficulty = [tags[(index - 1) % len(tags)], "synthetic", "fixture_replay"]
        if not include_pages:
            difficulty.append("missing_page_detail")
        failure_mode = "missing_quality_artifact" if index == 20 else None
        if failure_mode:
            difficulty.append("contract_failure")
        specs.append(
            SyntheticCaseSpec(
                case_id=f"t6_case_{index:03d}",
                amount=1000 + index * 17,
                period=f"2026Q{((index - 1) % 4) + 1}",
                include_pages_detail=include_pages,
                difficulty_tags=tuple(difficulty),
                failure_mode=failure_mode,
            )
        )
    return specs


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _pdf_bytes(spec: SyntheticCaseSpec) -> bytes:
    """Return a deterministic one-page PDF with a valid xref table."""
    text = f"{spec.case_id} {spec.period} revenue {spec.amount}"
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET\n".encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"endstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    content = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for object_number, body in enumerate(objects, start=1):
        offsets.append(len(content))
        content.extend(f"{object_number} 0 obj\n".encode("ascii"))
        content.extend(body)
        content.extend(b"\nendobj\n")
    xref_offset = len(content)
    content.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    content.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        content.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    content.extend(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF\n"
        ).encode("ascii")
    )
    return bytes(content)


def build_synthetic_case(root: Path, spec: SyntheticCaseSpec) -> dict[str, Any]:
    case_root = root / spec.case_id
    case_root.mkdir(parents=True, exist_ok=False)
    pdf_path = case_root / "input.pdf"
    pdf_path.write_bytes(_pdf_bytes(spec))
    pdf_hash = sha256_file(pdf_path)

    oracle = {
        "schema_version": "findoc.synthetic_oracle.v1",
        "case_id": spec.case_id,
        "expected": {
            "company": f"Synthetic Company {spec.case_id[-3:]}",
            "period": spec.period,
            "statement": "income_statement",
            "item": "revenue",
            "normalized_value": spec.amount,
            "page_count": 1,
        },
        "source_kind": "synthetic_oracle",
        "license": "CC0-1.0 generator-authored synthetic data",
        "generator_contract": "oracle_frozen_before_fixture",
    }
    oracle_path = case_root / "oracle.json"
    _write_json(oracle_path, oracle)
    oracle_hash = sha256_file(oracle_path)

    fixture = case_root / "fixture"
    (fixture / "raw").mkdir(parents=True)
    (fixture / "raw" / "parsed.md").write_text(
        f"# Synthetic statement {spec.case_id}\n\nRevenue: {spec.amount}\n",
        encoding="utf-8",
    )
    _write_json(
        fixture / "normalized" / "normalized_tables.json",
        {
            "tables": [
                {
                    "table_id": f"source_{spec.case_id}",
                    "statement": "income_statement",
                    "table_type": "financial_statement",
                    "columns": ["item", spec.period],
                    "rows": [{"item": "revenue", spec.period: spec.amount}],
                }
            ]
        },
    )
    _write_json(
        fixture / "normalized" / "financial_summary.json",
        {
            "company": oracle["expected"]["company"],
            "document_type": "synthetic_financial_statement",
            "period": spec.period,
            "metrics": [
                {
                    "statement": "income_statement",
                    "item": "revenue",
                    "period": spec.period,
                    "value": str(spec.amount),
                    "normalized_value": spec.amount,
                    "table_id": f"source_{spec.case_id}",
                }
            ],
        },
    )
    _write_json(
        fixture / "evaluation" / "quality_checks.json",
        {"contract": {"passed": True}, "source": "independent_synthetic_spec"},
    )
    _write_json(
        fixture / "meta" / "run_meta.json",
        {
            "execution_backend": "synthetic_fixture_generator",
            "output_source": "independent_synthetic_spec",
            "is_synthetic": True,
            "count_as_real_evaluation": False,
            "page_count": 1,
            "input_sha256": pdf_hash,
            "oracle_sha256": oracle_hash,
        },
    )
    if spec.include_pages_detail:
        _write_json(fixture / "raw" / "pages_detail.json", [{"page_number": 1, "blocks": [{"type": "table"}]}])
    if spec.failure_mode == "missing_quality_artifact":
        (fixture / "evaluation" / "quality_checks.json").unlink()

    return {
        "spec": spec,
        "pdf_path": pdf_path,
        "fixture_path": fixture,
        "oracle_path": oracle_path,
        "pdf_sha256": pdf_hash,
        "gt_sha256": oracle_hash,
        "case_sha256": canonical_sha256(spec.to_dict()),
        "oracle": oracle,
    }
