"""End-to-end orchestration pipeline and environment diagnostics for vet-case-extractor."""

from __future__ import annotations

import importlib
import importlib.metadata
import os
import sys
from pathlib import Path
from typing import Any, Literal

import psutil

from vet_case_extractor import __version__
from vet_case_extractor.config import AppConfig, find_config_dir, load_config
from vet_case_extractor.export.bundle import export_project_bundle
from vet_case_extractor.extraction.extractor import CaseExtractor
from vet_case_extractor.ingestion.project import ProjectWorkspace
from vet_case_extractor.models import (
    CaseRecord,
    CaseSegment,
    IndexSummary,
    LLMExtractedPayload,
    OCRPolicy,
    PageArtifact,
    SourceType,
    TaskStatus,
)
from vet_case_extractor.normalization.fields import normalize_case_fields
from vet_case_extractor.ocr.engine import determine_safe_workers, run_ocr_for_project
from vet_case_extractor.pdf.reader import ingest_document
from vet_case_extractor.provenance.tracker import ground_case_provenance
from vet_case_extractor.providers import (
    ProviderConfigError,
    export_agent_batch,
    get_provider,
    parse_agent_import_file,
)
from vet_case_extractor.segmentation.indexer import build_project_index
from vet_case_extractor.utils.fonts import probe_cjk_fonts
from vet_case_extractor.utils.logging import setup_logging
from vet_case_extractor.validation.qa import run_project_qa


class ConfigUsageError(ValueError):
    """Raised on invalid CLI/config arguments, missing paths, or missing API configuration."""


class QAFailureError(RuntimeError):
    """Raised when project QA finishes with overall_status == FAIL (or strict review failure)."""


_DOCTOR_PACKAGES: tuple[tuple[str, str, str], ...] = (
    ("pymupdf", "pymupdf", "PyMuPDF"),
    ("pdfplumber", "pdfplumber", "pdfplumber"),
    ("rapidocr_onnxruntime", "rapidocr-onnxruntime", "RapidOCR"),
    ("onnxruntime", "onnxruntime", "ONNX Runtime"),
    ("openpyxl", "openpyxl", "openpyxl"),
    ("pydantic", "pydantic", "Pydantic"),
    ("google.genai", "google-genai", "Google GenAI SDK"),
    ("httpx", "httpx", "HTTPX"),
    ("click", "click", "Click"),
    ("mcp", "mcp", "MCP SDK"),
)


def run_doctor(cfg: AppConfig | None = None) -> dict[str, Any]:
    """Inspect Python runtime, packages, API key presence (masked), hardware, and Chinese fonts."""
    resolved_cfg = cfg or load_config()
    in_venv = sys.prefix != getattr(sys, "base_prefix", sys.prefix)

    packages: dict[str, dict[str, Any]] = {}
    for import_name, dist_name, label in _DOCTOR_PACKAGES:
        available = False
        version: str | None = None
        err: str | None = None
        try:
            importlib.import_module(import_name)
            available = True
            try:
                version = importlib.metadata.version(dist_name)
            except importlib.metadata.PackageNotFoundError:
                version = "installed"
        except Exception as exc:
            err = f"{type(exc).__name__}: {exc}"
        packages[dist_name] = {
            "label": label,
            "available": available,
            "version": version,
            "error": err,
        }

    vm = psutil.virtual_memory()
    total_ram_gb = round(vm.total / (1024**3), 2)
    avail_ram_gb = round(vm.available / (1024**3), 2)
    cpu_count = os.cpu_count() or 1
    recommended_workers = determine_safe_workers(resolved_cfg.ocr)

    # Never reveal or print raw API keys
    api_keys = {
        "GEMINI_API_KEY": bool(os.environ.get("GEMINI_API_KEY", "").strip()),
        "OPENAI_API_KEY": bool(os.environ.get("OPENAI_API_KEY", "").strip()),
        "OPENAI_BASE_URL": bool(os.environ.get("OPENAI_BASE_URL", "").strip()),
    }

    font_probe = probe_cjk_fonts()
    fonts = font_probe["legacy_map"]
    config_dir = find_config_dir()

    return {
        "tool_version": __version__,
        "python_version": sys.version.split()[0],
        "python_executable": sys.executable,
        "in_virtualenv": in_venv,
        "config_dir": str(config_dir) if config_dir else None,
        "rule_version": resolved_cfg.rule_version,
        "packages": packages,
        "api_keys_present": api_keys,
        "hardware": {
            "cpu_count": cpu_count,
            "total_ram_gb": total_ram_gb,
            "available_ram_gb": avail_ram_gb,
            "recommended_ocr_workers": recommended_workers,
            "configured_ocr_workers": resolved_cfg.ocr.workers,
        },
        "chinese_fonts": fonts,
        "chinese_font_probe": {
            "current_platform": font_probe["current_platform"],
            "status": font_probe["status"],
            "selected_font": font_probe["selected_font"],
            "candidates": font_probe["candidates"],
            "note": font_probe["note"],
        },
        "any_chinese_font_available": font_probe["status"] == "available",
    }


