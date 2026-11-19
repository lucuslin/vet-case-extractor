"""Source provenance grounding linking extracted fields to PDF pages and TextBlock bounding boxes."""

from __future__ import annotations

import re

from vet_case_extractor.models import (
    BBox,
    CaseRecord,
    EvidenceSpan,
    PageArtifact,
    ReviewStatus,
    TaskStatus,
)
from vet_case_extractor.utils.text import normalize_for_match

_SPECIES_PREFIX_RE = re.compile(
    r"^(?:大熊猫|小熊猫|美洲驼|奶牛|水牛|牦牛|山羊|绵羊|豚鼠|仓鼠|龙猫|大鼠|小鼠|"
    r"刺猬|水貂|雪貂|羊驼|骆驼|鹦鹉|蜥蜴|犬|猫|马|驴|骡|牛|羊|猪|兔|鹿|狐|猴|虎|狮|熊|"
    r"鸡|鸭|鹅|鸽|鸟|禽|龟|蛇|鳄|蛙|鱼)\s*"
)
_SENTENCE_SPLIT_RE = re.compile(r"[\n。；;]+")
_CLAUSE_SPLIT_RE = re.compile(r"[，,、：:]+")


def _union_bbox(bboxes: list[BBox]) -> BBox | None:
    if not bboxes:
        return None
    return BBox(
        x0=min(b.x0 for b in bboxes),
        y0=min(b.y0 for b in bboxes),
        x1=max(b.x1 for b in bboxes),
        y1=max(b.y1 for b in bboxes),
    )


def _page_searchable_texts(page: PageArtifact) -> list[str]:
    texts: list[str] = []
    for candidate in (page.cleaned_text, page.raw_text, page.layout_text):
        if candidate and candidate not in texts:
            texts.append(candidate)
    if page.blocks:
        joined = "\n".join(b.text for b in page.blocks if b.text)
        if joined and joined not in texts:
            texts.append(joined)
    return texts


def _candidate_snippets(
    raw_val: str,
    norm_val: str,
    explicit_quotes: list[str],
    is_diagnosis: bool = False,
) -> list[str]:
    snippets: list[str] = []
    seen: set[str] = set()

    def _add(text: str) -> None:
        t = text.strip()
        if t and t not in seen:
            seen.add(t)
            snippets.append(t)

    for q in explicit_quotes:
        _add(q)

    for src in (raw_val, norm_val):
        if not src:
            continue
        _add(src)
        if is_diagnosis:
            core = _SPECIES_PREFIX_RE.sub("", src).strip()
            if core and core != src:
                _add(core)
        for sent in _SENTENCE_SPLIT_RE.split(src):
            sent_s = sent.strip()
            if not sent_s:
                continue
            _add(sent_s)
            if len(sent_s) > 16:
                for clause in _CLAUSE_SPLIT_RE.split(sent_s):
                    clause_s = clause.strip()
                    if len(clause_s) >= 4:
                        _add(clause_s)
                # Sliding substrings help ground lines broken across OCR/PDF blocks
                if len(sent_s) >= 24:
                    _add(sent_s[:16])
                    mid = len(sent_s) // 2
                    _add(sent_s[max(0, mid - 8) : mid + 8])
            if is_diagnosis:
                core_s = _SPECIES_PREFIX_RE.sub("", sent_s).strip()
                if core_s:
                    _add(core_s)
                    # Also strip trailing parenthetical when matching core disease name
                    no_paren = re.sub(r"[（(][^()（）]+[）)]", "", core_s).strip()
                    if no_paren:
                        _add(no_paren)

    return snippets


def _match_snippet_in_pages(
    snippet: str,
    pages: list[PageArtifact],
    allow_short: bool,
) -> tuple[int, BBox | None, float] | None:
    """Search one snippet across pages; returns `(page_index, bbox, match_score)` or None."""
    q_clean = snippet.strip()
    norm_q = normalize_for_match(q_clean)
    if not norm_q:
        return None
    if len(norm_q) < 4 and not allow_short:
        return None
    if len(norm_q) < 2:
        return None

    best: tuple[int, BBox | None, float] | None = None

    for page in pages:
        # 1. Exact block match
        for block in page.blocks:
            if q_clean in block.text:
                return page.page_index, block.bbox, 1.0

        # 2. Normalized block match (single block)
        for block in page.blocks:
            norm_b = normalize_for_match(block.text)
            if not norm_b:
                continue
            if norm_q in norm_b:
                cand = (page.page_index, block.bbox, 0.95)
                if best is None or cand[2] > best[2]:
                    best = cand

        if best is not None and best[2] >= 0.95:
            continue

        # 3. Page-level text match + multi-block union bbox
        page_texts = _page_searchable_texts(page)
        exact_page_hit = any(q_clean in pt for pt in page_texts)
        norm_page_hit = exact_page_hit or any(norm_q in normalize_for_match(pt) for pt in page_texts)
        if not norm_page_hit:
            continue

        matched_bboxes: list[BBox] = []
        if page.blocks:
            for block in page.blocks:
                norm_b = normalize_for_match(block.text)
                if len(norm_b) >= 3 and norm_b in norm_q:
                    matched_bboxes.append(block.bbox)
                elif len(norm_q) >= 6 and len(norm_b) >= 4:
                    # Check 4-char window overlap between block and query
                    for idx in range(0, len(norm_b) - 3):
                        if norm_b[idx : idx + 4] in norm_q:
                            matched_bboxes.append(block.bbox)
                            break

        union = _union_bbox(matched_bboxes)
        score = 0.92 if exact_page_hit else (0.90 if union is not None else 0.85)
        cand = (page.page_index, union, score)
        if best is None or cand[2] > best[2]:
            best = cand

    return best


