"""Project-wide case segmentation indexer, optional LLM boundary check, and packet generator."""

from __future__ import annotations

import logging
import re
from pathlib import Path

from pydantic import BaseModel

from vet_case_extractor.config import AppConfig
from vet_case_extractor.models import (
    CaseSegment,
    DocumentRecord,
    IndexSummary,
    PageArtifact,
    SourceType,
    TaskStatus,
)
from vet_case_extractor.providers.base import LLMProvider, ProviderError
from vet_case_extractor.segmentation.book import segment_book_document
from vet_case_extractor.segmentation.journal import segment_journal_document
from vet_case_extractor.utils.io import atomic_write_text

_LOGGER = logging.getLogger("vet_case_extractor.segmentation.indexer")

_BOOK_CASE_SIGNAL_RE = re.compile(
    r"^(?:\[\[(?:通栏|左栏|右栏)\]\]\s*)?病例\s*\d+\s*[一\-—－~、·.]*\s*等级\s*(?:\d+|[一二三四五])$"
)
_BOOK_CHAPTER_SIGNAL_RE = re.compile(
    r"^(?:\[\[(?:通栏|左栏|右栏)\]\]\s*)?第[一二三四五六七八九十百零\d]+章\b"
)
_JOURNAL_SIGNAL_RE = re.compile(
    r"(?:中国兽医杂志|畜牧与兽医|黑龙江畜牧兽医|收稿日期|中图分类号|文献标志码|文章编号|Chinese\s*Journal\s*of\s*Veterinary)"
)


class _BoundaryReviewResponse(BaseModel):
    """Minimal schema returned when LLMProvider inspects an uncertain segment boundary."""

    confirmed_title: str = ""
    confirmed_case_level: int | None = None
    is_valid_case_boundary: bool = True
    boundary_note: str = ""


def _detect_source_type(
    doc: DocumentRecord,
    pages: list[PageArtifact],
    cfg: AppConfig,
) -> SourceType:
    """Heuristically distinguish scanned/structured book vs journal article."""
    book_case_hits = 0
    book_chapter_hits = 0
    book_summary_hits = 0
    journal_hits = 0

    for page in pages[:12]:
        text = page.layout_text or page.cleaned_text or page.raw_text
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if _BOOK_CASE_SIGNAL_RE.match(line):
                book_case_hits += 1
            elif _BOOK_CHAPTER_SIGNAL_RE.match(line):
                book_chapter_hits += 1
        if any(h in text for h in cfg.rules.book_summary_headings):
            book_summary_hits += 1
        if _JOURNAL_SIGNAL_RE.search(text):
            journal_hits += 1

    if book_case_hits >= 1:
        return SourceType.BOOK
    if book_chapter_hits >= 1 and book_summary_hits >= 1 and journal_hits == 0:
        return SourceType.BOOK
    if doc.page_offset > 0 and journal_hits == 0:
        return SourceType.BOOK
    return SourceType.JOURNAL


def _consult_llm_boundary(
    segment: CaseSegment,
    provider: LLMProvider,
) -> None:
    """Optionally consult LLMProvider to annotate an uncertain boundary (non-destructive)."""
    excerpt = segment.raw_text[:2000]
    if not excerpt.strip():
        return
    system_prompt = (
        "你是中文兽医文献病例边界复核助手。"
        "请根据给出的病例开头与片段文本，判断边界或标题/等级是否明确，不得编造信息。"
    )
    user_prompt = (
        f"当前规则初判标题: {segment.title}\n"
        f"来源文件: {segment.source_filename} ({segment.source_type.value})\n"
        f"不确定原因: {'; '.join(segment.boundary_notes)}\n\n"
        f"文本片段:\n{excerpt}"
    )
    try:
        resp = provider.extract_structured(system_prompt, user_prompt, _BoundaryReviewResponse)
    except ProviderError as exc:
        _LOGGER.warning("LLM boundary consultation skipped for %s: %s", segment.case_id, exc)
        segment.boundary_notes.append(f"[LLM边界辅助失败] {exc}")
        return

    parsed = resp.parsed
    note_parts: list[str] = []
    if parsed.boundary_note.strip():
        note_parts.append(parsed.boundary_note.strip())
    if parsed.confirmed_title.strip():
        note_parts.append(f"建议标题={parsed.confirmed_title.strip()}")
    if parsed.confirmed_case_level is not None:
        note_parts.append(f"建议等级={parsed.confirmed_case_level}")
        if (
            segment.source_type == SourceType.BOOK
            and (segment.case_level is None or segment.case_level < 1 or segment.case_level > 5)
            and 1 <= parsed.confirmed_case_level <= 5
        ):
            segment.case_level = parsed.confirmed_case_level
            if segment.chapter_title and segment.case_number is not None:
                segment.title = (
                    f"{segment.chapter_title} · 病例{segment.case_number} 等级{parsed.confirmed_case_level}"
                )
    if note_parts:
        segment.boundary_notes.append(f"[LLM边界辅助({resp.model_name})] " + "；".join(note_parts))