def _load_workspace(project_dir: str | Path) -> ProjectWorkspace:
    ws = ProjectWorkspace(project_dir)
    if not ws.manifest_path.exists():
        raise ConfigUsageError(
            f"项目目录未初始化或缺少 manifest.json: {ws.project_dir}。请先运行 `vetcase ingest`。"
        )
    return ws


def ingest_project(
    project_dir: str | Path,
    input_path: str | Path,
    *,
    mode: SourceType = SourceType.AUTO,
    page_offset: int = 0,
    target_article_query: str | None = None,
    ocr_policy: OCRPolicy = OCRPolicy.AUTO,
    render_all_pages: bool | None = None,
    retry_failed: bool = False,
    cfg: AppConfig | None = None,
) -> dict[str, Any]:
    """Register PDFs into the workspace and run native PyMuPDF/pdfplumber page ingestion."""
    resolved_cfg = cfg or load_config()
    ws = ProjectWorkspace(project_dir)
    logger = setup_logging(ws.logs_dir / "vetcase.log")

    try:
        manifest, added_doc_ids, skipped_duplicate_doc_ids = ws.register_pdfs(
            input_path=input_path,
            source_type=mode,
            page_offset=page_offset,
            target_article_query=target_article_query,
        )
    except (FileNotFoundError, ValueError) as exc:
        raise ConfigUsageError(str(exc)) from exc

    added_set = set(added_doc_ids)
    processed_doc_ids: list[str] = []
    skipped_completed_doc_ids: list[str] = []
    failed_doc_ids: list[str] = []
    pages_needing_ocr = 0

    for doc_id, doc_rec in list(manifest.documents.items()):
        existing_pages = ws.load_page_artifacts(doc_id)
        should_process = (
            doc_id in added_set
            or doc_rec.status == TaskStatus.PENDING
            or not existing_pages
            or (
                retry_failed
                and doc_rec.status in (TaskStatus.FAILED, TaskStatus.REVIEW_REQUIRED)
            )
        )
        if not should_process:
            skipped_completed_doc_ids.append(doc_id)
            pages_needing_ocr += len(doc_rec.pages_needing_ocr)
            continue

        updated_doc, _ = ingest_document(
            doc_rec=doc_rec,
            ws=ws,
            cfg=resolved_cfg,
            ocr_policy=ocr_policy,
            render_all_pages=render_all_pages,
        )
        manifest.documents[doc_id] = updated_doc
        ws.save_manifest(manifest)

        if updated_doc.status == TaskStatus.FAILED:
            failed_doc_ids.append(doc_id)
        else:
            processed_doc_ids.append(doc_id)
        pages_needing_ocr += len(updated_doc.pages_needing_ocr)

    logger.info(
        "Ingest complete: added=%d duplicates=%d processed=%d skipped=%d failed=%d",
        len(added_doc_ids),
        len(skipped_duplicate_doc_ids),
        len(processed_doc_ids),
        len(skipped_completed_doc_ids),
        len(failed_doc_ids),
    )
    return {
        "project_dir": str(ws.project_dir),
        "total_documents": len(manifest.documents),
        "added_doc_ids": added_doc_ids,
        "skipped_duplicate_doc_ids": skipped_duplicate_doc_ids,
        "processed_doc_ids": processed_doc_ids,
        "skipped_completed_doc_ids": skipped_completed_doc_ids,
        "failed_doc_ids": failed_doc_ids,
        "pages_needing_ocr": pages_needing_ocr,
    }


