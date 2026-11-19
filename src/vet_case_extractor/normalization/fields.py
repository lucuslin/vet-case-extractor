"""Field-level post-extraction normalization: Tier C retention, reasoning separation, post-mortem routing."""

from __future__ import annotations

import re

from vet_case_extractor.config import ExtractionRules
from vet_case_extractor.models import (
    CaseRecord,
    CaseSegment,
    LLMExtractedPayload,
    NormalizationChange,
    ReviewStatus,
    TaskStatus,
)
from vet_case_extractor.normalization.dx import normalize_dx
from vet_case_extractor.normalization.rx import normalize_rx

_DEFAULT_REASONING_MARKERS = (
    "结合临床初步诊断",
    "鉴别诊断不能排除",
    "初步怀疑",
    "初步考虑",
    "初步诊断",
    "初步排除",
    "初诊考虑",
    "由此推断",
    "分析可能是",
    "暂时排除",
    "不排除",
    "病变定位于",
    "神经学定位",
    "临床意义",
    "考虑为",
    "疑似",
    "提示",
    "符合",
    "可能与",
    "推断",
)

_DEFAULT_TIER_C_MARKERS = (
    "外院",
    "他院",
    "转诊",
    "主人",
    "畜主",
    "既往",
    "曾诊断",
    "来院前",
    "入院前",
    "此前",
    "之前",
)

_DEFAULT_POSTMORTEM_MARKERS = (
    "安乐死后剖检",
    "死后剖检",
    "尸体剖检",
    "死后检查",
    "尸检",
    "剖检",
)

_DEFAULT_LIVING_EXCLUSIONS = (
    "开腹探查",
    "开胸探查",
    "术中探查",
    "离体",
    "截肢后",
    "手术切除后",
    "活检",
    "活组织检查",
)

_DEFAULT_FORBIDDEN_PLACEHOLDERS = {
    "无",
    "未提及",
    "未见",
    "不适用",
    "n/a",
    "na",
    "无记录",
    "未详",
    "不详",
    "暂无",
    "—",
    "-",
}

_REASONING_LEADIN_RE = re.compile(
    r"^(?:[\d.、\s]*)(?:故|因此|综上(?:所述)?|综合(?:上述|临床)?|所以|此时|该(?:结果|表现|检查)|检查结果|超声|X线|血液学)?[，,\s]*"
)

# Objective observation indicators that signal a mixed fact+reasoning sentence rather than pure reasoning
_OBJECTIVE_FACT_RE = re.compile(
    r"(?:\d+(?:\.\d+)?\s*(?:mmol|μmol|umol|mg|g/L|U/L|IU|%|kg|℃|mmHg|cm|mm|mL|ml|×10|次/min|个/HP)|"
    r"(?:见|可见|显示|测得|检出|发现|触及|听诊|穿刺|抽取|呈|升高|降低|增厚|增大|肿大|积液|阴影|回声|穿孔|结石))"
)

_EXPLICIT_PE_HEADER_RE = re.compile(r"^(?:一般)?体格检查\s*[:：]")
_TEST_SUBHEADER_RE = re.compile(r"(?:实验室|血常规|生化|影像|X线|超声|B超|神经学|眼科|口腔)")

_DANGLING_FRAGMENT_RE = re.compile(
    r"^(?:X线整体|同时超声也|ACTH刺激试验结果|检查结果|超声也|血常规结果|生化结果)[。；，]?$"
)


def _clean_placeholder(
    value: str,
    field_name: str,
    rules: ExtractionRules,
    changes: list[NormalizationChange],
) -> str:
    stripped = (value or "").strip()
    if not stripped:
        return ""
    placeholders = {p.lower() for p in (rules.forbidden_placeholders or [])} | _DEFAULT_FORBIDDEN_PLACEHOLDERS
    if stripped.lower() in placeholders:
        changes.append(
            NormalizationChange(
                field_name=field_name,
                rule_id="FIELD_PLACEHOLDER_CLEARED",
                before=stripped,
                after="",
                reason="Converted forbidden placeholder string to empty string.",
            )
        )
        return ""
    return stripped


