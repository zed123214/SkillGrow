"""Aggregate case results into Markdown reports."""
from __future__ import annotations

import json
import subprocess
import sys
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from framework import gt_evaluator, output_contract
from framework.context import FRAMEWORK_ROOT, load_config
from framework.sanitization import repository_relative_path


# P0 failure taxonomy used by `write_failure_cases_markdown`.
FAILURE_CATEGORIES = (
    "CONTRACT_MISSING",
    "AUTHENTICITY_INVALID",
    "GT_UNAVAILABLE",
    "NUMERIC_MISMATCH",
    "TABLE_STRUCTURE_LOW",
    "BACKEND_SKIPPED",
    "STATIC_CHECK_FAILED",
    "OTHER",
)


def _resolve(p: str | Path) -> Path:
    path = Path(p)
    if path.is_absolute():
        return path
    return (FRAMEWORK_ROOT / path).resolve()


def _judge_enabled() -> bool:
    path = FRAMEWORK_ROOT / "configs" / "judge.yaml"
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return False
    return bool(data.get("enabled", False))


def summarize_case(case: dict, invocation: dict | None = None, validations: list[dict] | None = None) -> dict:
    output_dir = invocation.get("output_dir") if invocation else case.get("output_dir")
    if not output_dir:
        return {"case_id": case.get("case_id"), "missing": True}
    output_dir = _resolve(output_dir)
    meta = output_contract.read_run_meta(output_dir)
    qc = output_contract.read_quality_checks(output_dir)
    table_stats = qc.get("table_statistics") or {}
    metric_stats = qc.get("metric_statistics") or {}
    auth = qc.get("data_authenticity") or {}

    # Output contract pass flag (default validate)
    contract = output_contract.validate_standard_output(output_dir, case.get("output_profile", "standard"))

    gt = gt_evaluator.evaluate(output_dir, case.get("ground_truth"))

    # Two distinct concepts:
    # - count_as_real_execution: a real_las / real_openclaw output was actually produced.
    # - count_as_accuracy_evaluation: this case has human-verified Ground Truth and
    #   yields a real numeric_accuracy. The Skill's run_meta field name
    #   "count_as_real_evaluation" historically conflated these; we expose both
    #   explicit fields and KEEP the legacy field for backwards compatibility.
    raw_real_eval = auth.get("count_as_real_evaluation", meta.get("count_as_real_evaluation"))
    count_as_real_execution = bool(raw_real_eval) and (auth.get("execution_backend") or meta.get("execution_backend")) in {"real_las", "real_openclaw"}
    case_no_accuracy = bool(case.get("count_as_real_evaluation") is False)
    count_as_accuracy_evaluation = (
        gt.get("status") == "success"
        and not gt.get("no_ground_truth")
        and not case_no_accuracy
    )

    validations = validations or []

    # Read score_result.json if previously produced by the pipeline. P0 does not
    # require it to exist; summaries built from legacy fixtures still work.
    score_path = output_dir / "evaluation" / "score_result.json"
    score_result: dict[str, Any] | None = None
    if score_path.exists():
        try:
            score_result = json.loads(score_path.read_text(encoding="utf-8"))
        except Exception:
            score_result = None

    # Optional judge_result.json (only present when judge.enabled=true).
    judge_path = output_dir / "evaluation" / "judge_result.json"
    judge_result: dict[str, Any] | None = None
    if _judge_enabled() and judge_path.exists():
        try:
            judge_result = json.loads(judge_path.read_text(encoding="utf-8"))
        except Exception:
            judge_result = None

    return {
        "case_id": case.get("case_id"),
        "case_name": case.get("name", case.get("case_id")),
        "backend": case.get("backend"),
        "output_profile": case.get("output_profile", "standard"),
        "output_dir": repository_relative_path(output_dir, FRAMEWORK_ROOT.parent),
        "run_status": invocation.get("status") if invocation else "unknown",
        "output_contract_passed": bool(contract.get("passed")),
        "execution_backend": auth.get("execution_backend") or meta.get("execution_backend"),
        "output_source": meta.get("output_source"),
        "is_synthetic": auth.get("is_synthetic", meta.get("is_synthetic")),
        "count_as_real_evaluation": raw_real_eval,
        "count_as_real_execution": count_as_real_execution,
        "count_as_accuracy_evaluation": count_as_accuracy_evaluation,
        "raw_table_count": table_stats.get("raw_table_count"),
        "financial_table_count": table_stats.get("financial_table_count"),
        "layout_table_count": table_stats.get("layout_table_count"),
        "signature_table_count": table_stats.get("signature_table_count"),
        "unknown_table_count": table_stats.get("unknown_table_count"),
        "metric_record_count": metric_stats.get("metric_record_count"),
        "unique_item_count": metric_stats.get("unique_item_count"),
        "unique_statement_count": metric_stats.get("unique_statement_count"),
        "exact_match_accuracy": gt.get("exact_match_accuracy"),
        "numeric_accuracy": gt.get("numeric_accuracy"),
        "failed_items_count": gt.get("failed_items_count"),
        "no_ground_truth": gt.get("no_ground_truth", False),
        "pending_manual_verify_count": gt.get("pending_manual_verify_count"),
        "eligible_count": gt.get("eligible_count"),
        "validations": validations,
        "score_result": score_result,
        "judge_result": judge_result,
    }


