"""RapidOCR execution engine with memory-aware multiprocessing and per-page caching."""

from __future__ import annotations

import concurrent.futures
import logging
import os
from pathlib import Path
from typing import Any

import psutil
import pymupdf as fitz
from PIL import Image

from vet_case_extractor.config import AppConfig, OCRConfig
from vet_case_extractor.ingestion.project import ProjectWorkspace
from vet_case_extractor.layout.columns import restore_page_layout
from vet_case_extractor.models import (
    BBox,
    PageArtifact,
    PageExtractionEngine,
    SourceType,
    TaskStatus,
    TextBlock,
    utc_now_iso,
)
from vet_case_extractor.ocr.tables import extract_lab_rows
from vet_case_extractor.utils.io import atomic_write_json, read_json
from vet_case_extractor.utils.text import clean_ocr_noise

logger = logging.getLogger("vet_case_extractor.ocr")

_WORKER_OCR_ENGINE: Any = None


def configure_ocr_thread_env(cfg: OCRConfig) -> None:
    """Configure BLAS/OMP/ORT thread caps before loading ONNX Runtime."""
    threads = str(max(1, int(cfg.ort_intra_op_threads)))
    os.environ["OMP_NUM_THREADS"] = threads
    os.environ["OPENBLAS_NUM_THREADS"] = threads
    os.environ["MKL_NUM_THREADS"] = threads


def determine_safe_workers(cfg: OCRConfig, requested_workers: int | None = None) -> int:
    """Determine memory-bandwidth-safe OCR worker count from psutil available RAM."""
    avail_gb = psutil.virtual_memory().available / (1024**3)
    if avail_gb >= cfg.min_ram_gb_for_3_workers:
        ram_cap = 3
    elif avail_gb >= cfg.min_ram_gb_for_2_workers:
        ram_cap = 2
    else:
        ram_cap = 1

    cpu_cap = max(1, os.cpu_count() or 1)
    desired = cfg.workers if requested_workers is None else requested_workers
    return max(1, min(int(desired), ram_cap, cpu_cap, 3))


def create_ocr_engine(cfg: OCRConfig) -> Any:
    """Create a RapidOCR instance safely across rapidocr-onnxruntime 1.2.x and 1.4.x."""
    configure_ocr_thread_env(cfg)
    from rapidocr_onnxruntime import RapidOCR

    try:
        ocr = RapidOCR(
            use_cls=cfg.use_angle_cls,
            use_det=True,
            use_rec=True,
            intra_op_num_threads=cfg.ort_intra_op_threads,
            inter_op_num_threads=cfg.ort_inter_op_threads,
        )
    except Exception:
        ocr = RapidOCR(use_cls=cfg.use_angle_cls, use_det=True, use_rec=True)

    # Do not pass rec_batch_num directly to RapidOCR.__init__ (1.2.x raises KeyError).
    for attr_name in ("text_rec", "text_recognizer"):
        rec_obj = getattr(ocr, attr_name, None)
        if rec_obj is not None and hasattr(rec_obj, "rec_batch_num"):
            rec_obj.rec_batch_num = cfg.rec_batch_num

    return ocr


def _worker_init(ocr_cfg_dict: dict[str, Any]) -> None:
    global _WORKER_OCR_ENGINE
    cfg = OCRConfig.model_validate(ocr_cfg_dict)
    _WORKER_OCR_ENGINE = create_ocr_engine(cfg)


