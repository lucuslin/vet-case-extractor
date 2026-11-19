"""Primary PyMuPDF PDF reader with selective pdfplumber fallback and page rendering."""

from __future__ import annotations

import contextlib
import logging
from pathlib import Path
from typing import Any

import pymupdf as fitz

from vet_case_extractor.config import AppConfig
from vet_case_extractor.ingestion.project import ProjectWorkspace
from vet_case_extractor.layout.columns import restore_page_layout
from vet_case_extractor.models import (
    BBox,
    DocumentRecord,
    OCRPolicy,
    PageArtifact,
    PageExtractionEngine,
    SourceType,
    TaskStatus,
    TextBlock,
    utc_now_iso,
)
from vet_case_extractor.ocr.tables import extract_lab_rows
from vet_case_extractor.pdf.quality import assess_page_quality
from vet_case_extractor.utils.text import clean_ocr_noise

logger = logging.getLogger("vet_case_extractor.pdf")


def _detect_images_on_page(
    page: fitz.Page,
    page_width: float,
    page_height: float,
    raw_blocks: list[tuple[Any, ...]],
) -> tuple[int, bool]:
    page_area = max(0.0, page_width * page_height)
    image_infos: list[dict[str, Any]] = []
    try:
        image_infos = page.get_image_info(xrefs=False)
    except Exception:
        image_infos = []

    image_count = len(image_infos)
    if image_count == 0:
        try:
            image_count = len(page.get_images(full=True))
        except Exception:
            image_count = 0

    img_block_rects = [
        (float(b[0]), float(b[1]), float(b[2]), float(b[3]))
        for b in raw_blocks
        if len(b) >= 7 and int(b[6]) == 1
    ]
    if image_count == 0 and img_block_rects:
        image_count = len(img_block_rects)

    full_page_image = False
    if page_area > 0.0:
        candidate_rects: list[tuple[float, float, float, float]] = []
        for info in image_infos:
            bbox = info.get("bbox")
            if bbox and len(bbox) >= 4:
                candidate_rects.append(
                    (float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3]))
                )
        candidate_rects.extend(img_block_rects)

        for x0, y0, x1, y1 in candidate_rects:
            ix0 = max(0.0, min(page_width, x0))
            iy0 = max(0.0, min(page_height, y0))
            ix1 = max(0.0, min(page_width, x1))
            iy1 = max(0.0, min(page_height, y1))
            inter_area = max(0.0, ix1 - ix0) * max(0.0, iy1 - iy0)
            if inter_area / page_area >= 0.60:
                full_page_image = True
                break

    return image_count, full_page_image


def _words_to_line_blocks(
    words: list[dict[str, Any]],
    page_idx: int,
) -> list[TextBlock]:
    if not words:
        return []
    sorted_words = sorted(words, key=lambda w: (float(w.get("top", 0.0)), float(w.get("x0", 0.0))))
    lines: list[list[dict[str, Any]]] = []
    for word in sorted_words:
        w_top = float(word.get("top", 0.0))
        if not lines:
            lines.append([word])
            continue
        prev_top = float(lines[-1][0].get("top", 0.0))
        if abs(w_top - prev_top) <= 3.5:
            lines[-1].append(word)
        else:
            lines.append([word])

    blocks: list[TextBlock] = []
    for idx, line_words in enumerate(lines):
        line_words.sort(key=lambda w: float(w.get("x0", 0.0)))
        line_text = " ".join(str(w.get("text", "")).strip() for w in line_words if w.get("text"))
        cleaned = clean_ocr_noise(line_text, repair_founder=True)
        if not cleaned:
            continue
        x0 = min(float(w.get("x0", 0.0)) for w in line_words)
        y0 = min(float(w.get("top", 0.0)) for w in line_words)
        x1 = max(float(w.get("x1", 0.0)) for w in line_words)
        y1 = max(float(w.get("bottom", 0.0)) for w in line_words)
        blocks.append(
            TextBlock(
                block_id=f"p{page_idx}_plumber_{idx}",
                text=cleaned,
                bbox=BBox(x0=x0, y0=y0, x1=x1, y1=y1),
                column="full",
                confidence=None,
                source="native",
            )
        )
    return blocks


