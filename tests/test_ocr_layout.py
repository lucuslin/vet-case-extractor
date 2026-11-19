"""Tests for dual-column layout restoration, lab table trap detection, and real RapidOCR."""

from __future__ import annotations

import io
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pymupdf as fitz
import pytest
from PIL import Image, ImageDraw, ImageFont

from vet_case_extractor.config import AppConfig, LayoutConfig, OCRConfig, load_config
from vet_case_extractor.ingestion.project import ProjectWorkspace
from vet_case_extractor.layout.columns import restore_page_layout
from vet_case_extractor.models import (
    BBox,
    OCRPolicy,
    PageExtractionEngine,
    SourceType,
    TaskStatus,
    TextBlock,
)
from vet_case_extractor.ocr.engine import (
    configure_ocr_thread_env,
    create_ocr_engine,
    determine_safe_workers,
    run_ocr_for_project,
)
from vet_case_extractor.ocr.tables import extract_lab_rows
from vet_case_extractor.pdf.reader import ingest_document

_MSYH_FONT = Path(r"C:\Windows\Fonts\msyh.ttc")
_SIMSUN_FONT = Path(r"C:\Windows\Fonts\simsun.ttc")


def _pick_chinese_font() -> Path | None:
    for candidate in (_SIMSUN_FONT, _MSYH_FONT):
        if candidate.is_file():
            return candidate
    return None


def test_restore_page_layout_single_column_no_markers() -> None:
    cfg = LayoutConfig()
    blocks = [
        TextBlock(
            block_id="b2",
            text="第二段正文内容：患猫精神沉郁。",
            bbox=BBox(x0=40, y0=200, x1=520, y1=230),
        ),
        TextBlock(
            block_id="b1",
            text="1 病例介绍",
            bbox=BBox(x0=40, y0=120, x1=150, y1=145),
        ),
    ]
    ordered, layout_text = restore_page_layout(blocks, page_width=595, page_height=842, cfg=cfg)
    assert [b.block_id for b in ordered] == ["b1", "b2"]
    assert all(b.column == "full" for b in ordered)
    assert "[[左栏]]" not in layout_text
    assert "[[右栏]]" not in layout_text
    assert layout_text.startswith("1 病例介绍\n第二段正文内容")


def test_restore_page_layout_dual_column_with_bands_and_spanning_blocks() -> None:
    cfg = LayoutConfig()
    blocks = [
        # Out of order deliberately
        TextBlock(
            block_id="right_top_2",
            text="右上第二段",
            bbox=BBox(x0=320, y0=260, x1=550, y1=290),
        ),
        TextBlock(
            block_id="left_bot_1",
            text="左下第一段",
            bbox=BBox(x0=40, y0=500, x1=270, y1=530),
        ),
        TextBlock(
            block_id="header",
            text="中国兽医杂志 2023年第2期",
            bbox=BBox(x0=50, y0=15, x1=540, y1=40),
        ),
        TextBlock(
            block_id="title",
            text="通栏标题：猫临床病例分析",
            bbox=BBox(x0=80, y0=80, x1=515, y1=120),
        ),
        TextBlock(
            block_id="right_bot_1",
            text="右下第一段",
            bbox=BBox(x0=320, y0=505, x1=550, y1=535),
        ),
        TextBlock(
            block_id="mid_table",
            text="通栏跨栏表格：血常规检查摘要",
            bbox=BBox(x0=40, y0=360, x1=550, y1=430),
        ),
        TextBlock(
            block_id="left_top_1",
            text="左上第一段",
            bbox=BBox(x0=40, y0=180, x1=270, y1=210),
        ),
        TextBlock(
            block_id="right_top_1",
            text="右上第一段",
            bbox=BBox(x0=320, y0=185, x1=550, y1=215),
        ),
        TextBlock(
            block_id="left_top_2",
            text="左上第二段",
            bbox=BBox(x0=40, y0=255, x1=270, y1=285),
        ),
        TextBlock(
            block_id="footer",
            text="第1页",
            bbox=BBox(x0=270, y0=800, x1=325, y1=825),
        ),
    ]

    ordered, layout_text = restore_page_layout(blocks, page_width=595, page_height=842, cfg=cfg)
    assert [b.block_id for b in ordered] == [
        "header",
        "title",
        "left_top_1",
        "left_top_2",
        "right_top_1",
        "right_top_2",
        "mid_table",
        "left_bot_1",
        "right_bot_1",
        "footer",
    ]
    assert ordered[0].column == "header"
    assert ordered[1].column == "full"
    assert ordered[2].column == "left"
    assert ordered[4].column == "right"
    assert ordered[6].column == "full"
    assert ordered[-1].column == "footer"
    assert "[[通栏]]" in layout_text
    assert "[[左栏]]" in layout_text
    assert "[[右栏]]" in layout_text