def _md_table(rows: list[dict], columns: list[str]) -> str:
    head = "| " + " | ".join(columns) + " |"
    sep = "| " + " | ".join(["---"] * len(columns)) + " |"
    body = []
    for r in rows:
        body.append("| " + " | ".join(str(r.get(c, "")) for c in columns) + " |")
    return "\n".join([head, sep, *body])


def write_summary_markdown(summaries: list[dict], path: Path) -> None:
    path = _resolve(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat()

    contract_rows = [s for s in summaries if not s.get("missing")]
    real_las_rows = [s for s in summaries if s.get("execution_backend") == "real_las"]
    accuracy_rows = [s for s in summaries if s.get("count_as_accuracy_evaluation")]
    no_gt_rows = [s for s in summaries if s.get("no_ground_truth") or not s.get("count_as_accuracy_evaluation")]

    lines = [
        f"# Evaluation Summary",
        f"_generated: {now} UTC_",
        "",
        "> **Output completeness is NOT parsing accuracy.**",
        "> **Accuracy is computed only against human-verified Ground Truth (`source: manual` / `human_verified`).**",
        "> real_las outputs are fixtures, not Ground Truth.",
        "> harness-only / template / todo GT files do NOT contribute to accuracy.",
        "> real_las costs money and is disabled by default.",
        "",
        "## Key counters",
        f"- real-execution samples (real_las / real_openclaw output produced): **{sum(1 for s in summaries if s.get('count_as_real_execution'))}**",
        f"- accuracy-evaluation samples (real execution **AND** human-verified GT): **{sum(1 for s in summaries if s.get('count_as_accuracy_evaluation'))}**",
        "",
        "## 1. Output Contract",
        _md_table(contract_rows, ["case_id", "backend", "output_profile", "output_contract_passed", "run_status"]) if contract_rows else "_no cases_",
        "",
        "## 2. real_las fixture stats (execution evidence, NOT accuracy)",
        _md_table(real_las_rows, ["case_id", "raw_table_count", "financial_table_count", "metric_record_count", "is_synthetic", "count_as_real_execution", "count_as_accuracy_evaluation"]) if real_las_rows else "_no real_las fixtures_",
        "",
        "## 3. Ground Truth accuracy (human-verified GT only)",
        _md_table(accuracy_rows, ["case_id", "exact_match_accuracy", "numeric_accuracy", "failed_items_count", "pending_manual_verify_count"]) if accuracy_rows else "_no human-verified Ground Truth filled in yet — accuracy is not reported_",
        "",
        "## 4. Cases without (usable) Ground Truth — output-contract / structure only",
        _md_table(no_gt_rows, ["case_id", "backend", "count_as_real_execution", "pending_manual_verify_count"]) if no_gt_rows else "_none_",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def _classify_validation(v: dict) -> str:
    vtype = (v.get("type") or "").lower()
    if vtype == "output_contract":
        return "CONTRACT_MISSING"
    if vtype == "data_authenticity":
        return "AUTHENTICITY_INVALID"
    if vtype.startswith("gt_") or vtype == "no_ground_truth_allowed":
        # Distinguish "no GT" from "GT exists but accuracy below threshold".
        actual = v.get("actual")
        if isinstance(actual, (int, float)) and v.get("expected") is not None:
            return "NUMERIC_MISMATCH"
        return "GT_UNAVAILABLE"
    if vtype.startswith("table_stat") or vtype.startswith("metric_stat"):
        return "TABLE_STRUCTURE_LOW"
    if vtype == "backend_eq":
        return "BACKEND_SKIPPED"
    return "OTHER"


def _classify_summary(s: dict) -> list[str]:
    cats: list[str] = []
    bad = [v for v in s.get("validations", []) if not v.get("passed")]
    for v in bad:
        cats.append(_classify_validation(v))
    if not s.get("output_contract_passed"):
        cats.append("CONTRACT_MISSING")
    if s.get("run_status") in {"skipped", "skip"}:
        cats.append("BACKEND_SKIPPED")
    if s.get("no_ground_truth"):
        cats.append("GT_UNAVAILABLE")
    if s.get("run_status") == "failed":
        cats.append("OTHER")
    # de-dup preserving order
    seen: set[str] = set()
    out: list[str] = []
    for c in cats:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out


def write_failure_cases_markdown(summaries: list[dict], path: Path) -> None:
    path = _resolve(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    grouped: "OrderedDict[str, list[tuple[dict, list[dict]]]]" = OrderedDict(
        (cat, []) for cat in FAILURE_CATEGORIES
    )
    total = 0
    for s in summaries:
        bad_validations = [v for v in s.get("validations", []) if not v.get("passed")]
        is_failure = bool(bad_validations) or not s.get("output_contract_passed") or s.get("run_status") == "failed"
        if not is_failure:
            continue
        cats = _classify_summary(s) or ["OTHER"]
        for cat in cats:
            grouped.setdefault(cat, []).append((s, bad_validations))
        total += 1

    lines = [f"# Failure Cases", f"_count: {total}_", ""]
    if total == 0:
        lines.append("_No failures detected._")

    for cat in FAILURE_CATEGORIES:
        entries = grouped.get(cat) or []
        if not entries:
            continue
        lines.append(f"## {cat} ({len(entries)})")
        for s, bad in entries:
            lines.append(f"### {s.get('case_id')}")
            lines.append(f"- backend: {s.get('backend')}")
            lines.append(f"- run_status: {s.get('run_status')}")
            lines.append(f"- output_contract_passed: {s.get('output_contract_passed')}")
            lines.append(f"- output_dir: {s.get('output_dir')}")
            if bad:
                lines.append("- failed validations:")
                for b in bad:
                    lines.append(f"  - **{b.get('type')}**: {b.get('message')} (expected={b.get('expected')}, actual={b.get('actual')})")
            lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")


SCORE_DIMENSIONS = (
    "output_contract",
    "data_authenticity",
    "table_structure",
    "financial_accuracy",
    "abnormal_handling",
    "cost_performance",
)


def _summary_with_score(summaries: list[dict]) -> list[dict]:
    rows: list[dict] = []
    for s in summaries:
        sr = s.get("score_result") or {}
        dims = sr.get("dimensions") or {}
        row = {
            "case_id": s.get("case_id"),
            "backend": s.get("backend"),
            "weighted_score": sr.get("weighted_score"),
            "level": sr.get("level"),
            "weighting_note": sr.get("weighting_note") or "",
        }
        for dim in SCORE_DIMENSIONS:
            row[dim] = dims.get(dim) if dim in dims else None
        rows.append(row)
    return rows


def _judge_section_lines(summaries: list[dict]) -> list[str]:
    """渲染「Judge 辅助评分（不参与总分）」小节。

    judge.enabled=false 时各 case 无 judge_result，本节显示「Judge 未启用」。
    """
    judged = [s for s in summaries if s.get("judge_result")]
    lines = [
        "## Judge 辅助评分（不参与总分）",
        "> LLM-as-a-Judge 仅作结构质量诊断（reading_order / table_structure / "
        "evidence_alignment），**不**参与 weighted_score 重算，也不覆盖 financial_accuracy。",
        "",
    ]
    if not judged:
        lines.append("_Judge 未启用（judge.enabled=false）或无 judge_result.json。_")
        lines.append("")
        return lines

    rows = []
    for s in judged:
        jr = s.get("judge_result") or {}
        rows.append({
            "case_id": s.get("case_id"),
            "mode": jr.get("mode"),
            "reading_order_score": jr.get("reading_order_score"),
            "table_structure_score": jr.get("table_structure_score"),
            "evidence_alignment_score": jr.get("evidence_alignment_score"),
            "confidence": jr.get("confidence"),
        })
    lines.append(_md_table(rows, [
        "case_id", "mode", "reading_order_score", "table_structure_score",
        "evidence_alignment_score", "confidence",
    ]))
    lines.append("")

    for s in judged:
        jr = s.get("judge_result") or {}
        deductions = jr.get("deduction_items") or []
        if not deductions:
            continue
        lines.append(f"### {s.get('case_id')} — deduction_items")
        lines.append(_md_table(
            [{"dimension": d.get("dimension"), "reason": d.get("reason"), "evidence": d.get("evidence")} for d in deductions],
            ["dimension", "reason", "evidence"],
        ))
        lines.append("")
    return lines


def write_score_summary_markdown(summaries: list[dict], path: Path) -> None:
    """Render P0 score summary table: case × dimension scores × weighted_score."""
    path = _resolve(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = _summary_with_score(summaries)
    now = datetime.now(timezone.utc).isoformat()
    columns = ["case_id", "backend", *SCORE_DIMENSIONS, "weighted_score", "level"]

    if any(r.get("weighted_score") is not None for r in rows):
        table = _md_table(rows, columns)
    else:
        table = "_no score_result.json found in any case output_dir. Run `python run.py --pipeline ...` first._"

    notes = [r for r in rows if r.get("weighting_note")]
    notes_block = ""
    if notes:
        bullet = "\n".join(f"- **{r['case_id']}**: {r['weighting_note']}" for r in notes)
        notes_block = f"\n## Weighting notes\n{bullet}\n"

    lines = [
        "# P0 Score Summary",
        f"_generated: {now} UTC_",
        "",
        "> Scores are derived solely from deterministic signals (output contract, ",
        "> data authenticity, table structure, GT numeric accuracy, abnormal handling, ",
        "> and cost/performance). P0 does **not** use any LLM judge.",
        "",
        "## Cases × Dimensions",
        table,
        notes_block,
        "",
        *_judge_section_lines(summaries),
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def write_final_project_report(summaries: list[dict], path: Path) -> None:
    """Compose a final project-style report on top of summary + Skill's generator."""
    path = _resolve(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    cfg = load_config()

    score_rows = _summary_with_score(summaries)
    has_scores = any(r.get("weighted_score") is not None for r in score_rows)

    head = [
        "# Final Project Report — financial-pdf-parse-doubao-eval",
        f"_generated: {datetime.now(timezone.utc).isoformat()} UTC_",
        "",
        "## Caveats",
        "- Output completeness != parsing accuracy.",
        "- Accuracy is reported only against **human-verified** Ground Truth (`source: manual` / `human_verified`).",
        "- `data/real_las_outputs/*` are real_las fixtures, not Ground Truth.",
        "- harness / template / todo GT files do NOT contribute to accuracy.",
        "- `count_as_real_execution` (real_las output produced) is distinct from `count_as_accuracy_evaluation` (human-verified GT exists).",
        "- real_las costs money; default-off.",
        "- real_openclaw backend is unverified and skipped by default.",
        "",
        f"## Counters",
        f"- real-execution samples: **{sum(1 for s in summaries if s.get('count_as_real_execution'))}**",
        f"- accuracy-evaluation samples: **{sum(1 for s in summaries if s.get('count_as_accuracy_evaluation'))}**",
        "",
        "## Cases overview",
        _md_table([s for s in summaries if not s.get("missing")], [
            "case_id", "backend", "execution_backend", "output_contract_passed",
            "raw_table_count", "financial_table_count", "metric_record_count",
            "numeric_accuracy", "count_as_real_execution", "count_as_accuracy_evaluation",
        ]) or "_no cases_",
        "",
        "## P0 评分摘要 (deterministic, no LLM judge)",
        (
            _md_table(score_rows, ["case_id", "backend", *SCORE_DIMENSIONS, "weighted_score", "level"])
            if has_scores
            else "_No score_result.json found. Run `python run.py --pipeline ...` to populate scores._"
        ),
        "",
        "See `reports/markdown/score_summary.md` for the standalone score table and weighting notes.",
        "",
        *_judge_section_lines(summaries),
    ]

    # Attempt to enrich with Skill's own final-report generator
    extra = ""
    script = cfg.skill.final_report_script
    outputs_dir = cfg.paths.get("real_las_outputs_dir") or _resolve("data/real_las_outputs")
    if script.exists() and outputs_dir.exists():
        tmp_report = path.with_name(path.stem + "_skill.md")
        cmd = [sys.executable, str(script), "--outputs-dir", str(outputs_dir), "--report", str(tmp_report)]
        try:
            subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=120)
            if tmp_report.exists():
                extra = "\n\n## Skill-generated section (from generate_final_project_report.py)\n\n" + tmp_report.read_text(encoding="utf-8")
        except Exception:
            pass

    path.write_text("\n".join(head) + extra, encoding="utf-8")


def collect_summaries_from_manifest(manifest_path: Path | str | None = None) -> list[dict]:
    cfg = load_config()
    if manifest_path is None:
        manifest_path = FRAMEWORK_ROOT / "configs" / "dataset_manifest.yaml"
    manifest_path = _resolve(manifest_path)
    if not manifest_path.exists():
        return []
    data = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    summaries = []
    for f in data.get("real_las_fixtures") or []:
        case = {
            "case_id": f.get("case_id"),
            "name": f.get("case_id"),
            "backend": "fixture",
            "output_profile": "standard",
            "output_dir": f.get("source_output_dir"),
            "ground_truth": f.get("ground_truth"),
            "count_as_real_evaluation": f.get("count_as_real_evaluation"),
        }
        invocation = {"status": "fixture", "output_dir": case["output_dir"]}
        summaries.append(summarize_case(case, invocation, []))
    return summaries
