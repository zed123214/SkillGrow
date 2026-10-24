"""Assessment-Skill Judge（LLM-as-a-Judge 辅助层）。

逻辑集中于单文件，实现 mock / live / skip 三模式。Judge 只产出诊断信号
（reading_order / table_structure / evidence_alignment），**不**参与
weighted_score 重算，也不覆盖 deterministic 维度。

行为矩阵（见 configs/judge.yaml）：

    enabled=false              -> 不调用、不写 judge_result.json，返回 disabled
    enabled=true + mode=mock   -> 读 mock_fixture，写 judge_result.json (mode=mock)
    enabled=true + mode=skip   -> 写 judge_result.json (mode=skipped)
    enabled=true + mode=live   -> 有 JUDGE_API_KEY 调 API；无 Key 降级为 skipped
    enabled=true + mode=live 且无 Key -> 同 skip：写 skipped + warning
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import yaml

from framework.context import FRAMEWORK_ROOT
from framework.logger import get_logger
from framework.sanitization import sanitize_exception_message

LOG = get_logger("llm_judge")

DEFAULT_CONFIG_PATH = FRAMEWORK_ROOT / "configs" / "judge.yaml"
PROMPT_TEMPLATE_PATH = FRAMEWORK_ROOT / "judge" / "judge_prompt_template.md"
ASSESSMENT_SKILL_PATH = FRAMEWORK_ROOT / "judge" / "assessment_skill.md"
JUDGE_SCHEMA_PATH = FRAMEWORK_ROOT / "judge" / "judge_result_schema.json"
DEFAULT_MAX_EXCERPT_CHARS = 12000
_JSON_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE | re.MULTILINE)

# Judge 只负责这三个维度（与 financial_accuracy 等确定性维度互不重叠）。
JUDGE_DIMENSIONS = ("reading_order", "table_structure", "evidence_alignment")
_SCORE_FIELDS = {
    "reading_order": "reading_order_score",
    "table_structure": "table_structure_score",
    "evidence_alignment": "evidence_alignment_score",
}

# 仅 enabled=true 时读取的输入（live 模式拼 prompt 用）。
JUDGE_INPUT_FILES = (
    "raw/parsed.md",
    "normalized/normalized_tables.json",
    "normalized/financial_summary.json",
    "evaluation/quality_checks.json",
    "meta/run_meta.json",
)


def _resolve(p: str | Path) -> Path:
    path = Path(p)
    return path if path.is_absolute() else (FRAMEWORK_ROOT / path).resolve()


def load_judge_config(config_path: str | Path | None = None) -> dict[str, Any]:
    path = _resolve(config_path) if config_path else DEFAULT_CONFIG_PATH
    if not path.exists():
        # 配置缺失时按「关闭」处理，保证默认流程不被打断。
        return {"enabled": False, "mode": "mock"}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def is_enabled(config: dict[str, Any]) -> bool:
    return bool(config.get("enabled", False))


def judged_dimensions(judge_result: dict[str, Any] | None) -> list[str]:
    """返回 judge_result 中实际给出非空分的维度名（用于 score_sources.llm_judge）。"""
    if not judge_result:
        return []
    out = []
    for dim in JUDGE_DIMENSIONS:
        if judge_result.get(_SCORE_FIELDS[dim]) is not None:
            out.append(dim)
    return out


def _output_dir(case: dict[str, Any]) -> Path:
    out_dir = case.get("output_dir")
    if not out_dir:
        raise ValueError("case missing output_dir")
    return _resolve(out_dir)


def _write_judge_result(output_dir: Path, judge_result: dict[str, Any]) -> Path:
    target = output_dir / "evaluation" / "judge_result.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(judge_result, ensure_ascii=False, indent=2), encoding="utf-8")
    return target


def _skipped_result(case_id: str, judge_version: str, reason: str) -> dict[str, Any]:
    return {
        "case_id": case_id,
        "judge_version": judge_version,
        "reading_order_score": None,
        "table_structure_score": None,
        "evidence_alignment_score": None,
        "deduction_items": [],
        "confidence": None,
        "mode": "skipped",
        "skip_reason": reason,
    }


def _load_mock_result(config: dict[str, Any], case_id: str, judge_version: str) -> dict[str, Any]:
    fixture_rel = config.get("mock_fixture") or "judge/fixtures/byd_caibao_judge_mock.json"
    fixture_path = _resolve(fixture_rel)
    data = json.loads(fixture_path.read_text(encoding="utf-8"))
    # 用运行中的 case_id 对齐 score_result / failure_trace，便于下游关联。
    data["case_id"] = case_id
    data.setdefault("judge_version", judge_version)
    data["mode"] = "mock"
    return data


def _read_judge_inputs(output_dir: Path) -> dict[str, str]:
    """读取 Judge 输入（live 模式拼 prompt 用）。缺失文件以空串占位。"""
    excerpts: dict[str, str] = {}
    for rel in JUDGE_INPUT_FILES:
        p = output_dir / rel
        try:
            excerpts[rel] = p.read_text(encoding="utf-8") if p.exists() else ""
        except Exception:
            excerpts[rel] = ""
    return excerpts


def _max_excerpt_chars(config: dict[str, Any]) -> int:
    live_cfg = config.get("live") or {}
    try:
        return int(live_cfg.get("max_excerpt_chars", DEFAULT_MAX_EXCERPT_CHARS))
    except (TypeError, ValueError):
        return DEFAULT_MAX_EXCERPT_CHARS


def _truncate_excerpt(text: str, max_chars: int) -> str:
    if not text or len(text) <= max_chars:
        return text or ""
    return text[:max_chars] + f"\n\n...[truncated, total {len(text)} chars]"


def _read_framework_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _render_judge_prompt(
    case_id: str,
    inputs: dict[str, str],
    config: dict[str, Any],
) -> tuple[str, str]:
    """渲染 judge_prompt_template.md，返回 (system, user) messages。"""
    template = _read_framework_text(PROMPT_TEMPLATE_PATH)
    if "## User" not in template:
        raise ValueError("judge_prompt_template.md missing ## User section")
    system_raw, user_raw = template.split("## User", 1)
    system_raw = system_raw.split("## System", 1)[-1].strip()
    user_raw = user_raw.strip()

    schema_text = _read_framework_text(JUDGE_SCHEMA_PATH).strip()
    max_chars = _max_excerpt_chars(config)
    replacements = {
        "assessment_skill_md": _read_framework_text(ASSESSMENT_SKILL_PATH).strip(),
        "judge_result_schema_json": schema_text,
        "case_id": case_id,
        "parsed_md_excerpt": _truncate_excerpt(inputs.get("raw/parsed.md", ""), max_chars),
        "normalized_tables_excerpt": _truncate_excerpt(
            inputs.get("normalized/normalized_tables.json", ""), max_chars
        ),
        "financial_summary_excerpt": _truncate_excerpt(
            inputs.get("normalized/financial_summary.json", ""), max_chars
        ),
        "quality_checks_excerpt": _truncate_excerpt(
            inputs.get("evaluation/quality_checks.json", ""), max_chars
        ),
        "run_meta_excerpt": _truncate_excerpt(inputs.get("meta/run_meta.json", ""), max_chars),
    }
    system = system_raw
    user = user_raw
    for key, value in replacements.items():
        system = system.replace(f"{{{{{key}}}}}", value)
        user = user.replace(f"{{{{{key}}}}}", value)
    user += '\n\n请仅输出一个 JSON 对象，字段符合 schema，mode 填 "live"。'
    return system, user


def _deepseek_chat(config: dict[str, Any], system: str, user: str) -> str:
    """调用 DeepSeek Chat Completions（OpenAI 兼容）。失败时抛异常供上层降级。"""
    live_cfg = config.get("live") or {}
    api_key_env = live_cfg.get("api_key_env", "JUDGE_API_KEY")
    api_key = os.environ.get(api_key_env)
    if not api_key:
        raise RuntimeError(f"{api_key_env} not set")

    endpoint = live_cfg.get("endpoint", "https://api.deepseek.com/chat/completions")
    model = live_cfg.get("model", "deepseek-chat")
    timeout = int(live_cfg.get("timeout_seconds", 60))
    temperature = float(live_cfg.get("temperature", 0.2))

    def _post(use_json_object: bool) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
        }
        if use_json_object:
            body["response_format"] = {"type": "json_object"}
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            endpoint,
            data=data,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            # Never preserve an HTTP response body: it can echo a request,
            # credentials, or provider-specific diagnostic data.
            raise RuntimeError(f"DeepSeek HTTP {exc.code}; response body redacted") from exc

    payload = _post(use_json_object=True)
    if not payload.get("choices"):
        # 部分模型/端点不支持 json_object，重试一次。
        LOG.warning("DeepSeek returned empty choices with json_object; retrying without response_format")
        payload = _post(use_json_object=False)

    choices = payload.get("choices") or []
    if not choices:
        raise RuntimeError("DeepSeek returned no choices; response payload redacted")
    content = (choices[0].get("message") or {}).get("content")
    if not content or not str(content).strip():
        raise RuntimeError("DeepSeek returned empty message content; response payload redacted")
    return str(content).strip()


def _strip_json_fence(raw: str) -> str:
    text = raw.strip()
    if text.startswith("```"):
        text = _JSON_FENCE_RE.sub("", text).strip()
    return text


def _validate_score(value: Any, field: str) -> float | None:
    if value is None:
        return None
    if not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a number or null, got {type(value).__name__}")
    score = float(value)
    if not 0.0 <= score <= 1.0:
        raise ValueError(f"{field} must be in [0, 1], got {score}")
    return score


def _parse_judge_json(raw: str, case_id: str, judge_version: str) -> dict[str, Any]:
    """解析并校验 Judge JSON，强制注入 case_id / judge_version / mode=live。"""
    data = json.loads(_strip_json_fence(raw))
    if not isinstance(data, dict):
        raise ValueError("judge response must be a JSON object")

    for field in _SCORE_FIELDS.values():
        data[field] = _validate_score(data.get(field), field)

    confidence = data.get("confidence")
    if confidence is not None:
        data["confidence"] = _validate_score(confidence, "confidence")

    deductions = data.get("deduction_items")
    if deductions is None:
        deductions = []
    if not isinstance(deductions, list):
        raise ValueError("deduction_items must be a list")
    for i, item in enumerate(deductions):
        if not isinstance(item, dict):
            raise ValueError(f"deduction_items[{i}] must be an object")
        dim = item.get("dimension")
        if dim not in JUDGE_DIMENSIONS:
            raise ValueError(f"deduction_items[{i}].dimension invalid: {dim}")
        if not item.get("reason"):
            raise ValueError(f"deduction_items[{i}].reason is required")

    data["case_id"] = case_id
    data["judge_version"] = judge_version
    data["deduction_items"] = deductions
    data["mode"] = "live"
    return data


def _call_live_judge(config: dict[str, Any], case_id: str, judge_version: str, inputs: dict[str, str]) -> dict[str, Any]:
    """live 模式：渲染 prompt → DeepSeek API → 解析 JSON。"""
    system, user = _render_judge_prompt(case_id, inputs, config)
    raw = _deepseek_chat(config, system, user)
    return _parse_judge_json(raw, case_id, judge_version)


def run_for_case(case: dict[str, Any], config: dict[str, Any] | None = None) -> dict[str, Any]:
    """对单个 case 运行 Judge。

    返回一个 stage 友好的 dict：
        {
          "enabled": bool,
          "status": "skipped" | "success",
          "mode": "disabled" | "mock" | "skipped" | "live",
          "judge_result": dict | None,
          "judge_result_path": str | None,
          "judge_dimensions": [...],
          "warning": str | None,
        }
    enabled=false 时不写任何文件，judge_result=None。
    """
    config = config if config is not None else load_judge_config()
    case_id = case.get("case_id", "")
    judge_version = config.get("judge_version", "assessment_skill_v1")

    if not is_enabled(config):
        return {
            "enabled": False,
            "status": "skipped",
            "mode": "disabled",
            "judge_result": None,
            "judge_result_path": None,
            "judge_dimensions": [],
            "warning": None,
        }

    mode = (config.get("mode") or "mock").lower()
    output_dir = _output_dir(case)
    warning: str | None = None

    if mode == "mock":
        judge_result = _load_mock_result(config, case_id, judge_version)
    elif mode == "skip":
        judge_result = _skipped_result(case_id, judge_version, "mode=skip (configured)")
    elif mode == "live":
        live_cfg = config.get("live") or {}
        api_key_env = live_cfg.get("api_key_env", "JUDGE_API_KEY")
        if not os.environ.get(api_key_env):
            warning = f"{api_key_env} not set; live judge degraded to skipped"
            LOG.warning(warning)
            judge_result = _skipped_result(case_id, judge_version, warning)
        else:
            try:
                inputs = _read_judge_inputs(output_dir)
                judge_result = _call_live_judge(config, case_id, judge_version, inputs)
                judge_result["mode"] = "live"
            except Exception as exc:  # 降级为 skipped，绝不让 pipeline 失败。
                warning = f"live judge failed, degraded to skipped: {sanitize_exception_message(exc)}"
                LOG.warning(warning)
                judge_result = _skipped_result(case_id, judge_version, warning)
    else:
        warning = f"unknown judge mode '{mode}', treated as skip"
        LOG.warning(warning)
        judge_result = _skipped_result(case_id, judge_version, warning)

    target = _write_judge_result(output_dir, judge_result)
    return {
        "enabled": True,
        "status": "success",
        "mode": judge_result.get("mode"),
        "judge_result": judge_result,
        "judge_result_path": str(target),
        "judge_dimensions": judged_dimensions(judge_result),
        "warning": warning,
    }