def _run_ocr_on_image_with_engine(
    img_path: Path,
    page_index: int,
    ocr_engine: Any,
) -> tuple[list[dict[str, Any]], str, int, int, Any]:
    with Image.open(img_path) as im:
        img_w, img_h = im.size
        rgb_img = im.convert("RGB")
        res, elapse = ocr_engine(rgb_img)

    blocks_dicts: list[dict[str, Any]] = []
    raw_lines: list[str] = []
    if res:
        for idx, item in enumerate(res):
            box, text, score = item[0], item[1], item[2]
            text_str = str(text or "")
            if not text_str.strip():
                continue
            xs = [float(pt[0]) for pt in box]
            ys = [float(pt[1]) for pt in box]
            raw_lines.append(text_str)
            blk = TextBlock(
                block_id=f"p{page_index}_ocr_{idx}",
                text=clean_ocr_noise(text_str, repair_founder=True) or text_str.strip(),
                bbox=BBox(x0=min(xs), y0=min(ys), x1=max(xs), y1=max(ys)),
                column="full",
                confidence=float(score) if score is not None else None,
                source="ocr",
            )
            blocks_dicts.append(blk.model_dump())

    raw_text = "\n".join(raw_lines)
    return blocks_dicts, raw_text, int(img_w), int(img_h), elapse


def _ocr_single_page_worker(task: dict[str, Any]) -> dict[str, Any]:
    global _WORKER_OCR_ENGINE
    doc_id = task["doc_id"]
    page_idx = int(task["page_idx"])
    img_path = Path(task["img_path"])
    pdf_path = Path(task["pdf_path"])
    dpi = int(task["dpi"])
    need_render = bool(task["need_render"])

    try:
        if need_render:
            img_path.parent.mkdir(parents=True, exist_ok=True)
            with fitz.open(pdf_path) as pdf_doc:
                page = pdf_doc.load_page(page_idx)
                pix = page.get_pixmap(dpi=dpi)
                pix.save(str(img_path))

        if _WORKER_OCR_ENGINE is None:
            ocr_cfg = OCRConfig.model_validate(task["ocr_cfg"])
            _WORKER_OCR_ENGINE = create_ocr_engine(ocr_cfg)

        blocks_dicts, raw_text, img_w, img_h, elapse = _run_ocr_on_image_with_engine(
            img_path=img_path,
            page_index=page_idx,
            ocr_engine=_WORKER_OCR_ENGINE,
        )
        return {
            "ok": True,
            "doc_id": doc_id,
            "page_idx": page_idx,
            "dpi": dpi,
            "img_path": str(img_path),
            "img_width": img_w,
            "img_height": img_h,
            "raw_text": raw_text,
            "blocks_dicts": blocks_dicts,
            "elapse": elapse if isinstance(elapse, list) else None,
            "error": None,
        }
    except Exception as exc:
        return {
            "ok": False,
            "doc_id": doc_id,
            "page_idx": page_idx,
            "dpi": dpi,
            "img_path": str(img_path),
            "error": f"{type(exc).__name__}: {exc}",
        }


def _apply_ocr_payload_to_artifact(
    artifact: PageArtifact,
    payload: dict[str, Any],
    cfg: AppConfig,
) -> PageArtifact:
    raw_text = str(payload.get("raw_text", ""))
    img_w = float(payload.get("image_width") or payload.get("img_width") or artifact.width)
    img_h = float(payload.get("image_height") or payload.get("img_height") or artifact.height)
    raw_blocks = [TextBlock.model_validate(b) for b in payload.get("blocks", [])]

    ordered_blocks, layout_text = restore_page_layout(
        raw_blocks,
        page_width=img_w,
        page_height=img_h,
        cfg=cfg.layout,
    )
    cleaned_text = clean_ocr_noise(raw_text, repair_founder=True)
    if not layout_text and cleaned_text:
        layout_text = cleaned_text

    lab_rows = extract_lab_rows(
        ordered_blocks,
        layout_text=layout_text,
        page_index=artifact.page_index,
        rules=cfg.rules,
    )

    artifact.width = img_w
    artifact.height = img_h
    artifact.engine = PageExtractionEngine.RAPIDOCR
    artifact.raw_text = raw_text
    artifact.cleaned_text = cleaned_text
    artifact.layout_text = layout_text
    artifact.blocks = ordered_blocks
    artifact.lab_rows = lab_rows
    artifact.ocr_cached = True
    artifact.error = None
    if payload.get("img_path"):
        artifact.image_path = str(payload["img_path"])
    return artifact


