"""Concurrent case extraction pipeline tying together LLMProvider, normalization, and provenance."""

from __future__ import annotations

import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed

from vet_case_extractor.config import AppConfig
from vet_case_extractor.extraction.chunking import chunk_case_text
from vet_case_extractor.extraction.prompts import build_case_user_prompt, build_system_prompt
from vet_case_extractor.models import (
    CaseRecord,
    CaseSegment,
    LLMExtractedPayload,
    PageArtifact,
    ReviewStatus,
    TaskStatus,
)
from vet_case_extractor.normalization.fields import normalize_case_fields
from vet_case_extractor.provenance.tracker import ground_case_provenance
from vet_case_extractor.providers.base import LLMProvider, ProviderError


def _merge_unique_text(parts: list[str], sep: str = "\n") -> str:
    seen: set[str] = set()
    kept: list[str] = []
    for part in parts:
        cleaned = part.strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        kept.append(cleaned)
    return sep.join(kept)


def _merge_chunk_payloads(
    segment: CaseSegment,
    payloads: list[LLMExtractedPayload],
) -> LLMExtractedPayload:
    """Merge multi-chunk payloads while preserving first metadata and deduplicating sentences."""
    if len(payloads) == 1:
        single = payloads[0]
        if not single.case_id:
            single.case_id = segment.case_id
        return single

    merged_quotes: dict[str, list[str]] = {}
    merged_uncertainties: list[str] = []
    for p in payloads:
        for k, vals in p.evidence_quotes.items():
            target = merged_quotes.setdefault(k, [])
            for v in vals:
                vs = str(v).strip()
                if vs and vs not in target:
                    target.append(vs)
        for u in p.uncertainties:
            us = str(u).strip()
            if us and us not in merged_uncertainties:
                merged_uncertainties.append(us)

    def _first_non_empty(attr: str) -> str:
        for p in payloads:
            val = getattr(p, attr, "").strip()
            if val:
                return val
        return ""

    return LLMExtractedPayload(
        case_id=segment.case_id,
        source=_first_non_empty("source"),
        publication_year=_first_non_empty("publication_year"),
        received_year=_first_non_empty("received_year"),
        title=_first_non_empty("title"),
        medical_history=_merge_unique_text([p.medical_history for p in payloads]),
        medical_test=_merge_unique_text([p.medical_test for p in payloads]),
        medical_treatment=_merge_unique_text([p.medical_treatment for p in payloads]),
        diagnosis_raw=_merge_unique_text([p.diagnosis_raw for p in payloads], sep="；"),
        follow_up=_merge_unique_text([p.follow_up for p in payloads]),
        notes=_merge_unique_text([p.notes for p in payloads]),
        evidence_quotes=merged_quotes,
        uncertainties=merged_uncertainties,
    )


