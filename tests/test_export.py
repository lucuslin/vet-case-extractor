"""Tests for Excel 9-col / 7-col export, sorting, layers, Chinese filenames, and project bundles."""

from __future__ import annotations

import json
from pathlib import Path

from openpyxl import load_workbook

from conftest import write_sample_project
from vet_case_extractor.config import AppConfig
from vet_case_extractor.export.bundle import export_project_bundle
from vet_case_extractor.export.excel import BOOK_COLUMNS, JOURNAL_COLUMNS, export_to_excel
from vet_case_extractor.models import CaseRecord, EvidenceSpan, QAReport, SourceType


def test_journal_excel_9_columns_sheets_freeze_wrap_and_xml_safety(
    tmp_path: Path,
    sample_journal_records: list[CaseRecord],
    app_config: AppConfig,
) -> None:
    # Inject illegal XML control char + missing field + reversed order to test sorting
    rec_a = sample_journal_records[0].model_copy(
        update={
            "medical_history": "患猫，3岁\x00\x0b，体温39.8℃。",
            "follow_up": "",
        }
    )
    rec_b = sample_journal_records[1]
    out_xlsx = tmp_path / "中文路径导出" / "病例抽取_总表.xlsx"

    export_to_excel(
        [rec_b, rec_a],
        out_xlsx,
        format_mode="journal",
        layer="normalized",
        source_filenames={"doc_a": "01_猫病例.pdf", "doc_b": "02_犬病例.pdf"},
        cfg=app_config,
    )
    assert out_xlsx.exists()

    wb = load_workbook(out_xlsx)
    assert wb.sheetnames == ["病例数据", "说明"]
    ws = wb["病例数据"]

    headers = tuple(ws.cell(row=1, column=c).value for c in range(1, 10))
    assert headers == JOURNAL_COLUMNS
    assert ws.freeze_panes == "A2"

    # Check deterministic sorting by source_filename ("01_猫病例.pdf" before "02_犬病例.pdf")
    assert ws.cell(row=2, column=3).value == "猫血支原体感染病例报告"
    assert ws.cell(row=3, column=3).value == "犬骨肉瘤病例报告"

    # Check year string preserved, illegal XML stripped, empty cell -> ""
    assert ws.cell(row=2, column=2).value == "2024（2023）"
    assert "\x00" not in (ws.cell(row=2, column=4).value or "")
    assert (ws.cell(row=2, column=8).value or "") == ""
    assert ws.cell(row=2, column=6).value == "多西环素10 mg/kg，po，bid。"

    # Check wrap_text, vertical alignment, and readable widths
    data_cell = ws.cell(row=2, column=4)
    assert data_cell.alignment.wrap_text is True
    assert data_cell.alignment.vertical == "top"
    assert ws.column_dimensions["D"].width >= 30

    # Ensure no internal debug fields appear in headers or row values
    row2_text = " ".join(str(ws.cell(row=2, column=c).value or "") for c in range(1, 10))
    assert "doc_a_case_01" not in row2_text
    assert ("a" * 64) not in row2_text
    wb.close()


def test_book_excel_7_columns_sort_and_raw_layer(
    tmp_path: Path,
    sample_book_records: list[CaseRecord],
    app_config: AppConfig,
) -> None:
    ch1_c2 = sample_book_records[0].model_copy(
        update={
            "case_id": "book_1_ch01_case02",
            "case_order": 3,
            "case_number": 2,
            "case_level": 2,
            "title": "第一章 肝酶升高 · 病例2 等级2",
            "medical_treatment_raw": "头孢曲松钠 静脉注射 每日1次",
            "medical_treatment": "头孢曲松钠 iv sid",
            "diagnosis_raw": "犬 肝脂肪沉积",
            "diagnosis_normalized": "犬肝脂肪沉积",
        }
    )
    shuffled = [sample_book_records[1], ch1_c2, sample_book_records[0]]

    norm_xlsx = tmp_path / "冬窝窝261009兽医临床病例分析_全书病例结构化抽取.xlsx"
    raw_xlsx = tmp_path / "raw_layer.xlsx"

    export_to_excel(shuffled, norm_xlsx, format_mode="book", layer="normalized", cfg=app_config)
    export_to_excel(shuffled, raw_xlsx, format_mode="book", layer="raw", cfg=app_config)

    wb_norm = load_workbook(norm_xlsx)
    assert wb_norm.sheetnames == ["病例数据", "说明"]
    ws_norm = wb_norm["病例数据"]
    headers = tuple(ws_norm.cell(row=1, column=c).value for c in range(1, 8))
    assert headers == BOOK_COLUMNS
    assert ws_norm.freeze_panes == "A2"

    # Sorted by (chapter_number, case_number)
    titles = [ws_norm.cell(row=r, column=1).value for r in (2, 3, 4)]
    assert titles == [
        "第一章 肝酶升高 · 病例1 等级1",
        "第一章 肝酶升高 · 病例2 等级2",
        "第二章 肾功能异常 · 病例1 等级2",
    ]
    assert ws_norm.cell(row=3, column=4).value == "头孢曲松钠 iv sid"
    assert ws_norm.cell(row=3, column=5).value == "犬肝脂肪沉积"
    wb_norm.close()

    wb_raw = load_workbook(raw_xlsx)
    ws_raw = wb_raw["病例数据"]
    assert ws_raw.cell(row=3, column=4).value == "头孢曲松钠 静脉注射 每日1次"
    assert ws_raw.cell(row=3, column=5).value == "犬 肝脂肪沉积"
    wb_raw.close()


def test_export_project_bundle_creates_all_artifacts(
    tmp_path: Path,
    sample_journal_records: list[CaseRecord],
    app_config: AppConfig,
) -> None:
    proj = write_sample_project(
        tmp_path / "项目工作区",
        sample_journal_records,
        SourceType.JOURNAL,
        filenames={"doc_a": "猫病.pdf", "doc_b": "犬病.pdf"},
    )
    out_dir = tmp_path / "交付束"
    outputs = export_project_bundle(proj, out_dir, cfg=app_config)

    expected_keys = {
        "excel",
        "cases_jsonl",
        "provenance_json",
        "qa_report_json",
        "qa_report_md",
        "review_queue_json",
        "run_metadata_json",
    }
    assert set(outputs.keys()) == expected_keys
    for path in outputs.values():
        assert path.exists()

    jsonl_lines = outputs["cases_jsonl"].read_text(encoding="utf-8").strip().splitlines()
    assert len(jsonl_lines) == 2
    # Sorted by source_filename: "犬病.pdf" (U+72AC) precedes "猫病.pdf" (U+732B)
    assert CaseRecord.model_validate_json(jsonl_lines[0]).case_id == "doc_b_case_01"
    assert CaseRecord.model_validate_json(jsonl_lines[1]).case_id == "doc_a_case_01"

    prov_data = json.loads(outputs["provenance_json"].read_text(encoding="utf-8"))
    assert len(prov_data) == 2
    assert EvidenceSpan.model_validate(prov_data[0]).case_id == "doc_b_case_01"
    assert EvidenceSpan.model_validate(prov_data[1]).case_id == "doc_a_case_01"

    qa = QAReport.model_validate_json(outputs["qa_report_json"].read_text(encoding="utf-8"))
    assert qa.overall_status == "PASS"

    meta = json.loads(outputs["run_metadata_json"].read_text(encoding="utf-8"))
    assert meta["exported_records"] == 2
    assert meta["format"] == "journal"
    assert meta["layer"] == "normalized"