def run_ocr_for_project(
    ws: ProjectWorkspace,
    cfg: AppConfig,
    doc_id: str | None = None,
    dpi: int | None = None,
    workers: int | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Run RapidOCR on pages needing OCR across a project (or one document) with caching."""
    manifest = ws.load_manifest()
    if doc_id is not None:
        if doc_id not in manifest.documents:
            raise KeyError(f"Document ID '{doc_id}' not found in manifest")
        target_docs = [manifest.documents[doc_id]]
    else:
        target_docs = list(manifest.documents.values())

    safe_workers = determine_safe_workers(cfg.ocr, requested_workers=workers)
    tasks: list[dict[str, Any]] = []
    artifacts_by_doc: dict[str, dict[int, PageArtifact]] = {}
    pages_skipped_cached = 0
    pages_total_needing_ocr = 0
    attempted_pages_by_doc: dict[str, set[int]] = {}
    failed_details: list[dict[str, Any]] = []

    for doc in target_docs:
        if doc.status == TaskStatus.FAILED and doc.page_count == 0:
            continue

        artifacts = ws.load_page_artifacts(doc.document_id)
        if not artifacts:
            continue
        artifacts_by_doc[doc.document_id] = {a.page_index: a for a in artifacts}
        attempted_pages_by_doc[doc.document_id] = set()

        needing_set = set(doc.pages_needing_ocr)
        target_dpi = (
            dpi
            if dpi is not None
            else (
                cfg.pdf.book_render_dpi
                if doc.source_type == SourceType.BOOK
                else cfg.pdf.journal_render_dpi
            )
        )

        for art in artifacts:
            if not (art.quality.needs_ocr or art.page_index in needing_set):
                continue

            pages_total_needing_ocr += 1
            attempted_pages_by_doc[doc.document_id].add(art.page_index)
            cache_path = ws.page_ocr_path(doc.document_id, art.page_index)

            if not force and cache_path.exists():
                try:
                    cached_payload = read_json(cache_path)
                    if isinstance(cached_payload, dict) and "blocks" in cached_payload:
                        if art.engine != PageExtractionEngine.RAPIDOCR or not art.ocr_cached:
                            _apply_ocr_payload_to_artifact(art, cached_payload, cfg)
                            ws.save_page_artifact(art)
                        pages_skipped_cached += 1
                        continue
                except Exception as exc:
                    logger.warning(
                        "Corrupted OCR cache at %s (%s); re-running OCR.",
                        cache_path,
                        exc,
                    )

            img_path = (
                Path(art.image_path)
                if art.image_path
                else ws.page_img_path(doc.document_id, art.page_index)
            )
            need_render = (not img_path.exists()) or (dpi is not None)
            tasks.append(
                {
                    "doc_id": doc.document_id,
                    "page_idx": art.page_index,
                    "img_path": str(ws.page_img_path(doc.document_id, art.page_index) if need_render and not img_path.exists() else img_path),
                    "pdf_path": doc.file_path,
                    "dpi": target_dpi,
                    "need_render": need_render,
                    "ocr_cfg": cfg.ocr.model_dump(),
                }
            )

    results: list[dict[str, Any]] = []
    if tasks:
        if safe_workers == 1 or len(tasks) == 1:
            local_ocr = create_ocr_engine(cfg.ocr)
            for task in tasks:
                page_idx = int(task["page_idx"])
                img_path = Path(task["img_path"])
                try:
                    if task["need_render"]:
                        img_path.parent.mkdir(parents=True, exist_ok=True)
                        with fitz.open(task["pdf_path"]) as pdf_doc:
                            page = pdf_doc.load_page(page_idx)
                            pix = page.get_pixmap(dpi=int(task["dpi"]))
                            pix.save(str(img_path))
                    blocks_dicts, raw_text, img_w, img_h, elapse = _run_ocr_on_image_with_engine(
                        img_path=img_path,
                        page_index=page_idx,
                        ocr_engine=local_ocr,
                    )
                    results.append(
                        {
                            "ok": True,
                            "doc_id": task["doc_id"],
                            "page_idx": page_idx,
                            "dpi": int(task["dpi"]),
                            "img_path": str(img_path),
                            "img_width": img_w,
                            "img_height": img_h,
                            "raw_text": raw_text,
                            "blocks_dicts": blocks_dicts,
                            "elapse": elapse if isinstance(elapse, list) else None,
                            "error": None,
                        }
                    )
                except Exception as exc:
                    results.append(
                        {
                            "ok": False,
                            "doc_id": task["doc_id"],
                            "page_idx": page_idx,
                            "dpi": int(task["dpi"]),
                            "img_path": str(img_path),
                            "error": f"{type(exc).__name__}: {exc}",
                        }
                    )
        else:
            with concurrent.futures.ProcessPoolExecutor(
                max_workers=safe_workers,
                initializer=_worker_init,
                initargs=(cfg.ocr.model_dump(),),
            ) as executor:
                future_to_task = {
                    executor.submit(_ocr_single_page_worker, task): task for task in tasks
                }
                for future in concurrent.futures.as_completed(future_to_task):
                    task = future_to_task[future]
                    try:
                        results.append(future.result())
                    except Exception as exc:
                        results.append(
                            {
                                "ok": False,
                                "doc_id": task["doc_id"],
                                "page_idx": int(task["page_idx"]),
                                "dpi": int(task["dpi"]),
                                "img_path": task["img_path"],
                                "error": f"{type(exc).__name__}: {exc}",
                            }
                        )

    pages_ocr_completed = 0
    pages_failed = 0
    failed_by_doc: dict[str, set[int]] = {}
    succeeded_by_doc: dict[str, set[int]] = {}

    for res in results:
        d_id = res["doc_id"]
        p_idx = int(res["page_idx"])
        art = artifacts_by_doc[d_id][p_idx]

        if res["ok"]:
            cache_payload = {
                "document_id": d_id,
                "page_index": p_idx,
                "dpi": res["dpi"],
                "image_width": res["img_width"],
                "image_height": res["img_height"],
                "raw_text": res["raw_text"],
                "blocks": res["blocks_dicts"],
                "elapse": res.get("elapse"),
                "img_path": res["img_path"],
            }
            atomic_write_json(ws.page_ocr_path(d_id, p_idx), cache_payload)
            _apply_ocr_payload_to_artifact(art, cache_payload, cfg)
            ws.save_page_artifact(art)
            pages_ocr_completed += 1
            succeeded_by_doc.setdefault(d_id, set()).add(p_idx)
        else:
            err_msg = str(res["error"] or "Unknown OCR failure")
            art.error = err_msg
            ws.save_page_artifact(art)
            pages_failed += 1
            failed_by_doc.setdefault(d_id, set()).add(p_idx)
            failed_details.append(
                {"document_id": d_id, "page_index": p_idx, "error": err_msg}
            )
            logger.error("OCR failed on %s page %d: %s", d_id, p_idx, err_msg)

    for doc in target_docs:
        if doc.document_id not in artifacts_by_doc:
            continue
        existing_failed = set(doc.pages_failed)
        existing_failed -= succeeded_by_doc.get(doc.document_id, set())
        existing_failed |= failed_by_doc.get(doc.document_id, set())
        doc.pages_failed = sorted(existing_failed)
        doc.updated_at = utc_now_iso()
        if doc.pages_failed:
            if doc.page_count > 0 and len(doc.pages_failed) >= doc.page_count:
                doc.status = TaskStatus.FAILED
            else:
                doc.status = TaskStatus.REVIEW_REQUIRED
            doc.error = f"Failed pages during OCR: {doc.pages_failed}"
        elif doc.status != TaskStatus.FAILED:
            doc.status = TaskStatus.COMPLETED
            doc.error = None

    ws.save_manifest(manifest)

    return {
        "documents_processed": len(artifacts_by_doc),
        "pages_total_needing_ocr": pages_total_needing_ocr,
        "pages_ocr_completed": pages_ocr_completed,
        "pages_skipped_cached": pages_skipped_cached,
        "pages_failed": pages_failed,
        "workers_used": safe_workers if tasks else 0,
        "failed_details": failed_details,
    }
