# FinDoc Guard Framework

[Project overview](../README.md) · [中文运行指南](README_zh.md)

FinDoc Guard turns standard financial-document outputs into a DocumentIR, an Evidence Pack, a structured quality decision, and a manifest-backed report.

## Quick start

Run the built-in 20-case synthetic fixture replay from this directory with Python:

```bash
python -m tools.run_t6_baseline --output runs/demo-001 --verification-id demo-001
```

Open `runs/demo-001/baseline_summary.md` for the summary and `baseline_manifest.json` for case-level results. Use a new output directory and verification ID for each run.

## Process standard outputs

Provide a PDF for input identity and its matching standard-output directory:

```bash
python -m findoc guard --input sample.pdf --fixture-output-dir sample-standard --run-root runs --policy configs/findoc_quality_policy.json
```

The standard directory contains `raw/parsed.md`, `normalized/normalized_tables.json`, `normalized/financial_summary.json`, `evaluation/quality_checks.json`, and `meta/run_meta.json`.

Each run has its own ID, manifest, stage attempts, and SHA-256 artifact registry. Quality decisions are `accepted`, `rejected`, `needs_review`, or `degraded`. Review cases include a structured review request.

## Evaluation tools

Install the evaluation dependencies and inspect the CLI:

```bash
python -m pip install -r requirements.txt
python run.py --help
```

Use `configs/` for evaluation settings, `testcases/` for YAML cases, and `evaluation/ground_truth/` for reference answers. Import existing standard outputs with `python run.py --import-real-outputs ../outputs` to populate the corresponding fixture cases.

The full offline pytest entry point is `python -m pytest -m offline -q`. The [project README](../README.md) includes the standard-library FinDoc regression command and source map.
