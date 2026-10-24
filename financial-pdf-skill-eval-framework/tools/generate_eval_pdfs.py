"""Generate synthetic financial PDF evaluation samples and matching metadata.

The CASES list is the single source of truth. PDF rendering, ground-truth JSON,
case YAML, and the dataset manifest all read from the same case dictionaries.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import tempfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
GT_SOURCE_DEFAULT = "todo_manual_verify"
VERIFIED_NOTE_SUFFIX = " Human verification date: YYYY-MM-DD."
FONT_NAME = "SyntheticCN"


def metric_spec(
    table_id: str,
    row_label: str,
    col: int,
    *,
    item: str | None = None,
    period: str | None = None,
    unit: str | None = None,
    statement: str | None = None,
) -> dict[str, Any]:
    return {
        "table_id": table_id,
        "row_label": row_label,
        "col": col,
        "item": item,
        "period": period,
        "unit": unit,
        "statement": statement,
    }


def make_metric(case: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
    table = next(t for t in case["tables"] if t["id"] == spec["table_id"])
    row = next(r for r in table["rows"] if r and r[0] == spec["row_label"])
    col = int(spec["col"])
    header_rows = int(table.get("header_rows", 1))
    period = spec.get("period")
    if period is None:
        for header in reversed(table["rows"][:header_rows]):
            if header[col]:
                period = header[col]
                break
    if period is None:
        period = ""
    item = spec.get("item") or spec["row_label"]
    statement = spec.get("statement") or table.get("statement") or table.get("caption") or ""
    unit = spec.get("unit") or table.get("unit", "")
    return {
        "statement": statement,
        "item": item,
        "period": period,
        "expected": row[col],
        "unit": unit,
        "page": table.get("page", 1),
        "evidence": f"{table['id']} {spec['row_label']} col {period}",
    }


def make_case(**kwargs: Any) -> dict[str, Any]:
    case = dict(kwargs)
    specs = case.pop("metric_specs", [])
    case["metrics"] = [make_metric(case, spec) for spec in specs]
    return case


CASES: list[dict[str, Any]] = [
    make_case(
        case_id="input_007_income_statement",
        filename="input_007_income_statement.pdf",
        band="normal",
        pages=1,
        language="zh-CN",
        tags=["offline", "synthetic_pdf", "normal", "income_statement"],
        image_only=False,
        company_name="星澜智能制造股份有限公司",
        name="简体中文合并利润表（合成）",
        title_by_page={1: "合并利润表"},
        yaml_extra={},
        diff_note="补充中文利润表场景，与已有资产负债表片段不同。",
        tests="financial_table>=1; metric_record>=5; GT numeric>=0.80",
        tables=[
            {
                "id": "main_table",
                "statement": "合并利润表",
                "caption": "合并利润表",
                "unit": "万元",
                "page": 1,
                "header_rows": 1,
                "col_widths": [250, 120, 120],
                "rows": [
                    ["项目", "2025年度", "2024年度"],
                    ["营业总收入", "12,580.36", "10,943.88"],
                    ["其中：主营业务收入", "11,902.14", "10,366.27"],
                    ["营业总成本", "9,874.22", "8,921.36"],
                    ["销售费用", "1,120.45", "988.17"],
                    ["管理费用", "1,432.08", "1,206.55"],
                    ["信用减值损失", "(86.72)", "(44.13)"],
                    ["营业利润", "2,706.14", "2,022.52"],
                    ["净利润", "2,294.21", "1,704.10"],
                ],
            }
        ],
        metric_specs=[
            metric_spec("main_table", "营业总收入", 1),
            metric_spec("main_table", "其中：主营业务收入", 1),
            metric_spec("main_table", "营业总成本", 1),
            metric_spec("main_table", "销售费用", 1),
            metric_spec("main_table", "管理费用", 1),
            metric_spec("main_table", "信用减值损失", 1),
            metric_spec("main_table", "营业利润", 1),
            metric_spec("main_table", "净利润", 1),
        ],
    ),
    make_case(
        case_id="input_008_cashflow_supplement",
        filename="input_008_cashflow_supplement.pdf",
        band="normal",
        pages=1,
        language="zh-CN",
        tags=["offline", "synthetic_pdf", "normal", "cashflow_supplement"],
        image_only=False,
        company_name="云栈新材股份有限公司",
        name="现金流量表补充资料（合成）",
        title_by_page={1: "现金流量表补充资料"},
        yaml_extra={},
        diff_note="补充现金流量表补充资料样式，覆盖非资产负债表指标。",
        tests="financial_table>=1; metric_record>=5; GT numeric>=0.80",
        tables=[
            {
                "id": "main_table",
                "statement": "现金流量表补充资料",
                "caption": "现金流量表补充资料",
                "unit": "万元",
                "page": 1,
                "header_rows": 1,
                "col_widths": [280, 110, 110],
                "rows": [
                    ["补充资料项目", "2025年度", "2024年度"],
                    ["净利润", "3,118.40", "2,746.12"],
                    ["加：信用减值损失", "74.23", "51.36"],
                    ["固定资产折旧", "642.55", "588.90"],
                    ["财务费用", "216.07", "239.41"],
                    ["经营性应收项目的减少", "(385.62)", "128.33"],
                    ["经营活动产生的现金流量净额", "3,665.63", "3,754.12"],
                ],
            }
        ],
        metric_specs=[
            metric_spec("main_table", "净利润", 1),
            metric_spec("main_table", "加：信用减值损失", 1),
            metric_spec("main_table", "固定资产折旧", 1),
            metric_spec("main_table", "财务费用", 1),
            metric_spec("main_table", "经营性应收项目的减少", 1),
            metric_spec("main_table", "经营活动产生的现金流量净额", 1),
        ],
    ),
    make_case(
        case_id="input_009_balance_sheet_assets",
        filename="input_009_balance_sheet_assets.pdf",
        band="normal",
        pages=1,
        language="zh-CN",
        tags=["offline", "synthetic_pdf", "normal", "balance_sheet_assets"],
        image_only=False,
        company_name="禾源储能科技股份有限公司",
        name="合并资产负债表资产侧（合成）",
        title_by_page={1: "合并资产负债表（资产）"},
        yaml_extra={},
        diff_note="补充资产侧层级和小计，与已有资产负债表片段版式不同。",
        tests="financial_table>=1; metric_record>=5; GT numeric>=0.80",
        tables=[
            {
                "id": "main_table",
                "statement": "合并资产负债表",
                "caption": "资产",
                "unit": "万元",
                "page": 1,
                "header_rows": 1,
                "col_widths": [250, 120, 120],
                "rows": [
                    ["资产项目", "2025-12-31", "2024-12-31"],
                    ["流动资产：", "", ""],
                    ["货币资金", "8,456.22", "7,998.15"],
                    ["交易性金融资产", "1,240.00", "960.00"],
                    ["应收账款", "5,318.76", "4,884.20"],
                    ["存货", "6,790.45", "5,973.12"],
                    ["流动资产合计", "21,805.43", "19,815.47"],
                    ["非流动资产：", "", ""],
                    ["固定资产", "13,602.18", "12,941.33"],
                    ["无形资产", "2,130.88", "2,201.45"],
                    ["非流动资产合计", "15,733.06", "15,142.78"],
                    ["资产总计", "37,538.49", "34,958.25"],
                ],
            }
        ],
        metric_specs=[
            metric_spec("main_table", "货币资金", 1),
            metric_spec("main_table", "交易性金融资产", 1),
            metric_spec("main_table", "应收账款", 1),
            metric_spec("main_table", "存货", 1),
            metric_spec("main_table", "流动资产合计", 1),
            metric_spec("main_table", "固定资产", 1),
            metric_spec("main_table", "非流动资产合计", 1),
            metric_spec("main_table", "资产总计", 1),
        ],
    ),
    make_case(
        case_id="input_010_bilingual_income_statement",
        filename="input_010_bilingual_income_statement.pdf",
        band="normal",
        pages=2,
        language="zh-CN/en",
        tags=["offline", "synthetic_pdf", "normal", "multi_lang"],
        image_only=False,
        company_name="北辰微光科技股份有限公司",
        name="中英混排利润表（合成）",
        title_by_page={
            1: "Consolidated Income Statement / 合并利润表",
            2: "Income Statement Notes / 利润表附页",
        },
        yaml_extra={},
        diff_note="新增表头中英混排场景，正文项目和数值仍为中文财报表达。",
        tests="financial_table>=1; metric_record>=5; GT numeric>=0.80",
        tables=[
            {
                "id": "main_table",
                "statement": "合并利润表",
                "caption": "Consolidated Income Statement / 合并利润表",
                "unit": "万元",
                "page": 1,
                "header_rows": 1,
                "col_widths": [255, 118, 118],
                "rows": [
                    ["Item / 项目", "2025年度", "2024年度"],
                    ["营业收入", "9,426.18", "8,316.42"],
                    ["营业成本", "6,142.77", "5,598.03"],
                    ["研发费用", "912.36", "780.11"],
                    ["财务费用", "(32.14)", "18.65"],
                    ["利润总额", "2,403.19", "1,919.63"],
                    ["净利润", "2,068.74", "1,642.80"],
                ],
            },
            {
                "id": "note_table",
                "statement": "利润表附页",
                "caption": "Supplementary Items / 补充项目",
                "unit": "万元",
                "page": 2,
                "header_rows": 1,
                "col_widths": [255, 118, 118],
                "rows": [
                    ["Item / 项目", "2025年度", "2024年度"],
                    ["其他收益", "248.55", "210.39"],
                    ["投资收益", "126.90", "98.44"],
                    ["所得税费用", "334.45", "276.83"],
                ],
            },
        ],
        metric_specs=[
            metric_spec("main_table", "营业收入", 1),
            metric_spec("main_table", "营业成本", 1),
            metric_spec("main_table", "研发费用", 1),
            metric_spec("main_table", "财务费用", 1),
            metric_spec("main_table", "利润总额", 1),
            metric_spec("main_table", "净利润", 1),
            metric_spec("note_table", "所得税费用", 1),
        ],
    ),
    make_case(
        case_id="input_011_cross_page_income_statement",
        filename="input_011_cross_page_income_statement.pdf",
        band="complex",
        pages=2,
        language="zh-CN",
        tags=["offline", "synthetic_pdf", "complex", "cross_page", "income_statement"],
        image_only=False,
        company_name="映川精密装备股份有限公司",
        name="跨页合并利润表（合成）",
        title_by_page={1: "合并利润表", 2: "合并利润表（续）"},
        tail_by_page={1: "（续下页）"},
        yaml_extra={},
        diff_note="跨页续表使用利润表和专门续表文案，区别于既有跨页表样本。",
        tests="financial_table>=1; metric_record>=5; GT numeric>=0.80",
        tables=[
            {
                "id": "page1_table",
                "statement": "合并利润表",
                "caption": "合并利润表",
                "unit": "万元",
                "page": 1,
                "header_rows": 1,
                "col_widths": [250, 120, 120],
                "rows": [
                    ["项目", "2025年度", "2024年度"],
                    ["营业总收入", "15,904.70", "13,808.26"],
                    ["营业总成本", "12,331.82", "10,998.45"],
                    ["税金及附加", "138.24", "120.31"],
                    ["销售费用", "1,254.38", "1,101.44"],
                    ["管理费用", "1,718.61", "1,526.20"],
                ],
            },
            {
                "id": "page2_table",
                "statement": "合并利润表",
                "caption": "合并利润表（续）",
                "unit": "万元",
                "page": 2,
                "header_rows": 1,
                "col_widths": [250, 120, 120],
                "rows": [
                    ["项目", "2025年度", "2024年度"],
                    ["研发费用", "936.12", "821.34"],
                    ["资产减值损失", "(212.40)", "(185.77)"],
                    ["营业利润", "3,572.88", "2,809.81"],
                    ["利润总额", "3,548.62", "2,782.15"],
                    ["所得税费用", "533.74", "421.55"],
                    ["净利润", "3,014.88", "2,360.60"],
                ],
            },
        ],
        metric_specs=[
            metric_spec("page1_table", "营业总收入", 1),
            metric_spec("page1_table", "营业总成本", 1),
            metric_spec("page1_table", "管理费用", 1),
            metric_spec("page2_table", "研发费用", 1),
            metric_spec("page2_table", "资产减值损失", 1),
            metric_spec("page2_table", "营业利润", 1),
            metric_spec("page2_table", "利润总额", 1),
            metric_spec("page2_table", "所得税费用", 1),
            metric_spec("page2_table", "净利润", 1),
        ],
    ),
    make_case(
        case_id="input_012_no_border_financial_table",
        filename="input_012_no_border_financial_table.pdf",
        band="complex",
        pages=1,
        language="zh-CN",
        tags=["offline", "synthetic_pdf", "complex", "no_border"],
        image_only=False,
        company_name="澄岳数科股份有限公司",
        name="无竖线财务指标表（合成）",
        title_by_page={1: "主要财务指标简表"},
        yaml_extra={"layout": "no_border"},
        diff_note="新增无竖线边框的坐标排版表，专测空白和列宽对齐。",
        tests="financial_table>=1; metric_record>=5; GT numeric>=0.80",
        tables=[
            {
                "id": "main_table",
                "statement": "主要财务指标简表",
                "caption": "主要财务指标简表",
                "unit": "元",
                "page": 1,
                "header_rows": 1,
                "col_widths": [245, 120, 120],
                "rows": [
                    ["项目", "2025年度", "2024年度"],
                    ["基本每股收益", "1.26", "1.03"],
                    ["稀释每股收益", "1.24", "1.01"],
                    ["扣非后基本每股收益", "1.08", "0.89"],
                    ["每股经营现金流量", "2.73", "2.18"],
                    ["每股净资产", "8.96", "7.84"],
                ],
            }
        ],
        metric_specs=[
            metric_spec("main_table", "基本每股收益", 1),
            metric_spec("main_table", "稀释每股收益", 1),
            metric_spec("main_table", "扣非后基本每股收益", 1),
            metric_spec("main_table", "每股经营现金流量", 1),
            metric_spec("main_table", "每股净资产", 1),
        ],
    ),
    make_case(
        case_id="input_013_multi_header_performance_table",
        filename="input_013_multi_header_performance_table.pdf",
        band="complex",
        pages=1,
        language="zh-CN",
        tags=["offline", "synthetic_pdf", "complex", "multi_header"],
        image_only=False,
        company_name="朗渊工业软件股份有限公司",
        name="多级表头业绩表（合成）",
        title_by_page={1: "分部业绩变动表"},
        yaml_extra={},
        diff_note="新增营业总收入和净利润双指标多级表头，列名避开既有华电样式。",
        tests="financial_table>=1; metric_record>=5; GT numeric>=0.80",
        tables=[
            {
                "id": "main_table",
                "statement": "分部业绩变动表",
                "caption": "分部业绩变动表",
                "unit": "万元",
                "page": 1,
                "header_rows": 2,
                "col_widths": [110, 85, 65, 65, 85, 65, 65],
                "rows": [
                    ["业务分部", "营业总收入", "", "", "净利润", "", ""],
                    ["", "本期额", "同比%", "环比%", "本期额", "同比%", "环比%"],
                    ["工业软件", "8,410.22", "18.6", "6.4", "1,925.31", "22.1", "5.2"],
                    ["设备云平台", "5,632.45", "15.2", "4.8", "1,108.74", "19.5", "3.9"],
                    ["运维服务", "4,163.10", "11.7", "2.5", "806.42", "14.0", "2.1"],
                    ["合计", "18,205.77", "15.9", "4.7", "3,840.47", "19.8", "4.2"],
                ],
            }
        ],
        metric_specs=[
            metric_spec("main_table", "工业软件", 1, item="工业软件-营业总收入本期额", period="2025年度"),
            metric_spec("main_table", "工业软件", 4, item="工业软件-净利润本期额", period="2025年度"),
            metric_spec("main_table", "设备云平台", 1, item="设备云平台-营业总收入本期额", period="2025年度"),
            metric_spec("main_table", "设备云平台", 4, item="设备云平台-净利润本期额", period="2025年度"),
            metric_spec("main_table", "合计", 1, item="合计-营业总收入本期额", period="2025年度"),
            metric_spec("main_table", "合计", 4, item="合计-净利润本期额", period="2025年度"),
        ],
    ),
    make_case(
        case_id="input_014_main_table_with_notes",
        filename="input_014_main_table_with_notes.pdf",
        band="complex",
        pages=2,
        language="zh-CN",
        tags=["offline", "synthetic_pdf", "complex", "main_table_with_notes"],
        image_only=False,
        company_name="青岳循环科技股份有限公司",
        name="主表与附注表（合成）",
        title_by_page={1: "合并资产负债表（节选）", 2: "财务报表附注"},
        yaml_extra={},
        diff_note="新增主表加两张附注表，覆盖主表与注释表联动场景。",
        tests="financial_table>=1; metric_record>=5; GT numeric>=0.80",
        tables=[
            {
                "id": "main_table",
                "statement": "合并资产负债表",
                "caption": "合并资产负债表（节选）",
                "unit": "万元",
                "page": 1,
                "header_rows": 1,
                "col_widths": [250, 120, 120],
                "rows": [
                    ["项目", "2025-12-31", "2024-12-31"],
                    ["应收账款", "6,842.30", "5,917.44"],
                    ["存货", "4,512.66", "4,090.25"],
                    ["合同资产", "1,206.88", "1,009.54"],
                    ["流动资产合计", "18,774.61", "16,802.19"],
                    ["短期借款", "2,300.00", "2,600.00"],
                    ["资产总计", "29,440.38", "26,918.72"],
                ],
            },
            {
                "id": "ar_age_note",
                "statement": "应收账款账龄附注",
                "caption": "附注一：应收账款账龄",
                "unit": "万元",
                "page": 2,
                "header_rows": 1,
                "col_widths": [200, 120, 120],
                "rows": [
                    ["账龄", "2025-12-31", "2024-12-31"],
                    ["1年以内", "5,930.12", "5,088.31"],
                    ["1至2年", "712.46", "650.22"],
                    ["2至3年", "199.72", "178.91"],
                    ["合计", "6,842.30", "5,917.44"],
                ],
            },
            {
                "id": "top_customers_note",
                "statement": "前五名客户附注",
                "caption": "附注二：前五名客户应收款",
                "unit": "万元",
                "page": 2,
                "header_rows": 1,
                "col_widths": [200, 120, 120],
                "rows": [
                    ["客户", "应收余额", "占比%"],
                    ["客户A", "1,420.00", "20.75"],
                    ["客户B", "1,116.35", "16.31"],
                    ["客户C", "830.44", "12.14"],
                    ["前五名合计", "4,018.92", "58.74"],
                ],
            },
        ],
        metric_specs=[
            metric_spec("main_table", "应收账款", 1),
            metric_spec("main_table", "存货", 1),
            metric_spec("main_table", "流动资产合计", 1),
            metric_spec("main_table", "资产总计", 1),
            metric_spec("ar_age_note", "1年以内", 1),
            metric_spec("ar_age_note", "合计", 1, item="应收账款账龄合计"),
            metric_spec("top_customers_note", "客户A", 1, item="前五名客户-客户A应收余额", period="2025-12-31"),
            metric_spec("top_customers_note", "前五名合计", 1, item="前五名客户应收余额合计", period="2025-12-31"),
        ],
    ),
    make_case(
        case_id="input_015_low_dpi_scan_sim",
        filename="input_015_low_dpi_scan_sim.pdf",
        band="adversarial",
        pages=1,
        language="zh-CN",
        tags=["offline", "synthetic_pdf", "adversarial", "image_only", "low_dpi_scan"],
        image_only=True,
        company_name="越岭光伏材料股份有限公司",
        name="低清扫描仿真财务表（合成）",
        title_by_page={1: "利润表扫描件仿真"},
        yaml_extra={"accuracy_threshold": 0.50},
        diff_note="先渲染为图再嵌入 PDF，带灰底、轻微倾斜和确定性噪声。",
        tests="image-only; financial_table>=1; metric_record>=5; GT numeric>=0.50",
        tables=[
            {
                "id": "main_table",
                "statement": "利润表扫描件仿真",
                "caption": "利润表扫描件仿真",
                "unit": "万元",
                "page": 1,
                "header_rows": 1,
                "col_widths": [250, 120, 120],
                "rows": [
                    ["项目", "2025年度", "2024年度"],
                    ["营业收入", "7,804.55", "6,991.43"],
                    ["营业成本", "5,902.17", "5,188.60"],
                    ["税金及附加", "88.91", "75.44"],
                    ["销售费用", "421.36", "390.52"],
                    ["净利润", "1,044.28", "876.15"],
                ],
            }
        ],
        metric_specs=[
            metric_spec("main_table", "营业收入", 1),
            metric_spec("main_table", "营业成本", 1),
            metric_spec("main_table", "税金及附加", 1),
            metric_spec("main_table", "销售费用", 1),
            metric_spec("main_table", "净利润", 1),
        ],
    ),
    make_case(
        case_id="input_016_stamp_watermark_table",
        filename="input_016_stamp_watermark_table.pdf",
        band="adversarial",
        pages=1,
        language="zh-CN",
        tags=["offline", "synthetic_pdf", "adversarial", "image_only", "stamp_watermark"],
        image_only=True,
        company_name="森曜智慧物流股份有限公司",
        name="印章水印图像财务表（合成）",
        title_by_page={1: "合并现金流量表"},
        yaml_extra={"accuracy_threshold": 0.50},
        diff_note="图像型 PDF 叠加红章和半透明样本水印，关键数字行保持可读。",
        tests="image-only; financial_table>=1; metric_record>=5; GT numeric>=0.50",
        tables=[
            {
                "id": "main_table",
                "statement": "合并现金流量表",
                "caption": "合并现金流量表",
                "unit": "万元",
                "page": 1,
                "header_rows": 1,
                "col_widths": [265, 115, 115],
                "rows": [
                    ["项目", "2025年度", "2024年度"],
                    ["销售商品、提供劳务收到的现金", "11,208.44", "9,856.02"],
                    ["收到的税费返还", "186.50", "142.33"],
                    ["购买商品、接受劳务支付的现金", "7,642.19", "6,911.27"],
                    ["支付给职工以及为职工支付的现金", "1,024.86", "903.14"],
                    ["经营活动产生的现金流量净额", "2,727.89", "2,184.66"],
                ],
            }
        ],
        metric_specs=[
            metric_spec("main_table", "销售商品、提供劳务收到的现金", 1),
            metric_spec("main_table", "收到的税费返还", 1),
            metric_spec("main_table", "购买商品、接受劳务支付的现金", 1),
            metric_spec("main_table", "支付给职工以及为职工支付的现金", 1),
            metric_spec("main_table", "经营活动产生的现金流量净额", 1),
        ],
    ),
    make_case(
        case_id="input_017_header_footer_noise",
        filename="input_017_header_footer_noise.pdf",
        band="adversarial",
        pages=1,
        language="zh-CN",
        tags=["offline", "synthetic_pdf", "adversarial", "header_footer_noise"],
        image_only=False,
        company_name="岚桥智能仪表股份有限公司",
        name="页眉页脚噪声财务表（合成）",
        title_by_page={1: "财务指标摘要"},
        yaml_extra={"accuracy_threshold": 0.50, "layout": "header_footer_noise"},
        diff_note="新增重复页眉、页脚页码和横线噪声，表格位于页面中部。",
        tests="header/footer noise; financial_table>=1; metric_record>=5; GT numeric>=0.50",
        tables=[
            {
                "id": "main_table",
                "statement": "财务指标摘要",
                "caption": "财务指标摘要",
                "unit": "万元",
                "page": 1,
                "header_rows": 1,
                "col_widths": [250, 120, 120],
                "rows": [
                    ["项目", "2025年度", "2024年度"],
                    ["营业收入", "6,318.44", "5,802.36"],
                    ["毛利", "2,114.70", "1,876.92"],
                    ["期间费用", "1,025.30", "966.18"],
                    ["利润总额", "1,089.40", "910.74"],
                    ["净利润", "926.08", "774.13"],
                ],
            }
        ],
        metric_specs=[
            metric_spec("main_table", "营业收入", 1),
            metric_spec("main_table", "毛利", 1),
            metric_spec("main_table", "期间费用", 1),
            metric_spec("main_table", "利润总额", 1),
            metric_spec("main_table", "净利润", 1),
        ],
    ),
    make_case(
        case_id="input_018_meeting_minutes_no_table",
        filename="input_018_meeting_minutes_no_table.pdf",
        band="boundary",
        pages=1,
        language="zh-CN",
        tags=["offline", "synthetic_pdf", "out_of_domain", "no_table"],
        image_only=False,
        company_name="曜泽科技股份有限公司",
        name="董事会会议纪要（无表，合成）",
        title_by_page={1: "董事会会议纪要"},
        yaml_extra={"negative_no_table": True},
        diff_note="金融语境会议纪要但无任何表格，区别于医疗域外样本。",
        tests="无表; financial_table==0; metric_record==0",
        tables=[],
        metric_specs=[],
        body_by_page={
            1: [
                "会议时间：2026年3月18日 09:30",
                "会议地点：公司第一会议室",
                "参会人员：董事会全体成员、董事会秘书及财务负责人列席。",
                "会议议题：审议年度经营计划、内部控制改进安排及投资者沟通事项。",
                "会议认为，公司应持续完善信息披露流程，提升财务报告编制复核质量。",
                "会议未审议具体财务报表，也未形成任何金额类表格或指标记录。",
                "本纪要仅用于验证解析流程不应输出 financial_table 或 metric_record，不参与 financial_accuracy。",
            ]
        },
    ),
]


def by_id(case_id: str) -> dict[str, Any]:
    for case in CASES:
        if case["case_id"] == case_id:
            return case
    raise KeyError(case_id)


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def write_text(path: Path, text: str) -> Path:
    ensure_parent(path)
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


def write_json(path: Path, data: dict[str, Any]) -> Path:
    ensure_parent(path)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    return path


def yaml_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value)
    if text == "":
        return "''"
    needs_quote = any(ch in text for ch in ":#{}[]&,*?!|>'\"%@`") or text.strip() != text
    if "\n" in text:
        return "|-\n" + "\n".join(f"    {line}" for line in text.splitlines())
    if needs_quote:
        return json.dumps(text, ensure_ascii=False)
    return text


def dump_yaml(data: dict[str, Any]) -> str:
    lines: list[str] = []

    def emit(value: Any, indent: int, key: str | None = None) -> None:
        prefix = " " * indent
        if isinstance(value, dict):
            if key is not None:
                lines.append(f"{prefix}{key}:")
                indent += 2
                prefix = " " * indent
            for k, v in value.items():
                emit(v, indent, str(k))
        elif isinstance(value, list):
            if key is not None:
                lines.append(f"{prefix}{key}:")
                indent += 2
                prefix = " " * indent
            if not value:
                if key is not None:
                    lines[-1] = lines[-1] + " []"
                else:
                    lines.append(f"{prefix}[]")
            for item in value:
                if isinstance(item, dict):
                    lines.append(f"{prefix}-")
                    for k, v in item.items():
                        emit(v, indent + 2, str(k))
                elif isinstance(item, list):
                    lines.append(f"{prefix}-")
                    emit(item, indent + 2)
                else:
                    lines.append(f"{prefix}- {yaml_scalar(item)}")
        else:
            if key is None:
                lines.append(f"{prefix}{yaml_scalar(value)}")
            else:
                rendered = yaml_scalar(value)
                if rendered.startswith("|-"):
                    first, rest = rendered.split("\n", 1)
                    lines.append(f"{prefix}{key}: {first}")
                    lines.append(rest)
                else:
                    lines.append(f"{prefix}{key}: {rendered}")

    emit(data, 0)
    return "\n".join(lines) + "\n"


def export_gt(case: dict[str, Any], promote_verified: bool = False) -> Path:
    source = "manual_verified" if promote_verified else GT_SOURCE_DEFAULT
    if case["case_id"] == "input_018_meeting_minutes_no_table":
        note = (
            "Synthetic PDF from tools/generate_eval_pdfs.py CASES[]; "
            "验证不应出现 financial_table / metric_record；不参与 financial_accuracy。"
        )
    else:
        note = "Synthetic PDF from tools/generate_eval_pdfs.py CASES[]; promote to manual_verified after human review."
    if promote_verified:
        note += VERIFIED_NOTE_SUFFIX
    payload = {
        "case_id": case["case_id"],
        "source": source,
        "note": note,
        "metrics": case["metrics"],
    }
    return write_json(ROOT / "evaluation" / "ground_truth" / f"{case['case_id']}_manual_gt.json", payload)


def case_validations(case: dict[str, Any]) -> list[dict[str, Any]]:
    validations: list[dict[str, Any]] = [{"type": "output_contract", "profile": "standard"}]
    if case["case_id"] == "input_018_meeting_minutes_no_table":
        validations.extend(
            [
                {"type": "table_stat_eq", "field": "financial_table_count", "expected": 0},
                {"type": "metric_stat_eq", "field": "metric_record_count", "expected": 0},
            ]
        )
        return validations
    threshold = float(case.get("yaml_extra", {}).get("accuracy_threshold", 0.80))
    validations.extend(
        [
            {"type": "table_stat_ge", "field": "financial_table_count", "expected": 1},
            {"type": "metric_stat_ge", "field": "metric_record_count", "expected": 5},
            {"type": "gt_numeric_accuracy_ge", "threshold": threshold, "skip_if_no_ground_truth": True},
        ]
    )
    return validations


def export_yaml(case: dict[str, Any]) -> Path:
    description = "Synthetic eval PDF; source-of-truth in tools/generate_eval_pdfs.py"
    if case["band"] == "adversarial":
        description += "；image-only PDF，准确率仅参考。" if case["image_only"] else "；含页眉页脚噪声，准确率仅参考。"
    data = {
        "case_id": case["case_id"],
        "name": case["name"],
        "description": description,
        "input_pdf": f"data/samples/{case['filename']}",
        "backend": "fixture",
        "output_profile": "standard",
        "output_dir": f"data/real_las_outputs/{case['case_id']}",
        "ground_truth": f"evaluation/ground_truth/{case['case_id']}_manual_gt.json",
        "count_as_real_evaluation": False,
        "tags": case["tags"],
        "validations": case_validations(case),
    }
    comment = (
        "# backend: fixture means run/import real_las or mock outputs into data/real_las_outputs/<case_id> first.\n"
        "# Synthetic cases intentionally omit data_authenticity + require_non_synthetic: true.\n"
        "# count_as_real_evaluation: false marks these as synthetic expansion scenarios.\n"
    )
    path = ROOT / "testcases" / "pdf_cases" / f"{case['case_id']}.yaml"
    return write_text(path, comment + dump_yaml(data))


def export_manifest_row(case: dict[str, Any]) -> str:
    return (
        f"| {case['filename']} | {case['case_id']} | {case['band']} | {case['pages']} | "
        f"{str(case['image_only']).lower()} | {len(case['metrics'])} | {GT_SOURCE_DEFAULT} | "
        f"{case['diff_note']} | {case['tests']} |"
    )


def export_manifest() -> Path:
    rows = [
        "# Dataset manifest: synthetic financial PDF cases input_007-input_018",
        "",
        "| filename | case_id | band | pages | image_only | metrics_count | gt_source | diff_note | tests |",
        "|---|---|---|---:|---|---:|---|---|---|",
    ]
    rows.extend(export_manifest_row(case) for case in CASES)
    rows.extend(
        [
            "",
            "## 接入步骤",
            "",
            "1. `python tools/generate_eval_pdfs.py`",
            "2. 人工抽查 PDF vs GT。",
            "3. （可选）人工核对后运行 `python tools/generate_eval_pdfs.py --promote-verified`。",
            "4. 对每个 case 跑 real_las 或 mock，导入到 `data/real_las_outputs/<case_id>/`，再保持 `backend: fixture` 评测。",
            "5. `python run.py --cases testcases/pdf_cases/input_007_income_statement.yaml --backend fixture --pipeline`",
            "6. `python run.py --build-dashboard-bundle`",
            "",
        ]
    )
    return write_text(ROOT / "reports" / "dataset_manifest_new_007_018.md", "\n".join(rows))


def export_config_snippet() -> Path:
    data = {
        "synthetic_pdf_cases": [
            {
                "case_id": case["case_id"],
                "input_pdf": f"data/samples/{case['filename']}",
                "case_yaml": f"testcases/pdf_cases/{case['case_id']}.yaml",
                "ground_truth": f"evaluation/ground_truth/{case['case_id']}_manual_gt.json",
                "band": case["band"],
                "pages": case["pages"],
                "image_only": case["image_only"],
                "count_as_real_evaluation": False,
            }
            for case in CASES
        ]
    }
    return write_text(ROOT / "configs" / "dataset_manifest_snippet_007_018.yaml", dump_yaml(data))


def load_reportlab():
    try:
        import reportlab.rl_config as rl_config

        rl_config.invariant = 1
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.utils import ImageReader
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        from reportlab.pdfgen import canvas
    except ImportError as exc:
        raise SystemExit("Missing dependency. Run: pip install reportlab pillow") from exc
    return colors, A4, ImageReader, pdfmetrics, TTFont, canvas


def find_chinese_font() -> Path:
    # Build system font candidates from environment/platform roots rather than
    # embedding a developer-machine absolute path in a distributable source file.
    root = Path(os.sep)
    candidates: list[Path] = []
    windows_dir = os.environ.get("WINDIR")
    if windows_dir:
        fonts_dir = Path(windows_dir) / "Fonts"
        candidates.extend(fonts_dir / name for name in ("msyh.ttc", "simsun.ttc", "msyh.ttf", "simsun.ttf"))
    candidates.extend([
        root / "System" / "Library" / "Fonts" / "PingFang.ttc",
        root / "System" / "Library" / "Fonts" / "STHeiti Light.ttc",
        root / "usr" / "share" / "fonts" / "opentype" / "noto" / "NotoSansCJK-Regular.ttc",
        root / "usr" / "share" / "fonts" / "truetype" / "noto" / "NotoSansCJK-Regular.ttc",
        root / "usr" / "share" / "fonts" / "opentype" / "source-han-sans" / "SourceHanSansSC-Regular.otf",
    ])
    for path in candidates:
        if path.exists():
            return path
    sys.exit(
        "未找到中文字体。请安装 SimSun、Microsoft YaHei、MSYH、Noto Sans CJK SC、"
        "Source Han Sans SC 或 PingFang SC；Windows 请设置 WINDIR 并安装系统中文字体。"
    )


def register_pdf_font() -> tuple[str, Path]:
    _, _, _, pdfmetrics, TTFont, _ = load_reportlab()
    font_path = find_chinese_font()
    try:
        pdfmetrics.registerFont(TTFont(FONT_NAME, str(font_path)))
    except Exception as exc:
        sys.exit(f"中文字体注册失败：{font_path}；请安装可被 reportlab 读取的中文 TrueType/OpenType 字体。错误：{exc}")
    return FONT_NAME, font_path


def draw_centered(c: Any, text: str, x: float, y: float, font_name: str, size: float) -> None:
    c.setFont(font_name, size)
    c.drawCentredString(x, y, text)


def draw_table(
    c: Any,
    table: dict[str, Any],
    x: float,
    top_y: float,
    font_name: str,
    *,
    horizontal_only: bool = False,
) -> float:
    colors, _, _, _, _, _ = load_reportlab()
    rows = table["rows"]
    col_widths = table.get("col_widths") or [140] * len(rows[0])
    header_rows = int(table.get("header_rows", 1))
    row_h = float(table.get("row_height", 24))
    width = sum(col_widths)
    height = row_h * len(rows)
    bottom_y = top_y - height

    c.setStrokeColor(colors.HexColor("#444444"))
    c.setLineWidth(0.6)
    for i in range(len(rows) + 1):
        y = top_y - i * row_h
        c.line(x, y, x + width, y)
    if not horizontal_only:
        xx = x
        for w in col_widths:
            c.line(xx, top_y, xx, bottom_y)
            xx += w
        c.line(x + width, top_y, x + width, bottom_y)

    if not horizontal_only:
        c.setFillColor(colors.HexColor("#F2F2F2"))
        c.rect(x, top_y - header_rows * row_h, width, header_rows * row_h, stroke=0, fill=1)
        c.setStrokeColor(colors.HexColor("#444444"))
        for i in range(len(rows) + 1):
            y = top_y - i * row_h
            c.line(x, y, x + width, y)
        xx = x
        for w in col_widths:
            c.line(xx, top_y, xx, bottom_y)
            xx += w
        c.line(x + width, top_y, x + width, bottom_y)

    for r_idx, row in enumerate(rows):
        y = top_y - (r_idx + 0.68) * row_h
        xx = x
        for col_idx, cell in enumerate(row):
            w = col_widths[col_idx]
            c.setFillColor(colors.black)
            c.setFont(font_name, 8.5 if len(str(cell)) > 16 else 9.5)
            if r_idx < header_rows:
                c.setFont(font_name, 9)
                c.drawCentredString(xx + w / 2, y, str(cell))
            elif col_idx == 0:
                c.drawString(xx + 5, y, str(cell))
            else:
                c.drawRightString(xx + w - 6, y, str(cell))
            xx += w
    return bottom_y


def draw_page_header(c: Any, case: dict[str, Any], page_num: int, font_name: str, width: float, height: float) -> None:
    title = case.get("title_by_page", {}).get(page_num, case.get("name", ""))
    draw_centered(c, title, width / 2, height - 54, font_name, 15)
    c.setFont(font_name, 9)
    c.drawCentredString(width / 2, height - 72, case["company_name"])


def draw_noise_header_footer(c: Any, font_name: str, width: float, height: float) -> None:
    colors, _, _, _, _, _ = load_reportlab()
    c.setStrokeColor(colors.HexColor("#777777"))
    c.setLineWidth(0.4)
    c.setFont(font_name, 8)
    c.drawString(42, height - 28, "临时公告  临时公告  临时公告")
    c.drawRightString(width - 42, height - 28, "公告编号：2026-017")
    c.line(36, height - 36, width - 36, height - 36)
    c.line(36, 42, width - 36, 42)
    c.drawCentredString(width / 2, 26, "第 1 页 / 共 1 页")


def draw_meeting_minutes(c: Any, case: dict[str, Any], page_num: int, font_name: str, width: float, height: float) -> None:
    draw_page_header(c, case, page_num, font_name, width, height)
    y = height - 120
    c.setFont(font_name, 11)
    for line in case.get("body_by_page", {}).get(page_num, []):
        c.drawString(72, y, line)
        y -= 28
    c.setFont(font_name, 9)
    c.drawString(72, 98, "记录人：证券事务部")
    c.drawRightString(width - 72, 98, "本文档不含财务表格")


def render_vector_pdf(case: dict[str, Any], font_name: str, path: Path) -> None:
    colors, A4, _, _, _, canvas = load_reportlab()
    c = canvas.Canvas(str(path), pagesize=A4, invariant=1, pageCompression=1)
    c.setTitle(case["case_id"])
    c.setAuthor("tools/generate_eval_pdfs.py")
    width, height = A4

    for page_num in range(1, int(case["pages"]) + 1):
        c.setFillColor(colors.white)
        c.rect(0, 0, width, height, stroke=0, fill=1)
        if case.get("yaml_extra", {}).get("layout") == "header_footer_noise":
            draw_noise_header_footer(c, font_name, width, height)
        if case["case_id"] == "input_018_meeting_minutes_no_table":
            draw_meeting_minutes(c, case, page_num, font_name, width, height)
            c.showPage()
            continue

        draw_page_header(c, case, page_num, font_name, width, height)
        y = height - 108
        for table in [t for t in case["tables"] if t.get("page", 1) == page_num]:
            c.setFont(font_name, 10)
            c.drawString(54, y, table.get("caption", ""))
            c.drawRightString(width - 54, y, f"单位：{table.get('unit', '')}")
            y -= 16
            horizontal_only = case.get("yaml_extra", {}).get("layout") == "no_border"
            y = draw_table(c, table, 54, y, font_name, horizontal_only=horizontal_only) - 28
        tail = case.get("tail_by_page", {}).get(page_num)
        if tail:
            c.setFont(font_name, 10)
            c.drawRightString(width - 54, 72, tail)
        c.setFont(font_name, 8)
        c.drawCentredString(width / 2, 28, f"{page_num}")
        c.showPage()
    c.save()


def pil_font(font_path: Path, size: int):
    try:
        from PIL import ImageFont
    except ImportError as exc:
        raise SystemExit("Missing dependency. Run: pip install reportlab pillow") from exc
    return ImageFont.truetype(str(font_path), size)


def draw_pil_table(draw: Any, table: dict[str, Any], font_path: Path, x: int, top_y: int, total_width: int) -> int:
    from PIL import Image

    rows = table["rows"]
    header_rows = int(table.get("header_rows", 1))
    source_widths = table.get("col_widths") or [140] * len(rows[0])
    scale = total_width / sum(source_widths)
    widths = [int(w * scale) for w in source_widths]
    widths[-1] += total_width - sum(widths)
    row_h = 46
    font = pil_font(font_path, 23)
    small_font = pil_font(font_path, 20)
    header_font = pil_font(font_path, 22)
    y = top_y
    for r_idx, row in enumerate(rows):
        xx = x
        fill = (222, 222, 222) if r_idx < header_rows else (248, 248, 248)
        for col_idx, cell in enumerate(row):
            w = widths[col_idx]
            draw.rectangle([xx, y, xx + w, y + row_h], outline=(80, 80, 80), fill=fill)
            text_font = header_font if r_idx < header_rows else (small_font if len(str(cell)) > 14 else font)
            bbox = draw.textbbox((0, 0), str(cell), font=text_font)
            text_w = bbox[2] - bbox[0]
            text_h = bbox[3] - bbox[1]
            if r_idx < header_rows:
                tx = xx + (w - text_w) / 2
            elif col_idx == 0:
                tx = xx + 10
            else:
                tx = xx + w - text_w - 10
            draw.text((tx, y + (row_h - text_h) / 2 - 2), str(cell), font=text_font, fill=(20, 20, 20))
            xx += w
        y += row_h
    return y


def add_deterministic_noise(img: Any, strength: int = 7) -> Any:
    data = []
    width, _ = img.size
    for idx, (r, g, b) in enumerate(img.convert("RGB").getdata()):
        delta = ((idx * 37 + (idx // width) * 17) % (strength * 2 + 1)) - strength
        data.append((max(0, min(255, r + delta)), max(0, min(255, g + delta)), max(0, min(255, b + delta))))
    img.putdata(data)
    return img


def add_stamp_and_watermark(img: Any, font_path: Path) -> Any:
    from PIL import Image, ImageDraw

    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    odraw = ImageDraw.Draw(overlay)
    big_font = pil_font(font_path, 150)
    stamp_font = pil_font(font_path, 30)
    w, h = img.size
    odraw.text((w * 0.35, h * 0.48), "样本", font=big_font, fill=(120, 120, 120, 45))
    cx, cy, rx, ry = int(w * 0.74), int(h * 0.24), 150, 72
    odraw.ellipse([cx - rx, cy - ry, cx + rx, cy + ry], outline=(200, 0, 0, 150), width=8)
    odraw.text((cx - 92, cy - 20), "合成测试专用", font=stamp_font, fill=(200, 0, 0, 155))
    return Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")


def make_table_image(case: dict[str, Any], font_path: Path) -> Any:
    try:
        from PIL import Image, ImageDraw
    except ImportError as exc:
        raise SystemExit("Missing dependency. Run: pip install reportlab pillow") from exc

    img = Image.new("RGB", (1240, 1754), (236, 236, 232) if case["case_id"] == "input_015_low_dpi_scan_sim" else (255, 255, 255))
    draw = ImageDraw.Draw(img)
    title_font = pil_font(font_path, 40)
    meta_font = pil_font(font_path, 24)
    table = case["tables"][0]
    draw.text((620, 108), case.get("title_by_page", {}).get(1, case["name"]), font=title_font, fill=(20, 20, 20), anchor="mm")
    draw.text((620, 154), case["company_name"], font=meta_font, fill=(30, 30, 30), anchor="mm")
    draw.text((1080, 220), f"单位：{table.get('unit', '')}", font=meta_font, fill=(30, 30, 30), anchor="ra")
    draw_pil_table(draw, table, font_path, 110, 245, 1020)
    if case["case_id"] == "input_015_low_dpi_scan_sim":
        img = img.rotate(1.0, resample=Image.Resampling.BICUBIC, fillcolor=(236, 236, 232))
        img = add_deterministic_noise(img, 8)
    if case["case_id"] == "input_016_stamp_watermark_table":
        img = add_stamp_and_watermark(img, font_path)
    return img


def render_image_pdf(case: dict[str, Any], font_path: Path, path: Path) -> None:
    _, A4, ImageReader, _, _, canvas = load_reportlab()
    c = canvas.Canvas(str(path), pagesize=A4, invariant=1, pageCompression=1)
    c.setTitle(case["case_id"])
    c.setAuthor("tools/generate_eval_pdfs.py")
    width, height = A4
    img = make_table_image(case, font_path)
    c.drawImage(ImageReader(img), 0, 0, width=width, height=height)
    c.showPage()
    c.save()


def render_pdf(case: dict[str, Any]) -> Path:
    path = ROOT / "data" / "samples" / case["filename"]
    ensure_parent(path)
    font_name, font_path = register_pdf_font()
    if case["image_only"]:
        render_image_pdf(case, font_path, path)
    else:
        render_vector_pdf(case, font_name, path)
    return path


def generate_all(promote_verified: bool = False) -> list[Path]:
    generated: list[Path] = []
    for case in CASES:
        generated.append(render_pdf(case))
        generated.append(export_gt(case, promote_verified=promote_verified))
        generated.append(export_yaml(case))
        (ROOT / "data" / "real_las_outputs" / case["case_id"]).mkdir(parents=True, exist_ok=True)
    generated.append(export_manifest())
    generated.append(export_config_snippet())
    return generated


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate synthetic financial PDF evaluation samples.")
    parser.add_argument("--promote-verified", action="store_true", help="write GT source=manual_verified after human PDF-vs-GT review")
    args = parser.parse_args(argv)
    generated = generate_all(promote_verified=args.promote_verified)
    print("Generated files:")
    for path in generated:
        print(path.relative_to(ROOT).as_posix())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