def _render_packet_text(segment: CaseSegment, doc_pages: list[PageArtifact]) -> str:
    """Format a self-contained case packet file with metadata, page text, markers, images, and lab rows."""
    page_map = {p.page_index: p for p in doc_pages}
    header_lines = [
        "=" * 64,
        f"CASE_ID: {segment.case_id}",
        f"DOCUMENT_ID: {segment.document_id}",
        f"SOURCE_FILENAME: {segment.source_filename}",
        f"SOURCE_TYPE: {segment.source_type.value}",
        f"SOURCE_JOURNAL: {segment.source_journal}",
        f"PUBLICATION_YEAR: {segment.publication_year}",
        f"RECEIVED_YEAR: {segment.received_year}",
        f"TITLE: {segment.title}",
        f"CHAPTER_TITLE: {segment.chapter_title}",
        f"CASE_NUMBER: {segment.case_number if segment.case_number is not None else ''}",
        f"CASE_LEVEL: {segment.case_level_raw or (segment.case_level if segment.case_level is not None else '')}",
        f"PAGE_RANGE: {segment.page_range}",
        f"BOOK_PAGE_RANGE: {segment.book_page_range}",
        f"BOUNDARY_UNCERTAIN: {segment.boundary_uncertain}",
        f"BOUNDARY_NOTES: {'; '.join(segment.boundary_notes)}",
        "IMAGE_PATHS:",
        *[f"  - {img}" for img in segment.image_paths],
        "=" * 64,
        "",
    ]

    # Build per-page image & book-page annotations alongside raw_text sections
    body_text = segment.raw_text
    if not body_text.strip() and segment.page_indices:
        parts: list[str] = []
        for idx in segment.page_indices:
            p = page_map.get(idx)
            if p is None:
                continue
            p_text = p.layout_text or p.cleaned_text or p.raw_text
            parts.append(f"--- PDF第{p.pdf_page_number}页 ---\n{p_text}")
        body_text = "\n\n".join(parts)

    # Inject page metadata lines right after `--- PDF第N页 ---` markers for readability
    enriched_lines: list[str] = []
    page_by_pdf_num = {p.pdf_page_number: p for p in doc_pages}
    for line in body_text.splitlines():
        enriched_lines.append(line)
        m = re.match(r"^---\s*PDF第(\d+)页\s*---$", line.strip())
        if m:
            pdf_num = int(m.group(1))
            page_obj = page_by_pdf_num.get(pdf_num)
            if page_obj is not None:
                if page_obj.book_page_number is not None:
                    enriched_lines.append(f"[书页码: 第{page_obj.book_page_number}页]")
                if page_obj.image_path:
                    enriched_lines.append(f"[页图路径: {page_obj.image_path}]")

    lab_lines: list[str] = []
    if segment.lab_rows:
        lab_lines.append("")
        lab_lines.append("[检验数值表提取结果（原始值，禁止自动篡改）]")
        for row in segment.lab_rows:
            anomaly_note = f" | anomalies={'; '.join(row.anomalies)}" if row.anomalies else ""
            title_prefix = f"[{row.table_title}] " if row.table_title else ""
            lab_lines.append(
                f"- p{row.page_index + 1} {title_prefix}{row.item_raw} | "
                f"value={row.value_raw} | unit={row.unit_raw} | ref={row.ref_range_raw} | "
                f"flag={row.flag_raw}{anomaly_note}"
            )

    return "\n".join(header_lines + enriched_lines + lab_lines).rstrip() + "\n"


def build_project_index(
    documents: list[DocumentRecord],
    pages_by_doc: dict[str, list[PageArtifact]],
    cases_dir: Path,
    cfg: AppConfig,
    source_type_override: SourceType = SourceType.AUTO,
    provider: LLMProvider | None = None,
) -> IndexSummary:
    """Segment all project documents into CaseSegments, write `packet.txt` files, and build IndexSummary."""
    cases_dir.mkdir(parents=True, exist_ok=True)
    all_segments: list[CaseSegment] = []
    continuity_issues: list[str] = []
    seen_case_ids: set[str] = set()
    duplicate_case_ids: list[str] = []

    for doc in documents:
        doc_pages = sorted(pages_by_doc.get(doc.document_id, []), key=lambda p: p.page_index)
        if doc.status == TaskStatus.FAILED and not doc_pages:
            continuity_issues.append(
                f"文档 {doc.filename} ({doc.document_id}) 状态为 failed 且无可用页面，已跳过切分。"
            )
            continue

        if source_type_override != SourceType.AUTO:
            effective_type = source_type_override
        elif doc.source_type != SourceType.AUTO:
            effective_type = doc.source_type
        else:
            effective_type = _detect_source_type(doc, doc_pages, cfg)

        if effective_type == SourceType.BOOK:
            doc_segments, doc_issues = segment_book_document(doc, doc_pages, cfg.rules)
            continuity_issues.extend(doc_issues)
        else:
            doc_segments = segment_journal_document(doc, doc_pages)

        for seg in doc_segments:
            if seg.case_id in seen_case_ids:
                duplicate_case_ids.append(seg.case_id)
                seg.case_id = f"{seg.case_id}__dup_{len(duplicate_case_ids)}"
            seen_case_ids.add(seg.case_id)

            if seg.boundary_uncertain and provider is not None:
                _consult_llm_boundary(seg, provider)

            packet_path = cases_dir / seg.case_id / "packet.txt"
            atomic_write_text(packet_path, _render_packet_text(seg, doc_pages), encoding="utf-8")
            seg.packet_path = str(packet_path)
            all_segments.append(seg)

    return IndexSummary(
        total_documents=len(documents),
        total_cases=len(all_segments),
        cases=all_segments,
        chapter_continuity_issues=continuity_issues,
        duplicate_case_ids=duplicate_case_ids,
    )
