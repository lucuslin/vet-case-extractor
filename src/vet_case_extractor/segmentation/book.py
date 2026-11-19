"""Scanned textbook segmentation: chapter headers, whole-line case headers, and continuity checks."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

from vet_case_extractor.config import ExtractionRules
from vet_case_extractor.models import (
    CaseSegment,
    DocumentRecord,
    LabTableRow,
    PageArtifact,
    SourceType,
)
from vet_case_extractor.utils.text import clean_ocr_noise, normalize_for_match

_LAYOUT_MARKER_RE = re.compile(r"^\[\[(?:通栏|左栏|右栏)\]\]\s*")

# Whole-line case header regex accepting dash OCR variants (`一` / `-` / `—` / `－` / `~` / `、`)
_BOOK_CASE_HEADER_RE = re.compile(
    r"^病例\s*(\d+)\s*[一\-—－~、·.]*\s*等级\s*(\d+|[一二三四五])$"
)
_BOOK_CHAPTER_HEADER_RE = re.compile(
    r"^(第([一二三四五六七八九十百零\d]+)章(?:\s*[:：\-—·]?\s*(.*))?)$"
)
_TOC_DOTS_OR_PAGENUM_RE = re.compile(r"(?:\.{2,}|…{2,}|\s+\d{1,4})$")

_CN_LEVEL_MAP = {
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
}

_CN_DIGITS = {
    "零": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}


@dataclass
class _BookLine:
    page_pos: int
    page_index: int
    pdf_page_number: int
    book_page_number: int | None
    raw_line: str
    clean_line: str


@dataclass
class _CaseMarker:
    entry_idx: int
    chapter_number: int | None
    chapter_title: str
    case_num: int
    level_raw: str
    level_int: int | None
    suspicious_level: bool


def _parse_cn_or_int(token: str) -> int | None:
    s = token.strip()
    if not s:
        return None
    if s.isdigit():
        return int(s)
    if s in _CN_DIGITS:
        return _CN_DIGITS[s]
    total = 0
    current = 0
    unit_map = {"十": 10, "百": 100}
    for ch in s:
        if ch in _CN_DIGITS:
            current = _CN_DIGITS[ch]
        elif ch in unit_map:
            unit = unit_map[ch]
            total += (current if current else 1) * unit
            current = 0
        else:
            return None
    return total + current if (total + current) > 0 else None


def _strip_layout_marker(line: str) -> str:
    return _LAYOUT_MARKER_RE.sub("", line).strip()


def _get_page_text(page: PageArtifact) -> str:
    if page.layout_text.strip():
        return page.layout_text
    if page.cleaned_text.strip():
        return page.cleaned_text
    if page.raw_text.strip():
        return clean_ocr_noise(page.raw_text, repair_founder=True)
    return ""


def _is_toc_page(clean_lines: list[str]) -> bool:
    has_toc_heading = any(re.match(r"^(?:目\s*录|CONTENTS)$", ln, re.IGNORECASE) for ln in clean_lines[:5])
    if not has_toc_heading:
        return False
    dotted_count = sum(1 for ln in clean_lines if _TOC_DOTS_OR_PAGENUM_RE.search(ln))
    chapter_count = sum(1 for ln in clean_lines if _BOOK_CHAPTER_HEADER_RE.match(ln))
    return dotted_count >= 3 or chapter_count >= 2


def _format_range(nums: list[int]) -> str:
    if not nums:
        return ""
    if len(nums) == 1:
        return str(nums[0])
    return f"{nums[0]}-{nums[-1]}"


def _filter_lab_rows_for_slice(
    sliced_pages: list[PageArtifact],
    case_text: str,
    shared_pages: set[int],
) -> list[LabTableRow]:
    norm_case = normalize_for_match(case_text)
    rows: list[LabTableRow] = []
    for page in sliced_pages:
        for row in page.lab_rows:
            if page.page_index in shared_pages:
                item_norm = normalize_for_match(row.item_raw)
                if item_norm and item_norm not in norm_case:
                    continue
            rows.append(row)
    return rows


def segment_book_document(
    doc: DocumentRecord,
    pages: list[PageArtifact],
    rules: ExtractionRules,
) -> tuple[list[CaseSegment], list[str]]:
    """Segment a scanned veterinary casebook into CaseSegments and check chapter continuity."""
    _ = rules  # reserved for future heading extensions
    ordered_pages = sorted(pages, key=lambda p: p.page_index)
    lines: list[_BookLine] = []

    for page_pos, page in enumerate(ordered_pages):
        text = _get_page_text(page)
        raw_lines = [ln for ln in text.splitlines() if _strip_layout_marker(ln)]
        clean_lines = [_strip_layout_marker(ln) for ln in raw_lines]
        if _is_toc_page(clean_lines):
            continue
        for raw_ln, clean_ln in zip(raw_lines, clean_lines, strict=True):
            lines.append(
                _BookLine(
                    page_pos=page_pos,
                    page_index=page.page_index,
                    pdf_page_number=page.pdf_page_number,
                    book_page_number=page.book_page_number,
                    raw_line=raw_ln,
                    clean_line=clean_ln,
                )
            )

    current_chapter_num: int | None = None
    current_chapter_title: str = ""
    markers: list[_CaseMarker] = []
    chapter_boundaries: set[int] = set()

    for idx, entry in enumerate(lines):
        ch_match = _BOOK_CHAPTER_HEADER_RE.match(entry.clean_line)
        if ch_match and len(entry.clean_line) <= 45:
            parsed_ch_num = _parse_cn_or_int(ch_match.group(2))
            full_ch_title = re.sub(r"\s+", " ", ch_match.group(1)).strip()
            # Ignore repeated running header of the exact same active chapter
            if parsed_ch_num != current_chapter_num:
                current_chapter_num = parsed_ch_num
                current_chapter_title = full_ch_title
                chapter_boundaries.add(idx)
            elif len(full_ch_title) > len(current_chapter_title):
                current_chapter_title = full_ch_title
            continue

        case_match = _BOOK_CASE_HEADER_RE.match(entry.clean_line)
        if case_match:
            case_num = int(case_match.group(1))
            level_raw = case_match.group(2).strip()
            level_int = int(level_raw) if level_raw.isdigit() else _CN_LEVEL_MAP.get(level_raw)
            suspicious = level_int is None or level_int < 1 or level_int > 5
            markers.append(
                _CaseMarker(
                    entry_idx=idx,
                    chapter_number=current_chapter_num,
                    chapter_title=current_chapter_title,
                    case_num=case_num,
                    level_raw=level_raw,
                    level_int=level_int,
                    suspicious_level=suspicious,
                )
            )

    if not markers:
        return [], [f"{doc.filename}: no valid book case headers (病例N 等级X) matched."]

    # Slice lines for each case marker up to the next case marker or next chapter boundary
    sorted_chapter_bounds = sorted(chapter_boundaries)
    case_slices: list[tuple[_CaseMarker, list[_BookLine]]] = []
    page_usage_counts: dict[int, int] = {}

    for m_idx, marker in enumerate(markers):
        start_idx = marker.entry_idx
        stop_idx = markers[m_idx + 1].entry_idx if m_idx + 1 < len(markers) else len(lines)
        for ch_idx in sorted_chapter_bounds:
            if start_idx < ch_idx < stop_idx:
                stop_idx = ch_idx
                break
        sub_lines = lines[start_idx:stop_idx]
        case_slices.append((marker, sub_lines))
        for p_idx in {ln.page_index for ln in sub_lines}:
            page_usage_counts[p_idx] = page_usage_counts.get(p_idx, 0) + 1

    shared_pages = {p_idx for p_idx, cnt in page_usage_counts.items() if cnt > 1}

    # Check chapter continuity (1..max missing and duplicates)
    continuity_issues: list[str] = []
    by_chapter: dict[tuple[int | None, str], list[int]] = {}
    for marker in markers:
        by_chapter.setdefault((marker.chapter_number, marker.chapter_title), []).append(marker.case_num)

    duplicate_pairs: set[tuple[int | None, str, int]] = set()
    for (ch_num, ch_title), nums in by_chapter.items():
        ch_label = ch_title or (f"第{ch_num}章" if ch_num is not None else "未命名章节")
        counts = Counter(nums)
        dups = sorted(n for n, c in counts.items() if c > 1)
        if dups:
            continuity_issues.append(f"章节「{ch_label}」存在重号病例: {dups}")
            for d in dups:
                duplicate_pairs.add((ch_num, ch_title, d))

        max_num = max(nums)
        missing = [n for n in range(1, max_num + 1) if n not in counts]
        if missing:
            continuity_issues.append(
                f"章节「{ch_label}」病例号在 1..{max_num} 间存在缺号: {missing}"
            )

    segments: list[CaseSegment] = []
    for order, (marker, sub_lines) in enumerate(case_slices, start=1):
        by_page_pos: dict[int, list[str]] = {}
        for ln in sub_lines:
            by_page_pos.setdefault(ln.page_pos, []).append(ln.raw_line)

        sliced_pages = [ordered_pages[pos] for pos in sorted(by_page_pos.keys())]
        raw_chunks = [
            f"--- PDF第{ordered_pages[pos].pdf_page_number}页 ---\n" + "\n".join(by_page_pos[pos])
            for pos in sorted(by_page_pos.keys())
        ]
        raw_text = "\n\n".join(raw_chunks)

        pdf_nums = [p.pdf_page_number for p in sliced_pages]
        book_nums = [p.book_page_number for p in sliced_pages if p.book_page_number is not None]
        level_display = str(marker.level_int) if marker.level_int is not None else marker.level_raw
        title = (
            f"{marker.chapter_title} · 病例{marker.case_num} 等级{level_display}"
            if marker.chapter_title
            else f"病例{marker.case_num} 等级{level_display}"
        )

        boundary_uncertain = marker.suspicious_level
        boundary_notes: list[str] = []
        if marker.suspicious_level:
            boundary_notes.append(
                f"Suspicious OCR case level '等级{marker.level_raw}' (expected 1..5); verify against page image."
            )
        if (marker.chapter_number, marker.chapter_title, marker.case_num) in duplicate_pairs:
            boundary_uncertain = True
            boundary_notes.append(
                f"Duplicate case number 病例{marker.case_num} detected in chapter '{marker.chapter_title or marker.chapter_number}'."
            )

        segments.append(
            CaseSegment(
                case_id=f"{doc.document_id}__case_{order:03d}",
                document_id=doc.document_id,
                source_filename=doc.filename,
                source_type=SourceType.BOOK,
                case_order=order,
                chapter_number=marker.chapter_number,
                chapter_title=marker.chapter_title,
                case_number=marker.case_num,
                case_level=marker.level_int,
                case_level_raw=marker.level_raw,
                title=title,
                page_indices=[p.page_index for p in sliced_pages],
                page_range=_format_range(pdf_nums),
                book_page_range=_format_range(book_nums),
                image_paths=[p.image_path for p in sliced_pages if p.image_path],
                raw_text=raw_text,
                lab_rows=_filter_lab_rows_for_slice(sliced_pages, raw_text, shared_pages),
                boundary_uncertain=boundary_uncertain,
                boundary_notes=boundary_notes,
            )
        )

    return segments, continuity_issues