def _extract_with_pdfplumber_page(
    pdf_path: Path,
    page_idx: int,
    page_width: float,
    page_height: float,
    plumber_doc: Any | None,
) -> tuple[Any | None, str, list[TextBlock]]:
    import pdfplumber

    if plumber_doc is None:
        plumber_doc = pdfplumber.open(pdf_path)

    p_page = plumber_doc.pages[page_idx]
    text = p_page.extract_text() or ""
    if not text.strip():
        return plumber_doc, "", []

    words = p_page.extract_words() or []
    blocks = _words_to_line_blocks(words, page_idx=page_idx)
    if not blocks:
        cleaned = clean_ocr_noise(text, repair_founder=True)
        if cleaned:
            blocks = [
                TextBlock(
                    block_id=f"p{page_idx}_plumber_0",
                    text=cleaned,
                    bbox=BBox(x0=0.0, y0=0.0, x1=page_width, y1=page_height),
                    column="full",
                    confidence=None,
                    source="native",
                )
            ]
    return plumber_doc, text, blocks


def ingest_document(
    doc_rec: DocumentRecord,
    ws: ProjectWorkspace,
    cfg: AppConfig,
    ocr_policy: OCRPolicy = OCRPolicy.AUTO,
    render_all_pages: bool | None = None,
) -> tuple[DocumentRecord, list[PageArtifact]]:
    """Ingest a PDF with PyMuPDF primary extraction, selective pdfplumber fallback, and PNG rendering."""
    pdf_path = Path(doc_rec.file_path)
    should_render_all = (
        cfg.pdf.render_all_pages_for_review if render_all_pages is None else render_all_pages
    )
    render_dpi = (
        cfg.pdf.book_render_dpi
        if doc_rec.source_type == SourceType.BOOK
        else cfg.pdf.journal_render_dpi
    )

    try:
        pdf_doc = fitz.open(pdf_path)
    except Exception as exc:
        doc_rec.status = TaskStatus.FAILED
        doc_rec.error = f"Failed to open PDF '{pdf_path.name}': {type(exc).__name__}: {exc}"
        doc_rec.updated_at = utc_now_iso()
        logger.error(doc_rec.error)
        return doc_rec, []

    plumber_doc: Any | None = None
    artifacts: list[PageArtifact] = []
    pages_needing_ocr: list[int] = []
    pages_failed: list[int] = []

    try:
        if pdf_doc.is_encrypted and pdf_doc.needs_pass:
            doc_rec.status = TaskStatus.FAILED
            doc_rec.error = f"PDF '{pdf_path.name}' is password-protected and cannot be read."
            doc_rec.updated_at = utc_now_iso()
            return doc_rec, []

        page_count = len(pdf_doc)
        if page_count == 0:
            doc_rec.status = TaskStatus.FAILED
            doc_rec.page_count = 0
            doc_rec.error = f"PDF '{pdf_path.name}' contains 0 pages."
            doc_rec.updated_at = utc_now_iso()
            return doc_rec, []

        doc_rec.page_count = page_count

        for page_idx in range(page_count):
            pdf_page_number = page_idx + 1
            book_page_number = (
                (pdf_page_number - doc_rec.page_offset)
                if (doc_rec.page_offset or doc_rec.source_type == SourceType.BOOK)
                else None
            )

            try:
                page = pdf_doc.load_page(page_idx)
                rect = page.rect
                page_width = float(rect.width)
                page_height = float(rect.height)

                raw_text = ""
                raw_blocks: list[tuple[Any, ...]] = []
                blocks: list[TextBlock] = []
                engine = PageExtractionEngine.PYMUPDF
                pymupdf_failed = False

                try:
                    raw_text = page.get_text("text") or ""
                    raw_blocks = list(page.get_text("blocks") or [])
                    for b_idx, blk in enumerate(raw_blocks):
                        if len(blk) < 7 or int(blk[6]) != 0:
                            continue
                        b_text = str(blk[4] or "")
                        cleaned_blk = clean_ocr_noise(b_text, repair_founder=True)
                        if not cleaned_blk:
                            continue
                        blocks.append(
                            TextBlock(
                                block_id=f"p{page_idx}_b{b_idx}",
                                text=cleaned_blk,
                                bbox=BBox(
                                    x0=float(blk[0]),
                                    y0=float(blk[1]),
                                    x1=float(blk[2]),
                                    y1=float(blk[3]),
                                ),
                                column="full",
                                confidence=None,
                                source="native",
                            )
                        )
                    if not raw_text.strip() and blocks:
                        raw_text = "\n".join(b.text for b in blocks)
                except Exception as exc:
                    pymupdf_failed = True
                    logger.warning(
                        "PyMuPDF text extraction failed on %s page %d: %s",
                        pdf_path.name,
                        page_idx,
                        exc,
                    )

                image_count, full_page_image = _detect_images_on_page(
                    page, page_width, page_height, raw_blocks
                )

                # Selective pdfplumber fallback ONLY when PyMuPDF fails or yields empty text
                if (pymupdf_failed or not raw_text.strip()) and cfg.pdf.use_pdfplumber_fallback:
                    try:
                        plumber_doc, p_text, p_blocks = _extract_with_pdfplumber_page(
                            pdf_path=pdf_path,
                            page_idx=page_idx,
                            page_width=page_width,
                            page_height=page_height,
                            plumber_doc=plumber_doc,
                        )
                        if p_text.strip():
                            raw_text = p_text
                            blocks = p_blocks
                            engine = PageExtractionEngine.PDFPLUMBER
                    except Exception as exc:
                        logger.warning(
                            "pdfplumber fallback failed on %s page %d: %s",
                            pdf_path.name,
                            page_idx,
                            exc,
                        )
                        if pymupdf_failed:
                            engine = PageExtractionEngine.FAILED

                quality = assess_page_quality(
                    raw_text=raw_text,
                    image_count=image_count,
                    full_page_image=full_page_image,
                    cfg=cfg.pdf,
                    ocr_policy=ocr_policy,
                )

                cleaned_text = clean_ocr_noise(raw_text, repair_founder=True)
                ordered_blocks, layout_text = restore_page_layout(
                    blocks=blocks,
                    page_width=page_width,
                    page_height=page_height,
                    cfg=cfg.layout,
                )
                if not layout_text and cleaned_text:
                    layout_text = cleaned_text

                lab_rows = (
                    extract_lab_rows(
                        blocks=ordered_blocks,
                        layout_text=layout_text,
                        page_index=page_idx,
                        rules=cfg.rules,
                    )
                    if (not quality.needs_ocr or ocr_policy == OCRPolicy.NEVER) and ordered_blocks
                    else []
                )

                image_path_str: str | None = None
                if quality.needs_ocr or should_render_all:
                    img_path = ws.page_img_path(doc_rec.document_id, page_idx)
                    img_path.parent.mkdir(parents=True, exist_ok=True)
                    pix = page.get_pixmap(dpi=render_dpi)
                    pix.save(str(img_path))
                    image_path_str = str(img_path)

                if quality.needs_ocr:
                    pages_needing_ocr.append(page_idx)
                if engine == PageExtractionEngine.FAILED:
                    pages_failed.append(page_idx)

                artifact = PageArtifact(
                    document_id=doc_rec.document_id,
                    page_index=page_idx,
                    pdf_page_number=pdf_page_number,
                    book_page_number=book_page_number,
                    width=page_width,
                    height=page_height,
                    engine=engine,
                    quality=quality,
                    raw_text=raw_text,
                    cleaned_text=cleaned_text,
                    layout_text=layout_text,
                    blocks=ordered_blocks,
                    lab_rows=lab_rows,
                    image_path=image_path_str,
                    ocr_cached=False,
                    error=None,
                )
            except Exception as exc:
                err_msg = f"Page {page_idx} ingestion error: {type(exc).__name__}: {exc}"
                logger.error("%s (%s)", err_msg, pdf_path.name)
                pages_failed.append(page_idx)
                artifact = PageArtifact(
                    document_id=doc_rec.document_id,
                    page_index=page_idx,
                    pdf_page_number=pdf_page_number,
                    book_page_number=book_page_number,
                    engine=PageExtractionEngine.FAILED,
                    error=err_msg,
                )

            ws.save_page_artifact(artifact)
            artifacts.append(artifact)

        doc_rec.pages_needing_ocr = pages_needing_ocr
        doc_rec.pages_failed = pages_failed
        doc_rec.updated_at = utc_now_iso()

        if len(pages_failed) == page_count:
            doc_rec.status = TaskStatus.FAILED
            doc_rec.error = f"All {page_count} pages failed during ingestion."
        elif pages_failed:
            doc_rec.status = TaskStatus.REVIEW_REQUIRED
            doc_rec.error = f"Partial page ingestion failures: {pages_failed}"
        else:
            doc_rec.status = TaskStatus.COMPLETED
            doc_rec.error = None

        return doc_rec, artifacts
    finally:
        pdf_doc.close()
        if plumber_doc is not None:
            with contextlib.suppress(Exception):
                plumber_doc.close()