def ground_case_provenance(
    record: CaseRecord,
    pages: list[PageArtifact],
    extraction_method: str,
    model_name: str,
    rule_version: str,
    evidence_quotes: dict[str, list[str]] | None = None,
) -> CaseRecord:
    """Match extracted case fields against PageArtifacts and TextBlock bboxes.

    Sets `review_status = ReviewStatus.UNGROUNDED` and appends to `review_reasons`
    without fabricating `page_index` or `bbox` if any non-empty extracted field has
    no matchable source span.
    """
    ordered_pages = sorted(pages, key=lambda p: p.page_index)
    quotes_map = evidence_quotes or {}

    field_specs: list[tuple[str, str, str, tuple[str, ...], bool]] = [
        ("medical_history", record.medical_history_raw, record.medical_history, ("medical_history",), False),
        ("medical_test", record.medical_test_raw, record.medical_test, ("medical_test",), False),
        (
            "medical_treatment",
            record.medical_treatment_raw,
            record.medical_treatment,
            ("medical_treatment",),
            False,
        ),
        (
            "diagnosis",
            record.diagnosis_raw,
            record.diagnosis_normalized,
            ("diagnosis", "diagnosis_raw", "diagnosis_normalized", "Dx"),
            True,
        ),
        ("follow_up", record.follow_up_raw, record.follow_up, ("follow_up", "follow-up"), False),
    ]

    spans: list[EvidenceSpan] = []
    ungrounded_fields: list[str] = []

    for field_name, raw_val, norm_val, aliases, is_dx in field_specs:
        norm_s = (norm_val or "").strip()
        raw_s = (raw_val or "").strip()
        # Empty fields were legitimately absent and do not require grounding
        if not norm_s and not (is_dx and raw_s):
            continue

        explicit_quotes: list[str] = []
        for alias in aliases:
            q_list = quotes_map.get(alias)
            if isinstance(q_list, list):
                explicit_quotes.extend(str(q).strip() for q in q_list if str(q).strip())

        snippets = _candidate_snippets(raw_s, norm_s, explicit_quotes, is_diagnosis=is_dx)
        effective_text = raw_s or norm_s
        allow_short = len(normalize_for_match(effective_text)) < 4

        best_match: tuple[int, BBox | None, float, str] | None = None
        page_by_index = {p.page_index: p for p in ordered_pages}
        full_level_count = len(explicit_quotes) + (1 if raw_s else 0) + (1 if norm_s and norm_s != raw_s else 0)
        for idx, snip in enumerate(snippets):
            res = _match_snippet_in_pages(
                snip,
                ordered_pages,
                allow_short=allow_short or (idx == 0 and len(snippets) == 1),
            )
            if res is None:
                continue
            page_idx, bbox, score = res
            if best_match is None or score > best_match[2]:
                best_match = (page_idx, bbox, score, snip)
            page_has_blocks = bool(page_by_index[page_idx].blocks) if page_idx in page_by_index else False
            if score >= 0.99 or (idx < full_level_count and score >= 0.88 and (bbox is not None or not page_has_blocks)):
                break

        if best_match is not None:
            page_idx, bbox, score, matched_quote = best_match
            spans.append(
                EvidenceSpan(
                    document_id=record.document_id,
                    file_sha256=record.file_sha256,
                    case_id=record.case_id,
                    field_name=field_name,
                    page_index=page_idx,
                    quote=matched_quote[:240],
                    bbox=bbox,
                    match_score=score,
                    extraction_method=extraction_method,
                    model_name=model_name,
                    rule_version=rule_version,
                    review_status=ReviewStatus.OK,
                )
            )
        else:
            ungrounded_fields.append(field_name)
            spans.append(
                EvidenceSpan(
                    document_id=record.document_id,
                    file_sha256=record.file_sha256,
                    case_id=record.case_id,
                    field_name=field_name,
                    page_index=None,
                    quote=effective_text[:240],
                    bbox=None,
                    match_score=0.0,
                    extraction_method=extraction_method,
                    model_name=model_name,
                    rule_version=rule_version,
                    review_status=ReviewStatus.UNGROUNDED,
                    notes="No matchable source text or bounding box found in document pages.",
                )
            )

    record.evidence_spans = spans
    record.extraction_method = extraction_method
    record.model_name = model_name
    record.rule_version = rule_version

    if ungrounded_fields:
        record.review_status = ReviewStatus.UNGROUNDED
        if record.status != TaskStatus.FAILED:
            record.status = TaskStatus.REVIEW_REQUIRED
        reason = (
            f"Ungrounded field(s) with no matching source span: {', '.join(ungrounded_fields)}"
        )
        if reason not in record.review_reasons:
            record.review_reasons.append(reason)

    return record
