"""Excel exporter matching legacy 9-column journal and 7-column book formats."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Literal

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from vet_case_extractor import __version__
from vet_case_extractor.config import AppConfig, load_config
from vet_case_extractor.models import CaseRecord, QAReport, SourceType, utc_now_iso
from vet_case_extractor.utils.text import strip_illegal_xml_chars

JOURNAL_COLUMNS: tuple[str, ...] = (
    "source",
    "year",
    "title",
    "medical_history",
    "medical_test",
    "medical_treatment",
    "Dx",
    "follow-up",
    "备注",
)

BOOK_COLUMNS: tuple[str, ...] = (
    "title",
    "medical_history",
    "medical_test",
    "medical_treatment",
    "Dx",
    "follow-up",
    "备注",
)

_COLUMN_WIDTHS: dict[str, int] = {
    "source": 20,
    "year": 15,
    "title": 36,
    "medical_history": 48,
    "medical_test": 48,
    "medical_treatment": 42,
    "Dx": 30,
    "follow-up": 32,
    "备注": 36,
}


def resolve_export_format(
    records: list[CaseRecord],
    format_mode: Literal["auto", "journal", "book"] = "auto",
    default_source_type: SourceType = SourceType.AUTO,
) -> Literal["journal", "book"]:
    """Resolve effective Excel schema (`journal` 9-col vs `book` 7-col)."""
    if format_mode in ("journal", "book"):
        return format_mode
    if default_source_type == SourceType.BOOK:
        return "book"
    if default_source_type == SourceType.JOURNAL:
        return "journal"
    if records and all(r.source_type == SourceType.BOOK for r in records):
        return "book"
    return "journal"


def sort_records_for_export(
    records: list[CaseRecord],
    format_mode: Literal["journal", "book"],
    source_filenames: dict[str, str] | None = None,
) -> list[CaseRecord]:
    """Deterministically sort CaseRecords by `(chapter_number, case_number)` or `(source_filename, case_order)`."""
    if format_mode == "book":
        return sorted(
            records,
            key=lambda r: (
                r.chapter_number if r.chapter_number is not None else 10**9,
                r.case_number if r.case_number is not None else 10**9,
                r.case_order,
                r.title,
                r.case_id,
            ),
        )
    fn_map = source_filenames or {}
    return sorted(
        records,
        key=lambda r: (
            fn_map.get(r.document_id) or r.title or r.document_id,
            r.case_order,
            r.case_id,
        ),
    )


def _clean_cell(val: str | None) -> str:
    if not val:
        return ""
    return strip_illegal_xml_chars(str(val))


def _record_row_values(
    record: CaseRecord,
    format_mode: Literal["journal", "book"],
    layer: Literal["normalized", "raw"],
) -> list[str]:
    if layer == "raw":
        history = record.medical_history_raw
        test = record.medical_test_raw
        treatment = record.medical_treatment_raw
        dx = record.diagnosis_raw
        follow_up = record.follow_up_raw
        notes = record.notes_raw
    else:
        history = record.medical_history
        test = record.medical_test
        treatment = record.medical_treatment
        dx = record.diagnosis_normalized
        follow_up = record.follow_up
        notes = record.notes

    base = [
        _clean_cell(record.title),
        _clean_cell(history),
        _clean_cell(test),
        _clean_cell(treatment),
        _clean_cell(dx),
        _clean_cell(follow_up),
        _clean_cell(notes),
    ]
    if format_mode == "book":
        return base
    return [
        _clean_cell(record.source_title),
        _clean_cell(record.formatted_year()),
        *base,
    ]


def _build_notes_rows(
    records: list[CaseRecord],
    format_mode: Literal["journal", "book"],
    layer: Literal["normalized", "raw"],
    cfg: AppConfig,
    qa_report: QAReport | None,
) -> list[tuple[str, str]]:
    columns = BOOK_COLUMNS if format_mode == "book" else JOURNAL_COLUMNS
    rows: list[tuple[str, str]] = [
        ("工具版本", f"vet-case-extractor v{__version__} (rule_version={cfg.rule_version})"),
        ("生成时间 (UTC)", utc_now_iso()),
        ("导出模式", "扫描书籍 7 列 (book)" if format_mode == "book" else "期刊病例 9 列 (journal)"),
        (
            "数据层 (layer)",
            "normalized（遵循 Dx 五则与 Rx 术语标准化，保留 Raw 于 cases.jsonl）"
            if layer == "normalized"
            else "raw（未经规则改写的逐字原文抽取层）",
        ),
        ("导出病例总数", str(len(records))),
        ("列顺序", " | ".join(columns)),
        (
            "核心纪律",
            "所有事实均逐字取自原文（或 OCR 原版文本），缺失字段留空 `\"\"`，绝不使用「无/未提及/N/A/—」占位，严禁编造、改写或补充未提及诊疗。",
        ),
        (
            "字段口径",
            "medical_history 含信号量/主诉/病史/一般体格检查(PE)/外院既往诊断(Tier C)；"
            "medical_test 含实验室/影像/神经学客观发现/专科检查/本次诊断性操作（不含一般 PE 与推理）；"
            "medical_treatment 含 Tx/手术/Rx（不含死亡/复查/好转等随访；途径频次按 po/iv/im/sc + sid/bid/tid/qid 规范化）；"
            "follow-up 含治疗结果、回访与死后剖检/尸检及伴随组织病理学结果（存活术中探查及离体肉眼检查留 medical_test）；"
            "备注集中存放剥离的推理/临床意义/神经学定位（【原文推理/解读剥离】）与 OCR 存疑提示。",
        ),
        (
            "Dx 书写五则",
            "1) 物种前置不空格且全行仅开头写一次物种；2) 主病在前；3) 多诊断用中文分号 `；` 间隔；"
            "4) 四类括号判别：解剖部位/限定词保留、解读性内容移入备注、更规范病名替换外层俗称、非诊断症状丢弃；"
            "5) 缩写先全称后括号，如 `犬颈椎C4~C5水合髓核脱出（HNPE）`。",
        ),
        (
            "OCR 与数值陷阱纪律",
            "检验表原始数字、单位与参考区间严禁自动篡改；若存在量级冲突(10⁹/10¹²)、前导1伪影、错位或形近词(GCT/TGA/Ma/TB)，均保留原值并标记复核，需人工对照页图确认。",
        ),
    ]
    if qa_report is not None:
        rows.append(
            (
                "QA 汇总",
                f"overall_status={qa_report.overall_status} | 文档={qa_report.total_documents} | "
                f"病例={qa_report.total_cases} | 待复核={qa_report.review_required_cases} | "
                f"失败={qa_report.failed_cases} | 问题条目={len(qa_report.issues)}",
            )
        )
    return rows


def export_to_excel(
    records: list[CaseRecord],
    output_path: str | Path,
    *,
    format_mode: Literal["auto", "journal", "book"] = "auto",
    layer: Literal["normalized", "raw"] = "normalized",
    source_filenames: dict[str, str] | None = None,
    default_source_type: SourceType = SourceType.AUTO,
    cfg: AppConfig | None = None,
    qa_report: QAReport | None = None,
) -> Path:
    """Export CaseRecords to a two-sheet (`病例数据`, `说明`) Excel workbook."""
    if layer not in ("normalized", "raw"):
        raise ValueError(f"Unsupported layer '{layer}': expected 'normalized' or 'raw'.")

    resolved_cfg = cfg or load_config()
    effective_format = resolve_export_format(
        records=records,
        format_mode=format_mode,
        default_source_type=default_source_type,
    )
    sorted_records = sort_records_for_export(
        records=records,
        format_mode=effective_format,
        source_filenames=source_filenames,
    )
    columns = BOOK_COLUMNS if effective_format == "book" else JOURNAL_COLUMNS

    wb = Workbook()
    ws_data = wb.active
    ws_data.title = "病例数据"

    header_font = Font(name="Microsoft YaHei", size=11, bold=True)
    body_font = Font(name="Microsoft YaHei", size=10)
    header_fill = PatternFill(fill_type="solid", fgColor="E8EEF5")
    thin_side = Side(border_style="thin", color="D0D7DE")
    cell_border = Border(left=thin_side, right=thin_side, top=thin_side, bottom=thin_side)

    header_align = Alignment(
        wrap_text=resolved_cfg.export.wrap_text,
        vertical="center",
        horizontal="left",
    )
    body_align = Alignment(
        wrap_text=resolved_cfg.export.wrap_text,
        vertical="top",
        horizontal="left",
    )

    # Write header row
    for col_idx, col_name in enumerate(columns, start=1):
        cell = ws_data.cell(row=1, column=col_idx, value=col_name)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = header_align
        cell.border = cell_border
        col_letter = get_column_letter(col_idx)
        ws_data.column_dimensions[col_letter].width = _COLUMN_WIDTHS.get(col_name, 28)
    ws_data.row_dimensions[1].height = 24

    # Write data rows
    for row_idx, rec in enumerate(sorted_records, start=2):
        values = _record_row_values(rec, format_mode=effective_format, layer=layer)
        for col_idx, val in enumerate(values, start=1):
            cell = ws_data.cell(row=row_idx, column=col_idx)
            cell.value = val
            cell.data_type = "s"
            cell.number_format = "@"
            cell.font = body_font
            cell.alignment = body_align
            cell.border = cell_border

    if resolved_cfg.export.freeze_header:
        ws_data.freeze_panes = "A2"

    # Build `说明` sheet
    ws_notes = wb.create_sheet(title="说明")
    for col_idx, header in enumerate(("项目", "说明内容"), start=1):
        cell = ws_notes.cell(row=1, column=col_idx, value=header)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = header_align
        cell.border = cell_border
    ws_notes.column_dimensions["A"].width = 24
    ws_notes.column_dimensions["B"].width = 96
    ws_notes.row_dimensions[1].height = 24

    for row_idx, (k, v) in enumerate(
        _build_notes_rows(sorted_records, effective_format, layer, resolved_cfg, qa_report),
        start=2,
    ):
        c1 = ws_notes.cell(row=row_idx, column=1, value=_clean_cell(k))
        c2 = ws_notes.cell(row=row_idx, column=2, value=_clean_cell(v))
        for cell in (c1, c2):
            cell.data_type = "s"
            cell.number_format = "@"
            cell.font = body_font
            cell.alignment = body_align
            cell.border = cell_border
    ws_notes.freeze_panes = "A2"

    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".xlsx", dir=str(target.parent))
    os.close(fd)
    tmp_path = Path(tmp_name)
    try:
        wb.save(str(tmp_path))
        wb.close()
        os.replace(tmp_path, target)
    except Exception:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
        raise

    return target