def _split_sentences_preserve_parens(text: str) -> list[str]:
    """Split text into sentences on `。`, `；`, `\\n` while keeping parentheses intact."""
    if not text:
        return []
    sentences: list[str] = []
    buf: list[str] = []
    depth = 0
    for ch in text:
        if ch in ("(", "（"):
            depth += 1
            buf.append(ch)
        elif ch in (")", "）"):
            depth = max(0, depth - 1)
            buf.append(ch)
        elif depth == 0 and ch in ("。", "；", "\n"):
            if ch in ("。", "；"):
                buf.append(ch)
            sent = "".join(buf).strip()
            if sent:
                sentences.append(sent)
            buf.clear()
        else:
            buf.append(ch)
    tail = "".join(buf).strip()
    if tail:
        sentences.append(tail)
    return sentences


def _clean_sentence_fragments(text: str) -> str:
    """Clean dangling punctuation and incomplete sentence stubs left after stripping."""
    if not text:
        return ""
    sents = _split_sentences_preserve_parens(text)
    kept: list[str] = []
    for sent in sents:
        s = re.sub(r"^[，,；;。.\s]+", "", sent).strip()
        if not s or _DANGLING_FRAGMENT_RE.match(s):
            continue
        if len(s) <= 2 and s in ("。", "；", "，"):
            continue
        kept.append(s)
    joined = "".join(
        s if (i == 0 or kept[i - 1].endswith(("。", "；", "！", "？", "\n"))) else f"；{s}"
        for i, s in enumerate(kept)
    )
    joined = re.sub(r"([。；])\1+", r"\1", joined)
    return joined.strip(" ；;，,")


def _separate_reasoning_sentences(
    field_name: str,
    text: str,
    rules: ExtractionRules,
    changes: list[NormalizationChange],
    review_reasons: list[str],
) -> tuple[str, list[str]]:
    """Strip standalone reasoning sentences to notes while protecting Tier C and mixed fact+reasoning sentences."""
    if not text:
        return "", []

    reasoning_markers = tuple(rules.reasoning_markers or _DEFAULT_REASONING_MARKERS)
    tier_c_markers = tuple(rules.tier_c_retention_markers or _DEFAULT_TIER_C_MARKERS)

    kept_sents: list[str] = []
    stripped_notes: list[str] = []

    for sent in _split_sentences_preserve_parens(text):
        # Tier C retention: external clinic / owner / prior history must stay in place
        if any(tc in sent for tc in tier_c_markers):
            kept_sents.append(sent)
            continue

        matched_markers = [m for m in reasoning_markers if m in sent]
        if not matched_markers:
            kept_sents.append(sent)
            continue

        # Determine whether sentence is standalone reasoning vs mixed objective fact + reasoning
        lead_stripped = _REASONING_LEADIN_RE.sub("", sent).strip()
        starts_with_reasoning = any(lead_stripped.startswith(m) for m in matched_markers)
        # Check whether objective facts precede the earliest reasoning marker
        first_pos = min(sent.find(m) for m in matched_markers)
        prefix_before_marker = sent[:first_pos].strip()
        has_objective_fact = bool(_OBJECTIVE_FACT_RE.search(prefix_before_marker))

        if starts_with_reasoning or not has_objective_fact:
            stripped_notes.append(f"【原文推理/解读剥离】{sent}")
            changes.append(
                NormalizationChange(
                    field_name=field_name,
                    rule_id="FIELD_REASONING_TO_NOTES",
                    before=sent,
                    after="",
                    reason=f"Moved standalone reasoning/interpretation sentence (matched {matched_markers[0]}) to notes.",
                )
            )
        else:
            # Mixed fact + reasoning sentence: preserve fact intact and flag for manual review (RD-002)
            kept_sents.append(sent)
            review_reasons.append(
                f"{field_name} contains mixed objective fact and reasoning ('{sent[:45]}'); preserved for manual review."
            )

    cleaned = _clean_sentence_fragments("".join(kept_sents))
    return cleaned, stripped_notes