class CaseExtractor:
    """Extracts, normalizes, and grounds CaseSegments concurrently via an LLMProvider."""

    def __init__(
        self,
        provider: LLMProvider,
        cfg: AppConfig,
        logger: logging.Logger | None = None,
    ) -> None:
        self.provider = provider
        self.cfg = cfg
        self.logger = logger or logging.getLogger("vet_case_extractor.extraction")

    def extract_case(
        self,
        segment: CaseSegment,
        pages: list[PageArtifact],
        file_sha256: str = "",
    ) -> CaseRecord:
        """Extract one CaseSegment into a normalized and provenance-grounded CaseRecord."""
        chunks = chunk_case_text(
            segment.raw_text,
            max_chars=self.cfg.llm.chunk_max_chars,
            overlap_chars=self.cfg.llm.chunk_overlap_chars,
        )
        if not chunks:
            return CaseRecord(
                document_id=segment.document_id,
                file_sha256=file_sha256,
                case_id=segment.case_id,
                case_order=segment.case_order,
                source_type=segment.source_type,
                source_title=segment.source_journal,
                publication_year=segment.publication_year,
                received_year=segment.received_year,
                page_range=segment.page_range,
                book_page_range=segment.book_page_range,
                chapter_number=segment.chapter_number,
                chapter_title=segment.chapter_title,
                case_number=segment.case_number,
                case_level=segment.case_level,
                title=segment.title,
                lab_rows=list(segment.lab_rows),
                status=TaskStatus.FAILED,
                review_status=ReviewStatus.FAILED,
                review_reasons=["Empty case text; cannot extract."],
                extraction_method=self.provider.provider_name,
                model_name=self.provider.model_name,
                rule_version=self.cfg.rules.rule_version,
                error="Empty case text",
            )

        system_prompt = build_system_prompt(self.cfg.rules, source_type=segment.source_type)
        chunk_payloads: list[LLMExtractedPayload] = []
        total_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        model_used = self.provider.model_name

        try:
            for idx, chunk in enumerate(chunks, start=1):
                user_prompt = build_case_user_prompt(
                    segment,
                    chunk,
                    chunk_index=idx,
                    total_chunks=len(chunks),
                )
                resp = self.provider.extract_structured(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    response_model=LLMExtractedPayload,
                )
                chunk_payloads.append(resp.parsed)
                if resp.model_name:
                    model_used = resp.model_name
                for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
                    total_usage[key] += int(resp.token_usage.get(key, 0) or 0)
        except (ProviderError, Exception) as exc:
            self.logger.error("Extraction failed for case %s: %s", segment.case_id, exc)
            return CaseRecord(
                document_id=segment.document_id,
                file_sha256=file_sha256,
                case_id=segment.case_id,
                case_order=segment.case_order,
                source_type=segment.source_type,
                source_title=segment.source_journal,
                publication_year=segment.publication_year,
                received_year=segment.received_year,
                page_range=segment.page_range,
                book_page_range=segment.book_page_range,
                chapter_number=segment.chapter_number,
                chapter_title=segment.chapter_title,
                case_number=segment.case_number,
                case_level=segment.case_level,
                title=segment.title,
                lab_rows=list(segment.lab_rows),
                status=TaskStatus.FAILED,
                review_status=ReviewStatus.FAILED,
                review_reasons=[f"Extraction error: {exc}"],
                extraction_method=self.provider.provider_name,
                model_name=model_used,
                rule_version=self.cfg.rules.rule_version,
                token_usage=total_usage,
                error=str(exc),
            )

        merged_payload = _merge_chunk_payloads(segment, chunk_payloads)
        record = normalize_case_fields(
            payload=merged_payload,
            segment=segment,
            rules=self.cfg.rules,
            method=self.provider.provider_name,
            model_name=model_used,
            rule_version=self.cfg.rules.rule_version,
            file_sha256=file_sha256,
        )
        record.token_usage = total_usage
        record = ground_case_provenance(
            record=record,
            pages=pages,
            extraction_method=self.provider.provider_name,
            model_name=model_used,
            rule_version=self.cfg.rules.rule_version,
            evidence_quotes=merged_payload.evidence_quotes,
        )
        self.logger.info(
            "Extracted case=%s provider=%s model=%s tokens=%s status=%s review=%s",
            record.case_id,
            self.provider.provider_name,
            model_used,
            total_usage.get("total_tokens", 0),
            record.status.value,
            record.review_status.value,
        )
        return record

    def extract_batch(
        self,
        segments: list[CaseSegment],
        pages_by_doc: dict[str, list[PageArtifact]],
        sha256_by_doc: dict[str, str] | None = None,
        on_case_done: Callable[[CaseRecord], None] | None = None,
        max_workers: int | None = None,
    ) -> list[CaseRecord]:
        """Extract multiple CaseSegments concurrently with per-case error isolation."""
        if not segments:
            return []
        workers = max(1, max_workers or self.cfg.llm.concurrency)
        sha_map = sha256_by_doc or {}
        results: dict[int, CaseRecord] = {}

        with ThreadPoolExecutor(max_workers=workers) as pool:
            future_to_pos = {
                pool.submit(
                    self.extract_case,
                    seg,
                    pages_by_doc.get(seg.document_id, []),
                    sha_map.get(seg.document_id, ""),
                ): pos
                for pos, seg in enumerate(segments)
            }
            for future in as_completed(future_to_pos):
                pos = future_to_pos[future]
                seg = segments[pos]
                try:
                    record = future.result()
                except Exception as exc:  # pragma: no cover - extract_case isolates errors
                    self.logger.exception("Unhandled error in extract_batch for %s", seg.case_id)
                    record = CaseRecord(
                        document_id=seg.document_id,
                        file_sha256=sha_map.get(seg.document_id, ""),
                        case_id=seg.case_id,
                        case_order=seg.case_order,
                        source_type=seg.source_type,
                        title=seg.title,
                        status=TaskStatus.FAILED,
                        review_status=ReviewStatus.FAILED,
                        review_reasons=[f"Unexpected extraction failure: {exc}"],
                        extraction_method=self.provider.provider_name,
                        model_name=self.provider.model_name,
                        rule_version=self.cfg.rules.rule_version,
                        error=str(exc),
                    )
                results[pos] = record
                if on_case_done is not None:
                    on_case_done(record)

        return [results[i] for i in range(len(segments))]