def test_extract_lab_rows_preserves_raw_and_detects_all_ocr_traps() -> None:
    cfg = load_config()
    rules = cfg.rules

    table_lines = [
        "血常规检查",
        "项目 | 结果 | 单位 | 参考范围 | 标记",
        "WBC | 15.2 | ×10¹²/L | 5.5~19.5 ×10⁹/L | ↑",
        "PLT | 240 | ×10%/L | 150~400 |",
        "NEU | 8.5 | ×10个/L | 2.5~12.5 |",
        "CREA | 155 | mg/dL | 0.8~1.8 | ↑",
        "HGB | 111.5 | g/dL | 8.0~15.0 |",
        "PCV | 134% | % | 24~45% | ↑",
        "ALB | 3.2 | g/dL | 25~40 g/L |",
        "GCT | 88 | U/L | 0~10 | ↑",
        "TGA | 1910 | mg/dL | 20~100 | ↑",
        "Ma | 0.9 | mmol/L | 0.7~1.2 |",
        "TB | 65 | g/L | 54~78 |",
    ]
    blocks = [
        TextBlock(
            block_id=f"row_{i}",
            text=line,
            bbox=BBox(x0=50, y0=100 + i * 24, x1=500, y1=120 + i * 24),
        )
        for i, line in enumerate(table_lines)
    ]

    rows = extract_lab_rows(
        blocks=blocks,
        layout_text="\n".join(table_lines),
        page_index=2,
        rules=rules,
    )
    by_item = {r.item_raw: r for r in rows}

    # Verify raw values are NEVER mutated
    assert by_item["HGB"].value_raw == "111.5"
    assert by_item["PCV"].value_raw == "134%"
    assert by_item["PLT"].unit_raw == "×10%/L"
    assert by_item["GCT"].item_raw == "GCT"
    assert by_item["WBC"].page_index == 2
    assert by_item["WBC"].bbox is not None
    assert by_item["WBC"].table_title == "血常规检查"

    # Verify all specific traps flagged in anomalies
    assert any("magnitude_exponent_conflict" in a for a in by_item["WBC"].anomalies)
    assert any("ocr_unit_corruption" in a for a in by_item["PLT"].anomalies)
    assert any("ocr_unit_corruption" in a for a in by_item["NEU"].anomalies)
    assert any("missing_decimal_or_extreme_ratio" in a for a in by_item["CREA"].anomalies)
    assert any("leading_one_artifact" in a for a in by_item["HGB"].anomalies)
    assert any("leading_one_artifact" in a for a in by_item["PCV"].anomalies)
    assert any("unit_mismatch" in a for a in by_item["ALB"].anomalies)
    assert any("look_alike_abbreviation" in a and "GGT" in a for a in by_item["GCT"].anomalies)
    assert any("look_alike_abbreviation" in a and "TG" in a for a in by_item["TGA"].anomalies)
    assert any("extreme_ratio_vs_ref_range" in a for a in by_item["TGA"].anomalies)
    assert any("look_alike_abbreviation" in a and "Mg" in a for a in by_item["Ma"].anomalies)
    assert any("look_alike_abbreviation" in a and "TP" in a for a in by_item["TB"].anomalies)