def ocr_project(
    project_dir: str | Path,
    *,
    doc_id: str | None = None,
    dpi: int | None = None,
    workers: int | None = None,
    force: bool = False,
    retry_failed: bool = False,
    cfg: AppConfig | None = None,
) -> dict[str, Any]:
    """Run RapidOCR on pages marked `needs_ocr`, honoring per-page JSON cache."""
    resolved_cfg = cfg or load_config()
    ws = _load_workspace(project_dir)
    setup_logging(ws.logs_dir / "vetcase.log")

    manifest = ws.load_manifest()
    if doc_id is not None and doc_id not in manifest.documents:
        raise ConfigUsageError(f"文档 ID `{doc_id}` 不存在于 manifest.json 中。")

    if retry_failed and not force:
        # Remove corrupted/stale page_ocr cache only for failed pages so they are re-run
        for doc in manifest.documents.values():
            if doc_id is not None and doc.document_id != doc_id:
                continue
            for p_idx in doc.pages_failed:
                cache_path = ws.page_ocr_path(doc.document_id, p_idx)
                if cache_path.exists():
                    cache_path.unlink(missing_ok=True)

    return run_ocr_for_project(
        ws=ws,
        cfg=resolved_cfg,
        doc_id=doc_id,
        dpi=dpi,
        workers=workers,
        force=force,
    )


def index_project(
    project_dir: str | Path,
    *,
    mode: SourceType = SourceType.AUTO,
    consult_provider: str | None = None,
    model: str | None = None,
    cfg: AppConfig | None = None,
) -> IndexSummary:
    """Segment documents into cases, write packet.txt files, and persist index.json."""
    resolved_cfg = cfg or load_config()
    ws = _load_workspace(project_dir)
    logger = setup_logging(ws.logs_dir / "vetcase.log")

    manifest = ws.load_manifest()
    documents = list(manifest.documents.values())
    pages_by_doc = {
        doc.document_id: ws.load_page_artifacts(doc.document_id) for doc in documents
    }

    provider = None
    if consult_provider and consult_provider.lower() != "none":
        try:
            provider = get_provider(consult_provider, resolved_cfg, model=model)
        except ProviderConfigError as exc:
            raise ConfigUsageError(str(exc)) from exc

    index = build_project_index(
        documents=documents,
        pages_by_doc=pages_by_doc,
        cases_dir=ws.cases_dir,
        cfg=resolved_cfg,
        source_type_override=mode,
        provider=provider,
    )
    ws.save_index(index)

    for seg in index.cases:
        manifest.case_statuses.setdefault(seg.case_id, TaskStatus.PENDING)
    ws.save_manifest(manifest)

    logger.info(
        "Index complete: total_documents=%d total_cases=%d",
        index.total_documents,
        index.total_cases,
    )
    return index