def _route_postmortem_from_test(
    medical_test: str,
    follow_up: str,
    rules: ExtractionRules,
    changes: list[NormalizationChange],
) -> tuple[str, str]:
    """Move post-mortem necropsy sentences from medical_test to follow_up unless living exploration."""
    if not medical_test:
        return "", follow_up

    postmortem_markers = tuple(rules.postmortem_markers or _DEFAULT_POSTMORTEM_MARKERS)
    living_exclusions = tuple(rules.living_exploration_exclusions or _DEFAULT_LIVING_EXCLUSIONS)

    kept_test: list[str] = []
    moved_follow_up: list[str] = []

    for sent in _split_sentences_preserve_parens(medical_test):
        has_postmortem = any(pm in sent for pm in postmortem_markers)
        has_living_excl = any(le in sent for le in living_exclusions)
        if has_postmortem and not has_living_excl:
            moved_follow_up.append(sent)
            changes.append(
                NormalizationChange(
                    field_name="medical_test",
                    rule_id="R08_POSTMORTEM_TO_FOLLOWUP",
                    before=sent,
                    after="",
                    reason="Moved post-mortem necropsy findings from medical_test to follow_up.",
                )
            )
        else:
            kept_test.append(sent)

    new_test = _clean_sentence_fragments("".join(kept_test))
    if moved_follow_up:
        moved_text = _clean_sentence_fragments("".join(moved_follow_up))
        new_follow_up = f"{follow_up.rstrip(' ；;。')}。{moved_text}" if follow_up else moved_text
    else:
        new_follow_up = follow_up
    return new_test, new_follow_up


def _route_explicit_pe_from_test(
    medical_history: str,
    medical_test: str,
    changes: list[NormalizationChange],
) -> tuple[str, str]:
    """Move explicitly labeled `体格检查：` sentences from medical_test into medical_history."""
    if not medical_test:
        return medical_history, ""

    kept_test: list[str] = []
    moved_pe: list[str] = []
    for sent in _split_sentences_preserve_parens(medical_test):
        if _EXPLICIT_PE_HEADER_RE.match(sent) and not _TEST_SUBHEADER_RE.search(sent):
            moved_pe.append(sent)
            changes.append(
                NormalizationChange(
                    field_name="medical_test",
                    rule_id="R04_PE_TO_HISTORY",
                    before=sent,
                    after="",
                    reason="Moved explicit general physical examination block from medical_test to medical_history.",
                )
            )
        else:
            kept_test.append(sent)

    new_test = _clean_sentence_fragments("".join(kept_test))
    if moved_pe:
        pe_text = _clean_sentence_fragments("".join(moved_pe))
        new_history = f"{medical_history.rstrip(' ；;。')}。{pe_text}" if medical_history else pe_text
    else:
        new_history = medical_history
    return new_history, new_test


