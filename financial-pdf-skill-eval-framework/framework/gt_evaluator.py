"""Ground Truth（真值）评估，委托给该 Skill 的 evaluate_with_ground_truth.py 进行处理。

P0 行为补强（不破坏既有接口）：
 - 仅当 ``source`` ∈ {"manual", "human_verified", "manual_verified"} 时才参与
   准确率（``accuracy_eligible``）；其他 source（``todo_manual_verify``、
   ``template``、``synthetic``、``auto``、``skill_output``、空值等）一律视为
   ``no_ground_truth``，与旧版语义一致。
 - 在调用 Skill 的 ``evaluate_with_ground_truth.py`` 前，**先过滤**掉
   ``metrics[].expected`` 为空 / 仅空白的条目；这些条目被记录到
   ``skipped_expected_items`` 里，**不进入准确率分母**。
 - 过滤后的 GT 写入临时文件（``tempfile.NamedTemporaryFile``），原 GT 文件保持只读。
 - 返回结构在原有字段基础上新增：
     ``eligible_count``           : 进入准确率分母的 metric 数量。
     ``skipped_expected_items``   : 被跳过的 metric 列表，仅记录定位字段。
     ``accuracy_eligible``        : 等价于 numeric_accuracy（保留单独字段以便日后区分）。
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from framework.context import FRAMEWORK_ROOT, load_config
from framework.logger import get_logger
from framework.sanitization import process_output_metadata, sanitize_exception_message

LOG = get_logger("gt_evaluator")


# Public constants so other modules (static_tests / scoring_model) can reuse the rule.
MANUAL_SOURCES = {"manual", "human_verified", "manual_verified"}
NON_MANUAL_SOURCES = {"todo_manual_verify", "template", "harness", "synthetic", "auto", "skill_output", ""}


def normalize_number(value: Any) -> float | None:
    """Tolerant number parser. Handles 1,234.56 / 1，234.56 / (18,970,274.67) / "-"."""
    if value is None:
        return None
    s = str(value).strip()
    if s in {"", "-", "—", "－", "N/A", "n/a"}:
        return None
    s = s.replace("，", ",").replace("（", "(").replace("）", ")")
    negative = s.startswith("(") and s.endswith(")")
    if negative:
        s = s[1:-1]
    if s.startswith("-"):
        negative = True
        s = s[1:]
    s = s.replace(",", "").replace(" ", "")
    if not re.fullmatch(r"-?\d+(\.\d+)?", s):
        return None
    try:
        v = float(s)
        return -v if negative else v
    except Exception:
        return None


def _resolve(p: str | Path) -> Path:
    path = Path(p)
    if path.is_absolute():
        return path
    return (FRAMEWORK_ROOT / path).resolve()


def _expected_filled(metric: dict[str, Any]) -> bool:
    val = metric.get("expected")
    if val is None:
        return False
    return bool(str(val).strip())


def _metric_locator(metric: dict[str, Any]) -> dict[str, Any]:
    return {
        "statement": metric.get("statement"),
        "item": metric.get("item"),
        "period": metric.get("period"),
    }


def filter_metrics(metrics: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return (eligible, skipped) given a list of GT metric entries."""
    eligible: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for m in metrics or []:
        if _expected_filled(m):
            eligible.append(m)
        else:
            skipped.append(_metric_locator(m))
    return eligible, skipped