def extract_project(
    project_dir: str | Path,
    *,
    provider_name: str | None = None,
    model: str | None = None,
    workers: int | None = None,
    case_ids: list[str] | None = None,
    retry_failed: bool = False,
    force: bool = False,
    cfg: AppConfig | None = None,
) -> dict[str, Any]:
    """Run LLM extraction on pending/failed cases with incremental persistence."""
    resolved_cfg = cfg or load_config()
    ws = _load_workspace(project_dir)
    logger = setup_logging(ws.logs_dir / "vetcase.log")

    manifest = ws.load_manifest()
    index = ws.load_index()
    if index is None:
        raise ConfigUsageError("尚未生成 index.json，请先运行 `vetcase index`。")

    resolved_provider = (provider_name or resolved_cfg.llm.provider).strip().lower()
    if resolved_provider == "none":
        raise ConfigUsageError(
            "provider='none' 不能直接调用 API 抽取；请指定 `gemini` / `openai_compat`，"
            "或运行 `vetcase prepare-agent-batch` + `vetcase import-cases`。"
        )

    seg_by_id = {seg.case_id: seg for seg in index.cases}
    if case_ids:
        unknown = [cid for cid in case_ids if cid not in seg_by_id]
        if unknown:
            raise ConfigUsageError(f"指定的 case_id 不存在于索引中: {unknown}")
        candidate_segments = [seg_by_id[cid] for cid in case_ids]
    else:
        candidate_segments = list(index.cases)

    existing_records = {rec.case_id: rec for rec in ws.load_all_case_records()}
    to_extract: list[CaseSegment] = []
    skipped_completed = 0
    skipped_failed = 0

    for seg in candidate_segments:
        existing = existing_records.get(seg.case_id)
        if existing is None:
            to_extract.append(seg)
            continue
        if force:
            to_extract.append(seg)
        elif existing.status in (TaskStatus.COMPLETED, TaskStatus.REVIEW_REQUIRED):
            skipped_completed += 1
        elif existing.status == TaskStatus.FAILED:
            if retry_failed:
                to_extract.append(seg)
            else:
                skipped_failed += 1
        else:
            to_extract.append(seg)

    if not to_extract:
        logger.info(
            "No cases require extraction (skipped_completed=%d, skipped_failed=%d).",
            skipped_completed,
            skipped_failed,
        )
        return {
            "project_dir": str(ws.project_dir),
            "provider": resolved_provider,
            "total_candidates": len(candidate_segments),
            "attempted": 0,
            "completed": 0,
            "review_required": 0,
            "failed": 0,
            "skipped_completed": skipped_completed,
            "skipped_failed": skipped_failed,
            "token_usage_totals": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        }

    try:
        provider = get_provider(resolved_provider, resolved_cfg, model=model)
    except (ProviderConfigError, ValueError) as exc:
        raise ConfigUsageError(str(exc)) from exc

    doc_ids_needed = {seg.document_id for seg in to_extract}
    pages_by_doc: dict[str, list[PageArtifact]] = {
        doc_id: ws.load_page_artifacts(doc_id) for doc_id in doc_ids_needed
    }
    sha256_by_doc: dict[str, str] = {
        doc_id: manifest.documents[doc_id].file_sha256
        for doc_id in doc_ids_needed
        if doc_id in manifest.documents
    }

    extractor = CaseExtractor(provider=provider, cfg=resolved_cfg, logger=logger)

    def _on_case_done(record: CaseRecord) -> None:
        ws.save_case_record(record)
        manifest.case_statuses[record.case_id] = record.status
        ws.save_manifest(manifest)

    results = extractor.extract_batch(
        segments=to_extract,
        pages_by_doc=pages_by_doc,
        sha256_by_doc=sha256_by_doc,
        on_case_done=_on_case_done,
        max_workers=workers,
    )

    completed_n = sum(1 for r in results if r.status == TaskStatus.COMPLETED)
    review_n = sum(1 for r in results if r.status == TaskStatus.REVIEW_REQUIRED)
    failed_n = sum(1 for r in results if r.status == TaskStatus.FAILED)
    token_totals = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    for r in results:
        for k in token_totals:
            token_totals[k] += int(r.token_usage.get(k, 0) or 0)

    return {
        "project_dir": str(ws.project_dir),
        "provider": resolved_provider,
        "model": provider.model_name,
        "total_candidates": len(candidate_segments),
        "attempted": len(results),
        "completed": completed_n,
        "review_required": review_n,
        "failed": failed_n,
        "skipped_completed": skipped_completed,
        "skipped_failed": skipped_failed,
        "token_usage_totals": token_totals,
    }


def prepare_agent_batch_for_project(
    project_dir: str | Path,
    *,
    output_dir: str | Path | None = None,
    batch_size: int | None = None,
    include_review: bool = False,
    cfg: AppConfig | None = None,
) -> Path:
    """Export EXTRACTION_SPEC.md and pending batch JSON manifests for interactive Agent extraction."""
    resolved_cfg = cfg or load_config()
    ws = _load_workspace(project_dir)
    index = ws.load_index()
    if index is None:
        raise ConfigUsageError("尚未生成 index.json，请先运行 `vetcase index`。")

    records = ws.load_all_case_records()
    completed_ids = {
        rec.case_id
        for rec in records
        if rec.status == TaskStatus.COMPLETED
        or (not include_review and rec.status == TaskStatus.REVIEW_REQUIRED)
    }

    target_dir = Path(output_dir) if output_dir is not None else (ws.project_dir / "agent_batches")
    resolved_batch_size = batch_size if batch_size is not None else resolved_cfg.segmentation.batch_size
    try:
        return export_agent_batch(
            index=index,
            completed_case_ids=completed_ids,
            output_dir=target_dir,
            rules=resolved_cfg.rules,
            batch_size=resolved_batch_size,
        )
    except ValueError as exc:
        raise ConfigUsageError(str(exc)) from exc


