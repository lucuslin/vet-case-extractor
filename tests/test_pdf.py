"""Tests for PDF quality assessment, PyMuPDF reader, pdfplumber fallback, and smoke checks."""

from __future__ import annotations

import io
import os
from pathlib import Path
from unittest.mock import patch

import pymupdf as fitz
import pytest
from PIL import Image, ImageDraw, ImageFont

from vet_case_extractor.config import AppConfig, PDFConfig
from vet_case_extractor.ingestion.project import ProjectWorkspace
from vet_case_extractor.models import (
    OCRPolicy,
    PageExtractionEngine,
    SourceType,
    TaskStatus,
)
from vet_case_extractor.pdf.quality import assess_page_quality
from vet_case_extractor.pdf.reader import ingest_document
from vet_case_extractor.utils.fonts import find_cjk_font


def _pick_chinese_font() -> Path | None:
    try:
        return find_cjk_font()
    except FileNotFoundError:
        return None


def test_quality_distinguishes_founder_pua_from_corrupted_pua() -> None:
    cfg = PDFConfig()
    # CNKI/Founder PUA punctuation (\ua3ac ',', \ua3ae '.', \ua3bb ';', \U001001b0 '.')
    founder_text = (
        "猫弥散性虹膜黑色素瘤的病例报告\ua3ac异国短毛猫\ua3ac11岁\ua3bb"
        "雄性\ua3ac体重2\U001001b077kg\ua3ae主诉右眼瞳孔偏小\ua3ac裂隙灯检查见虹膜表面大量黑点\ua3ae"
        "行眼球摘除术及组织病理学检查\ua3ae"
    )
    metrics = assess_page_quality(
        raw_text=founder_text,
        image_count=1,
        full_page_image=False,
        cfg=cfg,
        ocr_policy=OCRPolicy.AUTO,
    )
    assert metrics.founder_pua_count == 9
    assert metrics.pua_count == 0
    assert metrics.replacement_char_count == 0
    assert metrics.garbled_ratio == 0.0
    assert metrics.needs_ocr is False

    # Truly corrupted PUA and replacement chars
    corrupt_text = "猫病例报告" + ("\ue001\ue002\ufffd" * 20)
    corrupt_metrics = assess_page_quality(
        raw_text=corrupt_text,
        image_count=0,
        full_page_image=False,
        cfg=cfg,
        ocr_policy=OCRPolicy.AUTO,
    )
    assert corrupt_metrics.pua_count == 40
    assert corrupt_metrics.replacement_char_count == 20
    assert corrupt_metrics.garbled_ratio > cfg.max_garbled_ratio
    assert corrupt_metrics.needs_ocr is True
    assert "garbled" in corrupt_metrics.ocr_reason


def test_quality_thresholds_and_ocr_policies() -> None:
    cfg = PDFConfig()

    short_metrics = assess_page_quality("短文本", 0, False, cfg, OCRPolicy.AUTO)
    assert short_metrics.needs_ocr is True
    assert "low_char_count" in short_metrics.ocr_reason

    img_low_cjk = assess_page_quality(
        "Journal of Veterinary Medicine Vol 59 No 2 English abstract only text here.",
        image_count=1,
        full_page_image=True,
        cfg=cfg,
        ocr_policy=OCRPolicy.AUTO,
    )
    assert img_low_cjk.needs_ocr is True
    assert "full_page_image_low_cjk" in img_low_cjk.ocr_reason

    # Full page image with high-CJK embedded OCR text skips OCR under AUTO, forces under FORCE_IMAGE_PAGES
    cjk_with_bg = "猫临床病例分析血常规检查生化检查结果如下患猫体温正常精神良好食欲正常体重四公斤。" * 2
    auto_res = assess_page_quality(cjk_with_bg, 1, True, cfg, OCRPolicy.AUTO)
    assert auto_res.needs_ocr is False

    force_img_res = assess_page_quality(cjk_with_bg, 1, True, cfg, OCRPolicy.FORCE_IMAGE_PAGES)
    assert force_img_res.needs_ocr is True
    assert "policy_force_image_pages" in force_img_res.ocr_reason

    never_res = assess_page_quality("", 1, True, cfg, OCRPolicy.NEVER)
    assert never_res.needs_ocr is False

    force_all_res = assess_page_quality(cjk_with_bg, 0, False, cfg, OCRPolicy.FORCE_ALL)
    assert force_all_res.needs_ocr is True