def test_extract_lab_rows_detects_column_row_misalignment() -> None:
    rules = load_config().rules
    shifted_lines = [
        "血常规检查",
        "RBC | 125 | ×10¹²/L | 5.0~10.0 |",
        "HGB | 6.8 | g/L | 90~150 |",
        "PLT | | ×10⁹/L | 150~400 |",
    ]
    blocks = [
        TextBlock(
            block_id=f"s_{i}",
            text=ln,
            bbox=BBox(x0=40, y0=80 + i * 25, x1=480, y1=100 + i * 25),
        )
        for i, ln in enumerate(shifted_lines)
    ]
    rows = extract_lab_rows(blocks, "\n".join(shifted_lines), page_index=0, rules=rules)
    assert len(rows) == 3
    assert any("column_row_misalignment" in a for a in rows[0].anomalies)
    assert any("column_row_misalignment" in a for a in rows[1].anomalies)
    assert any("column_row_misalignment" in a for a in rows[2].anomalies)


def test_extract_lab_rows_side_by_side_columns_and_prose_guard() -> None:
    rules = load_config().rules
    blocks = [
        # Dual-column prose at the same y must NOT merge into fake lab rows
        TextBlock(
            block_id="prose_l",
            text="1.1 基本情况：患猫体重3.5kg，体温38.5℃。",
            bbox=BBox(x0=40, y0=60, x1=260, y1=85),
        ),
        TextBlock(
            block_id="prose_r",
            text="参考范围3.2~4.3，患猫精神状态良好。",
            bbox=BBox(x0=320, y0=60, x1=540, y1=85),
        ),
        # Side-by-side separate lab rows at the same y must stay separate
        TextBlock(
            block_id="lab_l",
            text="WBC 15.2 ×10⁹/L 5.5~19.5",
            bbox=BBox(x0=40, y0=120, x1=260, y1=145),
        ),
        TextBlock(
            block_id="lab_r",
            text="ALT 120 U/L 10~100 ↑",
            bbox=BBox(x0=320, y0=120, x1=540, y1=145),
        ),
        # Space-collapsed OCR lab row
        TextBlock(
            block_id="collapsed",
            text="PCV134%24~45%",
            bbox=BBox(x0=40, y0=160, x1=220, y1=185),
        ),
    ]
    rows = extract_lab_rows(blocks, "", page_index=0, rules=rules)
    items = [r.item_raw for r in rows]
    assert items == ["WBC", "ALT", "PCV"]
    assert any("leading_one_artifact" in a for a in rows[2].anomalies)


def test_ocr_worker_ram_scaling_and_engine_init() -> None:
    cfg = OCRConfig(workers=3, rec_batch_num=16, ort_intra_op_threads=2)

    with patch("vet_case_extractor.ocr.engine.psutil.virtual_memory") as mock_vm:
        mock_vm.return_value = SimpleNamespace(available=2 * (1024**3))
        assert determine_safe_workers(cfg) == 1

        mock_vm.return_value = SimpleNamespace(available=6 * (1024**3))
        assert determine_safe_workers(cfg) <= 2

        mock_vm.return_value = SimpleNamespace(available=16 * (1024**3))
        assert 1 <= determine_safe_workers(cfg) <= 3

    configure_ocr_thread_env(cfg)
    assert os.environ["OMP_NUM_THREADS"] == "2"
    assert os.environ["OPENBLAS_NUM_THREADS"] == "2"
    assert os.environ["MKL_NUM_THREADS"] == "2"

    ocr = create_ocr_engine(cfg)
    assert getattr(ocr.text_rec, "rec_batch_num", None) == 16