def import_agent_cases(
    project_dir: str | Path,
    json_path: str | Path,
    *,
    method: str = "agent_interactive",
    model_name: str = "unspecified-agent",
    agent_platform: str = "unspecified",
    agent_version: str = "",
    cfg: AppConfig | None = None,
) -> dict[str, Any]:
    """Validate, normalize, provenance-ground, and persist agent-extracted JSON records."""
    resolved_cfg = cfg or load_config()
    ws = _load_workspace(project_dir)
    logger = setup_logging(ws.logs_dir / "vetcase.log")

    manifest = ws.load_manifest()
    index = ws.load_index()
    if index is None:
        raise ConfigUsageError("尚未生成 index.json，请先运行 `vetcase index`。")

    try:
        payloads: list[LLMExtractedPayload] = parse_agent_import_file(Path(json_path))
    except FileNotFoundError as exc:
        raise ConfigUsageError(str(exc)) from exc

    seg_by_id = {seg.case_id: seg for seg in index.cases}
    segs_by_title: dict[str, list[CaseSegment]] = {}
    for seg in index.cases:
        segs_by_title.setdefault(seg.title.strip(), []).append(seg)

    pages_cache: dict[str, list[PageArtifact]] = {}
    imported_ids: list[str] = []
    completed_n = 0
    review_n = 0
    failed_n = 0

    for idx, payload in enumerate(payloads):
        seg: CaseSegment | None = None
        cid = payload.case_id.strip()
        if cid:
            seg = seg_by_id.get(cid)
            if seg is None:
                raise ConfigUsageError(
                    f"导入条目 #{idx} 的 case_id `{cid}` 不存在于 index.json 中。"
                )
        else:
            title_matches = segs_by_title.get(payload.title.strip(), [])
            if len(title_matches) == 1:
                seg = title_matches[0]
            else:
                raise ConfigUsageError(
                    f"导入条目 #{idx} 缺少 case_id 且无法通过 title `{payload.title}` 唯一匹配。"
                )

        if seg.document_id not in pages_cache:
            pages_cache[seg.document_id] = ws.load_page_artifacts(seg.document_id)
        doc_rec = manifest.documents.get(seg.document_id)
        sha256 = doc_rec.file_sha256 if doc_rec else ""

        record = normalize_case_fields(
            payload=payload,
            segment=seg,
            rules=resolved_cfg.rules,
            method=method,
            model_name=model_name,
            rule_version=resolved_cfg.rules.rule_version,
            file_sha256=sha256,
        )
        record = ground_case_provenance(
            record=record,
            pages=pages_cache[seg.document_id],
            extraction_method=method,
            model_name=model_name,
            rule_version=resolved_cfg.rules.rule_version,
            evidence_quotes=payload.evidence_quotes,
        )
        record.agent_platform = agent_platform
        record.agent_version = agent_version
        for span in record.evidence_spans:
            span.agent_platform = agent_platform
        ws.save_case_record(record)
        manifest.case_statuses[record.case_id] = record.status
        imported_ids.append(record.case_id)

        if record.status == TaskStatus.COMPLETED:
            completed_n += 1
        elif record.status == TaskStatus.REVIEW_REQUIRED:
            review_n += 1
        elif record.status == TaskStatus.FAILED:
            failed_n += 1

    ws.save_manifest(manifest)
    logger.info(
        "Imported %d case(s) via method=%s (completed=%d review_required=%d failed=%d)",
        len(imported_ids),
        method,
        completed_n,
        review_n,
        failed_n,
    )
    return {
        "project_dir": str(ws.project_dir),
        "method": method,
        "model_name": model_name,
        "agent_platform": agent_platform,
        "agent_version": agent_version,
        "imported": len(imported_ids),
        "completed": completed_n,
        "review_required": review_n,
        "failed": failed_n,
        "case_ids": imported_ids,
    }


def get_project_status(project_dir: str | Path) -> dict[str, Any]:
    """Inspect workspace state across ingestion, OCR, index, extraction, QA, and export."""
    ws = _load_workspace(project_dir)
    manifest = ws.load_manifest()
    index = ws.load_index()
    records = ws.load_all_case_records()

    doc_status_counts: dict[str, int] = {}
    pages_needing_ocr = 0
    pages_failed = 0
    for doc in manifest.documents.values():
        key = doc.status.value
        doc_status_counts[key] = doc_status_counts.get(key, 0) + 1
        pages_needing_ocr += len(doc.pages_needing_ocr)
        pages_failed += len(doc.pages_failed)

    case_status_counts: dict[str, int] = {}
    for rec in records:
        key = rec.status.value
        case_status_counts[key] = case_status_counts.get(key, 0) + 1

    qa_report_path = ws.qa_dir / "qa_report.json"
    qa_status: str | None = None
    if qa_report_path.exists():
        try:
            from vet_case_extractor.models import QAReport

            qa_status = QAReport.model_validate_json(
                qa_report_path.read_text(encoding="utf-8")
            ).overall_status
        except Exception:
            qa_status = "CORRUPTED"

    export_files = (
        sorted(p.name for p in ws.export_dir.iterdir() if p.is_file())
        if ws.export_dir.is_dir()
        else []
    )

    return {
        "project_dir": str(ws.project_dir),
        "source_type": manifest.source_type.value,
        "total_documents": len(manifest.documents),
        "documents_by_status": doc_status_counts,
        "pages_needing_ocr": pages_needing_ocr,
        "pages_failed": pages_failed,
        "index_exists": index is not None,
        "indexed_cases": index.total_cases if index is not None else 0,
        "chapter_continuity_issues": index.chapter_continuity_issues if index is not None else [],
        "extracted_records": len(records),
        "cases_by_status": case_status_counts,
        "qa_report_exists": qa_report_path.exists(),
        "qa_overall_status": qa_status,
        "export_files": export_files,
    }