def evaluate(output_dir: str | Path, ground_truth_path: str | Path | None) -> dict:
    output_dir = _resolve(output_dir)
    summary = output_dir / "normalized" / "financial_summary.json"
    if not summary.exists():
        return {"status": "skipped", "reason": "financial_summary.json missing", "no_ground_truth": False}

    if not ground_truth_path:
        return {"status": "skipped", "reason": "no ground_truth declared in case", "no_ground_truth": True}

    gt_path = _resolve(ground_truth_path)
    if not gt_path.exists():
        return {"status": "no_ground_truth", "reason": "ground_truth file not found", "no_ground_truth": True}

    try:
        gt_data = json.loads(gt_path.read_text(encoding="utf-8"))
    except Exception as e:
        return {"status": "failed", "reason": f"invalid ground_truth JSON: {sanitize_exception_message(e)}", "no_ground_truth": True}
    metrics = gt_data.get("metrics", []) if isinstance(gt_data, dict) else []

    source = (gt_data.get("source") or "").strip().lower() if isinstance(gt_data, dict) else ""
    is_manual = source in MANUAL_SOURCES

    eligible, skipped = filter_metrics(metrics)
    pending = len(skipped)

    if not is_manual:
        return {
            "status": "no_ground_truth",
            "reason": f"ground_truth source={source!r} is not human-verified; refusing to compute accuracy",
            "no_ground_truth": True,
            "pending_manual_verify_count": pending,
            "skipped_expected_items": skipped,
            "eligible_count": 0,
            "accuracy_eligible": None,
        }

    if not eligible:
        return {
            "status": "no_ground_truth",
            "reason": "ground_truth has no expected values yet (all pending_manual_verify)",
            "no_ground_truth": True,
            "pending_manual_verify_count": pending,
            "skipped_expected_items": skipped,
            "eligible_count": 0,
            "accuracy_eligible": None,
        }

    cfg = load_config()
    script = cfg.skill.gt_eval_script
    result_path = output_dir / "evaluation" / "gt_eval_result.json"
    result_path.parent.mkdir(parents=True, exist_ok=True)
    if not script.exists():
        return {"status": "failed", "reason": "gt evaluator script missing", "no_ground_truth": False}

    # Build filtered GT in a temp file so the on-disk GT is never mutated.
    filtered_gt = dict(gt_data)
    filtered_gt["metrics"] = eligible
    # Stash provenance for traceability inside the temp file.
    filtered_gt["__filtered_from__"] = str(gt_path)
    filtered_gt["__skipped_expected_items_count__"] = pending

    with tempfile.NamedTemporaryFile(
        prefix=f"gt_filtered_{output_dir.name}_",
        suffix=".json",
        mode="w",
        encoding="utf-8",
        delete=False,
    ) as tf:
        json.dump(filtered_gt, tf, ensure_ascii=False, indent=2)
        tmp_gt_path = Path(tf.name)

    try:
        cmd = [
            sys.executable, str(script),
            "--financial-summary", str(summary),
            "--ground-truth", str(tmp_gt_path),
            "--output", str(result_path),
        ]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False, timeout=120)
        except Exception as e:
            return {"status": "failed", "reason": f"gt eval subprocess error: {sanitize_exception_message(e)}", "no_ground_truth": False}
        if proc.returncode != 0:
            return {
                "status": "failed",
                "reason": f"gt evaluator exited with code {proc.returncode}; process output redacted",
                "process_output": process_output_metadata(proc.stdout, proc.stderr),
                "no_ground_truth": False,
            }

        try:
            result_data = json.loads(result_path.read_text(encoding="utf-8"))
        except Exception as e:
            return {"status": "failed", "reason": f"invalid gt_eval_result.json: {sanitize_exception_message(e)}", "no_ground_truth": False}

        # Annotate the on-disk gt_eval_result.json with eligibility metadata so
        # downstream consumers (scoring_model / reports) don't have to re-derive it.
        result_data["eligible_count"] = len(eligible)
        result_data["skipped_expected_items"] = skipped
        result_data["accuracy_eligible"] = result_data.get("numeric_accuracy")
        try:
            result_path.write_text(json.dumps(result_data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

        return {
            "status": "success",
            "no_ground_truth": False,
            "pending_manual_verify_count": pending,
            "skipped_expected_items": skipped,
            "eligible_count": len(eligible),
            "exact_match_accuracy": result_data.get("exact_match_accuracy"),
            "numeric_accuracy": result_data.get("numeric_accuracy"),
            "accuracy_eligible": result_data.get("numeric_accuracy"),
            "failed_items_count": len(result_data.get("failed_items", []) or []),
            "result_path": str(result_path),
        }
    finally:
        try:
            tmp_gt_path.unlink(missing_ok=True)
        except Exception:
            pass