def test_ingest_native_and_scanned_and_book_page_offset(tmp_path: Path) -> None:
    font_path = _pick_chinese_font()
    if font_path is None:
        pytest.skip("Windows Chinese font not found")

    pdf_path = tmp_path / "synthetic_book.pdf"
    doc = fitz.open()
    # Page 0: native Chinese text
    p0 = doc.new_page(width=595, height=842)
    p0.insert_font(fontname="cjk", fontfile=str(font_path))
    p0.insert_text(
        (50, 80),
        "第一章 肝胆疾病\n病例1 等级1\n患猫，3岁，雌性，体重3.5kg，因厌食、黄疸就诊。"
        "一般体格检查体温38.5℃，心率160次/min。",
        fontname="cjk",
        fontsize=12,
    )
    # Page 1: pure scanned image page
    p1 = doc.new_page(width=595, height=842)
    img = Image.new("RGB", (800, 1100), "white")
    draw = ImageDraw.Draw(img)
    pil_font = ImageFont.truetype(str(font_path), 28)
    draw.text((60, 80), "扫描页测试文本", fill="black", font=pil_font)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    p1.insert_image(p1.rect, stream=buf.getvalue())
    doc.save(str(pdf_path))
    doc.close()

    ws = ProjectWorkspace(tmp_path / "ws")
    manifest, added, _ = ws.register_pdfs(pdf_path, source_type=SourceType.BOOK, page_offset=1)
    doc_rec = manifest.documents[added[0]]
    cfg = AppConfig()

    updated_doc, artifacts = ingest_document(
        doc_rec,
        ws,
        cfg,
        ocr_policy=OCRPolicy.AUTO,
        render_all_pages=False,
    )
    assert updated_doc.status == TaskStatus.COMPLETED
    assert updated_doc.page_count == 2
    assert updated_doc.pages_needing_ocr == [1]
    assert len(artifacts) == 2

    # Page 0 native: pdf_page_number=1, book_page_number=0 (1 - offset 1), not rendered when render_all_pages=False
    assert artifacts[0].page_index == 0
    assert artifacts[0].pdf_page_number == 1
    assert artifacts[0].book_page_number == 0
    assert artifacts[0].engine == PageExtractionEngine.PYMUPDF
    assert artifacts[0].quality.needs_ocr is False
    assert artifacts[0].image_path is None

    # Page 1 scanned: rendered at book_render_dpi=250
    assert artifacts[1].page_index == 1
    assert artifacts[1].pdf_page_number == 2
    assert artifacts[1].book_page_number == 1
    assert artifacts[1].quality.needs_ocr is True
    assert artifacts[1].image_path is not None
    assert Path(artifacts[1].image_path).exists()
    with Image.open(artifacts[1].image_path) as rendered:
        # 595 pt * 250 / 72 ≈ 2066 px
        assert 2000 <= rendered.width <= 2120


def test_ingest_pdfplumber_fallback_only_on_pymupdf_failure(tmp_path: Path) -> None:
    pdf_path = tmp_path / "fallback.pdf"
    doc = fitz.open()
    p0 = doc.new_page(width=595, height=842)
    p0.insert_text((50, 80), "Native PyMuPDF text enough characters " * 3, fontsize=11)
    p1 = doc.new_page(width=595, height=842)
    p1.insert_text((50, 80), "Fallback page ASCII text line " * 3, fontsize=11)
    doc.save(str(pdf_path))
    doc.close()

    ws = ProjectWorkspace(tmp_path / "ws")
    manifest, added, _ = ws.register_pdfs(pdf_path, source_type=SourceType.JOURNAL)
    doc_rec = manifest.documents[added[0]]
    cfg = AppConfig()

    orig_get_text = fitz.Page.get_text

    def selective_get_text(self: fitz.Page, *args: object, **kwargs: object) -> object:
        if self.number == 1:
            return "" if args and args[0] == "text" else []
        return orig_get_text(self, *args, **kwargs)

    with (
        patch.object(fitz.Page, "get_text", selective_get_text),
        patch(
            "vet_case_extractor.pdf.reader._extract_with_pdfplumber_page",
            wraps=__import__(
                "vet_case_extractor.pdf.reader", fromlist=["_extract_with_pdfplumber_page"]
            )._extract_with_pdfplumber_page,
        ) as spy_plumber,
    ):
        updated_doc, artifacts = ingest_document(doc_rec, ws, cfg, render_all_pages=False)

    assert updated_doc.status == TaskStatus.COMPLETED
    assert artifacts[0].engine == PageExtractionEngine.PYMUPDF
    assert artifacts[1].engine == PageExtractionEngine.PDFPLUMBER
    # pdfplumber fallback called ONLY for page 1, never for page 0
    assert spy_plumber.call_count == 1
    assert spy_plumber.call_args.kwargs["page_idx"] == 1


def test_ingest_corrupted_pdf_sets_failed_without_crash(tmp_path: Path) -> None:
    corrupt_pdf = tmp_path / "corrupted.pdf"
    corrupt_pdf.write_bytes(b"THIS IS NOT A VALID PDF STREAM")

    ws = ProjectWorkspace(tmp_path / "ws")
    manifest, added, _ = ws.register_pdfs(corrupt_pdf, source_type=SourceType.JOURNAL)
    doc_rec = manifest.documents[added[0]]

    updated_doc, artifacts = ingest_document(doc_rec, ws, AppConfig())
    assert updated_doc.status == TaskStatus.FAILED
    assert updated_doc.error
    assert artifacts == []


def test_smoke_local_corpus_if_configured(tmp_path: Path) -> None:
    """Optional local PDF corpus smoke test when VETCASE_LOCAL_PAPERS_DIR is explicitly set."""
    env_dir = os.environ.get("VETCASE_LOCAL_PAPERS_DIR", "").strip()
    if not env_dir:
        pytest.skip("VETCASE_LOCAL_PAPERS_DIR not set; skipping local corpus smoke test")
    papers_dir = Path(env_dir)
    if not papers_dir.is_dir():
        pytest.skip("Configured VETCASE_LOCAL_PAPERS_DIR does not exist")
    pdfs = sorted(papers_dir.glob("*.pdf"))[:2]
    if not pdfs:
        pytest.skip("No PDF files found in VETCASE_LOCAL_PAPERS_DIR")

    ws = ProjectWorkspace(tmp_path / "smoke_ws")
    ws.init_project(source_type=SourceType.JOURNAL)
    cfg = AppConfig()

    for pdf_path in pdfs:
        manifest, added, _ = ws.register_pdfs(pdf_path, source_type=SourceType.JOURNAL)
        doc_rec, arts = ingest_document(
            manifest.documents[added[0]], ws, cfg, render_all_pages=False
        )
        assert doc_rec.status == TaskStatus.COMPLETED
        assert doc_rec.page_count == len(arts)
        assert len(arts) >= 1