def run_full_pipeline(
    project_dir: str | Path,
    input_path: str | Path,
    *,
    mode: SourceType = SourceType.AUTO,
    provider: str = "none",
    model: str | None = None,
    export_format: Literal["auto", "journal", "book"] = "auto",
    layer: Literal["normalized", "raw"] = "normalized",
    output_dir: str | Path | None = None,
    excel_filename: str | None = None,
    page_offset: int = 0,
    target_article_query: str | None = None,
    ocr_policy: OCRPolicy = OCRPolicy.AUTO,
    render_all_pages: bool | None = None,
    ocr_workers: int | None = None,
    extract_workers: int | None = None,
    retry_failed: bool = False,
    cfg: AppConfig | None = None,
) -> dict[str, Any]:
    """Run ingest -> OCR -> index -> (optional extract -> QA -> export)."""
    resolved_cfg = cfg or load_config()
    resolved_provider = provider.strip().lower()
    if resolved_provider not in ("gemini", "openai_compat", "none"):
        raise ConfigUsageError(
            f"Unsupported provider '{provider}': expected gemini, openai_compat, or none."
        )

    ingest_res = ingest_project(
        project_dir=project_dir,
        input_path=input_path,
        mode=mode,
        page_offset=page_offset,
        target_article_query=target_article_query,
        ocr_policy=ocr_policy,
        render_all_pages=render_all_pages,
        retry_failed=retry_failed,
        cfg=resolved_cfg,
    )
    ocr_res = ocr_project(
        project_dir=project_dir,
        workers=ocr_workers,
        retry_failed=retry_failed,
        cfg=resolved_cfg,
    )
    index_res = index_project(
        project_dir=project_dir,
        mode=mode,
        consult_provider=None if resolved_provider == "none" else resolved_provider,
        model=model,
        cfg=resolved_cfg,
    )

    if resolved_provider == "none":
        return {
            "project_dir": str(Path(project_dir).resolve()),
            "stopped_after": "index",
            "provider": "none",
            "ingest": ingest_res,
            "ocr": ocr_res,
            "index": {
                "total_documents": index_res.total_documents,
                "total_cases": index_res.total_cases,
                "chapter_continuity_issues": index_res.chapter_continuity_issues,
            },
            "next_steps": (
                "Stopped cleanly after indexing because --provider=none. "
                "Next: run `vetcase extract --project <dir> --provider gemini|openai_compat` "
                "OR `vetcase prepare-agent-batch --project <dir>` followed by `vetcase import-cases`."
            ),
        }

    extract_res = extract_project(
        project_dir=project_dir,
        provider_name=resolved_provider,
        model=model,
        workers=extract_workers,
        retry_failed=retry_failed,
        cfg=resolved_cfg,
    )
    qa_report = run_project_qa(Path(project_dir), cfg=resolved_cfg)
    bundle_paths = export_project_bundle(
        project_dir=project_dir,
        output_dir=output_dir,
        excel_filename=excel_filename,
        format_mode=export_format,
        layer=layer,
        cfg=resolved_cfg,
    )
    return {
        "project_dir": str(Path(project_dir).resolve()),
        "stopped_after": "export",
        "provider": resolved_provider,
        "ingest": ingest_res,
        "ocr": ocr_res,
        "index": {
            "total_documents": index_res.total_documents,
            "total_cases": index_res.total_cases,
            "chapter_continuity_issues": index_res.chapter_continuity_issues,
        },
        "extract": extract_res,
        "qa_overall_status": qa_report.overall_status,
        "exports": {k: str(v) for k, v in bundle_paths.items()},
    }