def normalize_case_fields(
    payload: LLMExtractedPayload,
    segment: CaseSegment,
    rules: ExtractionRules,
    method: str,
    model_name: str,
    rule_version: str,
    file_sha256: str = "",
) -> CaseRecord:
    """Normalize extracted case payload into a dual-layer CaseRecord while preserving *_raw."""
    changes: list[NormalizationChange] = []
    review_reasons: list[str] = []

    raw_history = (payload.medical_history or "").strip()
    raw_test = (payload.medical_test or "").strip()
    raw_treatment = (payload.medical_treatment or "").strip()
    raw_dx = (payload.diagnosis_raw or "").strip()
    raw_follow_up = (payload.follow_up or "").strip()
    raw_notes = (payload.notes or "").strip()

    # Clean forbidden placeholders before field normalization
    norm_history = _clean_placeholder(raw_history, "medical_history", rules, changes)
    norm_test = _clean_placeholder(raw_test, "medical_test", rules, changes)
    norm_treatment_base = _clean_placeholder(raw_treatment, "medical_treatment", rules, changes)
    dx_input = _clean_placeholder(raw_dx, "diagnosis_raw", rules, changes)
    norm_follow_up = _clean_placeholder(raw_follow_up, "follow_up", rules, changes)
    norm_notes = _clean_placeholder(raw_notes, "notes", rules, changes)

    # Route explicit PE out of test into history, and post-mortem necropsy out of test into follow-up
    norm_history, norm_test = _route_explicit_pe_from_test(norm_history, norm_test, changes)
    norm_test, norm_follow_up = _route_postmortem_from_test(
        norm_test, norm_follow_up, rules, changes
    )

    # Separate standalone reasoning sentences into notes (respecting Tier C & mixed sentences)
    extra_notes: list[str] = []
    norm_history, h_notes = _separate_reasoning_sentences(
        "medical_history", norm_history, rules, changes, review_reasons
    )
    extra_notes.extend(h_notes)
    norm_test, t_notes = _separate_reasoning_sentences(
        "medical_test", norm_test, rules, changes, review_reasons
    )
    extra_notes.extend(t_notes)
    norm_treatment_base, tr_notes = _separate_reasoning_sentences(
        "medical_treatment", norm_treatment_base, rules, changes, review_reasons
    )
    extra_notes.extend(tr_notes)
    norm_follow_up, f_notes = _separate_reasoning_sentences(
        "follow_up", norm_follow_up, rules, changes, review_reasons
    )
    extra_notes.extend(f_notes)

    # Normalize Rx and Dx
    norm_treatment, rx_changes = normalize_rx(norm_treatment_base, rules)
    changes.extend(rx_changes)

    dx_normalized, dx_moved_notes, dx_changes = normalize_dx(dx_input, rules)
    changes.extend(dx_changes)
    if dx_moved_notes:
        extra_notes.append(f"【Dx括号解读剥离】{dx_moved_notes}")

    notes_segments = [norm_notes] if norm_notes else []
    notes_segments.extend(extra_notes)
    final_notes = "\n".join(dict.fromkeys(seg for seg in notes_segments if seg))

    # Determine review status & reasons
    review_status = ReviewStatus.OK
    status = TaskStatus.COMPLETED

    if segment.boundary_uncertain:
        review_status = ReviewStatus.REVIEW_REQUIRED
        status = TaskStatus.REVIEW_REQUIRED
        review_reasons.extend(segment.boundary_notes)

    if payload.uncertainties:
        review_status = ReviewStatus.REVIEW_REQUIRED
        status = TaskStatus.REVIEW_REQUIRED
        review_reasons.extend(u.strip() for u in payload.uncertainties if u.strip())

    if review_reasons:
        review_status = ReviewStatus.REVIEW_REQUIRED
        status = TaskStatus.REVIEW_REQUIRED

    lab_anomalies = [
        f"p{row.page_index + 1} {row.item_raw}: {'; '.join(row.anomalies)}"
        for row in segment.lab_rows
        if row.anomalies
    ]
    if lab_anomalies:
        if review_status == ReviewStatus.OK:
            review_status = ReviewStatus.OCR_SUSPECT
        status = TaskStatus.REVIEW_REQUIRED
        review_reasons.extend(lab_anomalies)

    # Authoritative source/year/title resolution
    source_title = segment.source_journal or _clean_placeholder(payload.source, "source", rules, changes)
    pub_year = segment.publication_year or _clean_placeholder(
        payload.publication_year, "publication_year", rules, changes
    )
    rec_year = segment.received_year or _clean_placeholder(
        payload.received_year, "received_year", rules, changes
    )
    title = segment.title or _clean_placeholder(payload.title, "title", rules, changes)

    return CaseRecord(
        document_id=segment.document_id,
        file_sha256=file_sha256,
        case_id=segment.case_id,
        case_order=segment.case_order,
        source_type=segment.source_type,
        source_title=source_title,
        publication_year=pub_year,
        received_year=rec_year,
        page_range=segment.page_range,
        book_page_range=segment.book_page_range,
        chapter_number=segment.chapter_number,
        chapter_title=segment.chapter_title,
        case_number=segment.case_number,
        case_level=segment.case_level,
        title=title,
        medical_history_raw=raw_history,
        medical_test_raw=raw_test,
        medical_treatment_raw=raw_treatment,
        diagnosis_raw=raw_dx,
        follow_up_raw=raw_follow_up,
        notes_raw=raw_notes,
        medical_history=norm_history,
        medical_test=norm_test,
        medical_treatment=norm_treatment,
        diagnosis_normalized=dx_normalized,
        follow_up=norm_follow_up,
        notes=final_notes,
        normalization_changes=changes,
        lab_rows=list(segment.lab_rows),
        status=status,
        review_status=review_status,
        review_reasons=list(dict.fromkeys(r for r in review_reasons if r)),
        extraction_method=method,
        model_name=model_name,
        rule_version=rule_version,
    )
