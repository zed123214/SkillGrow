# FinDoc Guard 运行指南

[项目首页](../README.md) · [English](README.md)

## 合成回放

在本目录执行：

```bash
python -m tools.run_t6_baseline --output runs/demo-001 --verification-id demo-001
```

工具会生成 20 个合成用例并运行质量工作流。汇总报告位于 `runs/demo-001/baseline_summary.md`，每份文档的结果通过对应的 `manifest.json` 索引。每轮使用新的输出目录和 verification ID。

## Standard 解析产物

`findoc guard` 读取 PDF 的输入身份以及配套的 standard 解析产物。目录结构为：

```text
sample-standard/
├── raw/parsed.md
├── normalized/normalized_tables.json
├── normalized/financial_summary.json
├── evaluation/quality_checks.json
└── meta/run_meta.json
```

```bash
python -m findoc guard --input sample.pdf --fixture-output-dir sample-standard --run-root runs --policy configs/findoc_quality_policy.json
```

通过 `--run-id` 指定本轮标识，使用 `--parent-run-id` 记录重试来源。复核结果会包含 `human_review_request.json`。

## 质量策略

编辑 [configs/findoc_quality_policy.json](configs/findoc_quality_policy.json)：

| 配置 | 默认值 | 含义 |
| --- | --- | --- |
| `minimum_evidence_coverage` | 0.9 | 最低证据覆盖率 |
| `minimum_gt_score` | 0.95 | 最低 GT 分数 |
| `judge_failure_is_degraded` | true | Judge 失败时使用降级状态 |
| `high_confidence_judge_fail_review` | 0.8 | Judge 失败信号进入复核的置信度阈值 |

## Skill 评测工具

安装评测模块依赖后，可以使用 pytest、失败追踪、报告和候选补丁工具：

```bash
python -m pip install -r requirements.txt
python run.py --help
```

配置入口为 `configs/`，YAML 用例位于 `testcases/`，独立答案位于 `evaluation/ground_truth/`。已有 standard 输出可以通过 `python run.py --import-real-outputs ../outputs` 导入 `data/real_las_outputs/`，供对应的 fixture 用例使用。

完整 pytest 入口为 `python -m pytest -m offline -q`。FinDoc 的标准库测试命令见[项目首页](../README.md#开发与测试)。

## 源码目录

- `findoc/`：DocumentIR、证据、Judge、门控及运行管理。
- `framework/`：用例加载、评分、报告和共享脱敏工具。
- `optimizer/`：候选 patch、验证集与回归门控。
- `tests/`、`static_tests/`：功能回归与静态检查。
- `tools/run_t6_baseline.py`：合成回放入口。