def test_real_rapidocr_dual_column_caching_and_page_error_recovery(tmp_path: Path) -> None:
    font_path = _pick_chinese_font()
    if font_path is None:
        pytest.skip("Windows Chinese font not found")

    # Render a dual-column synthetic Chinese scanned page with Pillow + Chinese font
    img_w, img_h = 1400, 1000
    img = Image.new("RGB", (img_w, img_h), "white")
    draw = ImageDraw.Draw(img)
    title_font = ImageFont.truetype(str(font_path), 36)
    body_font = ImageFont.truetype(str(font_path), 28)

    # Full-width spanning title
    draw.text((220, 60), "猫肝脏脂肪沉积症病例报告分析", fill="black", font=title_font)
    # Left column
    draw.text((80, 180), "左栏第一行：患猫四岁雌性已绝育", fill="black", font=body_font)
    draw.text((80, 240), "左栏第二行：主诉厌食呕吐三天", fill="black", font=body_font)
    # Right column
    draw.text((780, 180), "右栏第一行：腹部超声见肝脏高回声", fill="black", font=body_font)
    draw.text((780, 240), "右栏第二行：经鼻饲营养支持后康复", fill="black", font=body_font)
    # Lab table row inside image
    draw.text((80, 380), "PCV  134%  24~45%", fill="black", font=body_font)

    png_buf = io.BytesIO()
    img.save(png_buf, format="PNG")

    pdf_path = tmp_path / "scanned_dual_col.pdf"
    doc = fitz.open()
    p0 = doc.new_page(width=700, height=500)
    p0.insert_image(p0.rect, stream=png_buf.getvalue())
    p1 = doc.new_page(width=700, height=500)
    p1.insert_image(p1.rect, stream=png_buf.getvalue())
    doc.save(str(pdf_path))
    doc.close()

    ws = ProjectWorkspace(tmp_path / "ws")
    cfg: AppConfig = load_config()
    manifest, added, _ = ws.register_pdfs(pdf_path, source_type=SourceType.JOURNAL)
    doc_id = added[0]

    doc_rec, _ = ingest_document(
        manifest.documents[doc_id],
        ws,
        cfg,
        ocr_policy=OCRPolicy.AUTO,
        render_all_pages=True,
    )
    manifest.documents[doc_id] = doc_rec
    ws.save_manifest(manifest)
    assert doc_rec.pages_needing_ocr == [0, 1]

    # Corrupt page 1's rendered PNG so page 1 fails OCR while page 0 succeeds via real RapidOCR
    ws.page_img_path(doc_id, 1).write_bytes(b"corrupted png bytes")

    summary = run_ocr_for_project(ws, cfg, doc_id=doc_id, workers=1, force=False)
    assert summary["pages_total_needing_ocr"] == 2
    assert summary["pages_ocr_completed"] == 1
    assert summary["pages_failed"] == 1
    assert summary["pages_skipped_cached"] == 0

    # Page 0 persisted & cached cleanly with dual-column layout markers + lab row
    assert ws.page_ocr_path(doc_id, 0).exists()
    loaded_arts = ws.load_page_artifacts(doc_id)
    art0 = loaded_arts[0]
    assert art0.engine == PageExtractionEngine.RAPIDOCR
    assert art0.ocr_cached is True
    assert "猫" in art0.cleaned_text or "病例" in art0.cleaned_text
    assert "[[左栏]]" in art0.layout_text
    assert "[[右栏]]" in art0.layout_text
    assert art0.layout_text.index("左栏") < art0.layout_text.index("右栏")
    assert any(r.item_raw == "PCV" for r in art0.lab_rows)
    pcv_row = next(r for r in art0.lab_rows if r.item_raw == "PCV")
    assert any("leading_one_artifact" in a for a in pcv_row.anomalies)

    # Manifest records partial page failure without dropping page 0
    updated_manifest = ws.load_manifest()
    assert updated_manifest.documents[doc_id].pages_failed == [1]
    assert updated_manifest.documents[doc_id].status == TaskStatus.REVIEW_REQUIRED

    # Fix page 1 image and re-run without force: page 0 is skipped from cache, page 1 recovers!
    ws.page_img_path(doc_id, 1).write_bytes(png_buf.getvalue())
    summary2 = run_ocr_for_project(ws, cfg, doc_id=doc_id, workers=1, force=False)
    assert summary2["pages_skipped_cached"] == 1
    assert summary2["pages_ocr_completed"] == 1
    assert summary2["pages_failed"] == 0

    reloaded_manifest2 = ws.load_manifest()
    assert reloaded_manifest2.documents[doc_id].pages_failed == []
    assert reloaded_manifest2.documents[doc_id].status == TaskStatus.COMPLETED

    # Run with force=True re-executes OCR for both cached pages
    summary3 = run_ocr_for_project(ws, cfg, doc_id=doc_id, workers=1, force=True)
    assert summary3["pages_ocr_completed"] == 2
    assert summary3["pages_skipped_cached"] == 0
