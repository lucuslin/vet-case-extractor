"""Journal article segmentation: metadata extraction, omnibus slicing, and multi-case splitting."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from vet_case_extractor.models import (
    CaseSegment,
    DocumentRecord,
    LabTableRow,
    PageArtifact,
    SourceType,
)
from vet_case_extractor.utils.text import clean_ocr_noise, normalize_for_match

_KNOWN_JOURNALS = (
    "中国兽医杂志",
    "中国兽医科学",
    "中国兽医学报",
    "畜牧与兽医",
    "黑龙江畜牧兽医",
    "动物医学进展",
    "中国预防兽医学报",
    "中国动物传染病学报",
    "畜牧兽医学报",
    "现代畜牧兽医",
    "中国工作犬业",
    "中国宠物医师",
    "上海畜牧兽医通讯",
    "浙江畜牧兽医",
    "甘肃畜牧兽医",
    "贵州畜牧兽医",
    "福建畜牧兽医",
    "山东畜牧兽医",
    "湖北畜牧兽医",
    "广东畜牧兽医科技",
    "四川畜牧兽医",
    "云南畜牧兽医",
    "江西畜牧兽医杂志",
    "湖南畜牧兽医",
    "吉林畜牧兽医",
    "兽医导刊",
    "养犬",
)

_JOURNAL_HEADER_RE = re.compile(
    r"《?\s*([\u4e00-\u9fff]{2,14}(?:杂志|学报|兽医学|兽医|畜牧|医学|医师|科学|进展|通讯|导刊))\s*》?"
    r"(?:\s*(?:(?:19|20)\d{2}\s*[年,，(（]|\d+\s*卷|\(\s*\d+\s*\)))"
)
_ENGLISH_JOURNAL_RE = re.compile(
    r"(Chinese\s*Journal\s*of\s*Veterinary\s*Medicine|[A-Z][A-Za-z\s]+Journal\s+of\s+[A-Za-z\s]+)",
    re.IGNORECASE,
)
_PUB_YEAR_HEADER_RE = re.compile(
    r"(?:杂志|学报|兽医|畜牧|医学|科学|进展|通讯|导刊|》)\s*[,，\s]*((?:19|20)\d{2})\s*(?:年|[,，]\s*\d+|\()"
)
_ARTICLE_NO_YEAR_RE = re.compile(r"(?:文章编号|DOI)\s*[:：;]?\s*.*?(?:[(.])((?:19|20)\d{2})(?:[).])")
_RECEIVED_DATE_RE = re.compile(
    r"收稿(?:日期)?\s*[:：]?\s*((?:19|20)\d{2})(?:\s*[-—–./年]\s*\d{1,2}|\d{4})"
)

_LAYOUT_MARKER_RE = re.compile(r"^\[\[(?:通栏|左栏|右栏)\]\]\s*")
_NON_TITLE_PREFIX_RE = re.compile(
    r"^(?:\d+(?:[.．]\d+)*\s*[\u4e00-\u9fff]|"
    r"[一二三四五六七八九十]+、|图\s*\d|表\s*\d|\[\d+\]|"
    r"[（(]|《|注\s*[:：]|第?\d+卷|"
    r"[\u4e00-\u9fff]{2,4}等\s*[:：]|"
    r"中图分类号|文献标志码|文献标识码|文章编号|收稿|作者简介|通[讯信]作者|"
    r"D[O0]I|Corresponding|Case\s+Report|Keywords|Abstract|关键词|摘要|"
    r"参考文献|基金项目|Chinese\s*Journal|第[一二三四五六七八九十\d]+章)"
)
_JOURNAL_PREFIX_STRIP_RE = re.compile(
    r"^(?:\d{1,4}\s*)?《?[\u4e00-\u9fff]{2,14}(?:杂志|学报|兽医学|兽医|畜牧|医学|医师|科学|进展|通讯|导刊)》?"
    r"\s*(?:(?:19|20)\d{2}\s*(?:年\s*(?:[（(]?\s*第?\d+卷\s*[)）]?)?\s*(?:第?\d+期)?|[,，]\s*\d+\s*[（(]\s*\d+\s*[)）]\s*[:：]?\s*\d+[-—–]\d+|[(（]\s*\d+\s*[)）]\s*[:：]?\s*\d+[-—–]\d+))?"
    r"(?:\s*\d{1,4})?"
)
_PRIMARY_ARTICLE_META_RE = re.compile(
    r"(?:中图分类号|文献标志码|文献标识码|摘要\s*[:：]|"
    r"^[（(](?:1[.、．])?.*?(?:大学|学院|医院|中心|研究所|实验室|站|局|公司).*?[）)]?)"
)
_ARTICLE_NO_META_RE = re.compile(r"文章编号\s*[:：]?")
_AFFILIATION_KW_RE = re.compile(r"(?:大学|学院|医院|中心|研究所|实验室|站|局|公司)")
_PLATE_HEADER_RE = re.compile(r"^《\s*([^》]+?)\s*》\s*图版")
_FIG_TABLE_XREF_RE = re.compile(r"(?:见图\s*\d|如图\s*\d|见表\s*\d|如表\s*\d|图\s*\d+|表\s*\d+)")
_BODY_SECTION_OR_PROSE_RE = re.compile(r"(?:^\d{1,2}(?:[.．]\d{1,2})*\s*[\u4e00-\u9fff]|[。；;])")
_RESUME_AFTER_REF_RE = re.compile(
    r"^(?:"
    r"文章编号|中图分类号|文献标志码|文献标识码|收稿(?:日期)?\s*[:：]?|作者简介|通[讯信]作者|"
    r"《\s*[^》]+?\s*》\s*图版|"
    r"(?:图|表)\s*\d+|"
    r"\d{1,2}(?:[.．]\d{1,2})+\s*[\u4e00-\u9fff]|"
    r"\d{1,2}\s+(?:病例|基本|临床|材料|实验|检验|检查|影像|诊断|治疗|手术|预后|转归|结果|讨论|小结|结论)"
    r")"
)
_MULTI_CASE_RE = re.compile(
    r"^(?:(?:\d+(?:\.\d+)*)\s+)?(?:病例|病历|例|患[犬猫马牛猪羊])\s*([1-9]\d*|[一二三四五六七八九十])"
    r"(?:\s*[:：、.．,，(（\-—~]|(?=\s*[\u4e00-\u9fffA-Za-z]))"
)
_MULTI_CASE_FALSE_PREFIX_RE = re.compile(r"^(?:见|如|同|与|图|表|本院|笔者|共|现|其中|包括|参考)")
_DISCUSSION_OR_REF_RE = re.compile(r"^(?:\d+(?:\.\d+)*\s*)?(?:讨论|讨\s*论|小结|结语|参考文献)\s*[:：]?$")
_REFERENCES_HEADING_RE = re.compile(r"^参考文献\s*[:：]?$")

_CN_NUM_MAP = {
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
}


@dataclass
class _LineEntry:
    page_pos: int
    page_index: int
    pdf_page_number: int
    line_idx: int
    raw_line: str
    clean_line: str


@dataclass
class _ArticleAnchor:
    entry_idx: int
    page_pos: int
    line_idx: int
    title: str
    has_strong_meta: bool
    target_score: float


def _parse_case_num(token: str) -> int | None:
    token = token.strip()
    if token.isdigit():
        return int(token)
    return _CN_NUM_MAP.get(token)


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


def _build_line_entries(pages: list[PageArtifact]) -> list[_LineEntry]:
    entries: list[_LineEntry] = []
    for page_pos, page in enumerate(pages):
        text = _get_page_text(page)
        for line_idx, raw_line in enumerate(text.splitlines()):
            clean_line = _strip_layout_marker(raw_line)
            if not clean_line:
                continue
            entries.append(
                _LineEntry(
                    page_pos=page_pos,
                    page_index=page.page_index,
                    pdf_page_number=page.pdf_page_number,
                    line_idx=line_idx,
                    raw_line=raw_line,
                    clean_line=clean_line,
                )
            )
    return entries


def _extract_journal_and_pub_year(lines: list[str]) -> tuple[str, str]:
    source_journal = ""
    pub_year = ""

    for line in lines:
        if not source_journal:
            for jname in _KNOWN_JOURNALS:
                if jname in line:
                    source_journal = jname
                    break
        if not source_journal:
            m = _JOURNAL_HEADER_RE.search(line)
            if m:
                source_journal = m.group(1)

        if not pub_year and "收稿" not in line and "作者简介" not in line:
            ym = _PUB_YEAR_HEADER_RE.search(line)
            if ym:
                pub_year = ym.group(1)
            else:
                am = _ARTICLE_NO_YEAR_RE.search(line)
                if am:
                    pub_year = am.group(1)

        if source_journal and pub_year:
            break

    if not source_journal:
        for line in lines:
            em = _ENGLISH_JOURNAL_RE.search(line)
            if em:
                source_journal = re.sub(r"\s+", " ", em.group(1)).strip()
                break

    if not pub_year:
        for line in lines:
            if "收稿" in line or "作者简介" in line:
                continue
            if any(k in line for k in ("第", "卷", "期", "杂志", "学报", "兽医")):
                m = re.search(r"((?:19|20)\d{2})", line)
                if m:
                    pub_year = m.group(1)
                    break

    return source_journal, pub_year


def _extract_received_year(lines: list[str]) -> str:
    for line in lines:
        m = _RECEIVED_DATE_RE.search(line)
        if m:
            return m.group(1)
    return ""


def _cjk_chars(text: str) -> str:
    return "".join(ch for ch in text if "\u4e00" <= ch <= "\u9fff")


def _core_topic_cjk(text: str) -> str:
    """Strip generic species/report boilerplate to compare underlying topic overlap."""
    cleaned = re.sub(
        r"(?:病例报告|诊治病例|病理诊断报告病例|诊断与治疗|的病例|病例|诊治|诊断|治疗|报告|临床|一例|的)",
        "",
        text,
    )
    cleaned = re.sub(r"^(?:大熊猫|小熊猫|美洲驼|奶牛|水牛|山羊|绵羊|犬|猫|马|驴|牛|羊|猪)", "", cleaned)
    return _cjk_chars(cleaned)


def _strip_running_journal_prefix(line: str) -> str:
    """Return remainder of line after stripping running journal header; empty if pure header."""
    s = line.strip()
    if not s or re.fullmatch(r"\d{1,4}[.．]?", s):
        return ""
    if re.fullmatch(r"Chinese\s*Journal\s*of\s*Veterinary\s*Medicine", s, re.IGNORECASE):
        return ""
    m = _JOURNAL_PREFIX_STRIP_RE.match(s)
    if m and any(k in m.group(0) for k in ("杂志", "学报", "兽医", "畜牧", "医学", "科学", "进展", "通讯", "导刊")):
        rem = s[m.end() :].strip()
        if re.fullmatch(r"\d{1,4}[.．]?", rem):
            return ""
        return rem
    return s


def _clean_candidate_title(line: str, query_variants: list[str]) -> str:
    """Extract clean article title, trimming journal prefix and concatenated author names."""
    candidate_line = _strip_running_journal_prefix(line) or line.strip()
    norm_line = normalize_for_match(candidate_line)
    for q in query_variants:
        nq = normalize_for_match(q)
        if len(nq) >= 4 and norm_line.startswith(nq):
            if candidate_line.startswith(q):
                return q
            acc = []
            for ch in candidate_line:
                acc.append(ch)
                if len(normalize_for_match("".join(acc))) >= len(nq):
                    return "".join(acc).strip()
        # Handle 1-2 char OCR substitution inside title prefix (e.g. 猫胱氮酸结石 vs 猫胱氨酸结石)
        cjk_q = _cjk_chars(q)
        if len(cjk_q) >= 6:
            prefix_cjk = _cjk_chars(candidate_line[: len(q) + 4])
            if len(prefix_cjk) >= len(cjk_q):
                prefix_head = prefix_cjk[: len(cjk_q)]
                overlap = sum(1 for a, b in zip(prefix_head, cjk_q, strict=False) if a == b)
                if overlap / len(cjk_q) >= 0.75:
                    # Slice candidate_line up to end of matching title length
                    cjk_seen = 0
                    for idx_c, ch in enumerate(candidate_line):
                        if "\u4e00" <= ch <= "\u9fff":
                            cjk_seen += 1
                            if cjk_seen >= len(cjk_q):
                                return candidate_line[: idx_c + 1].strip()
    stripped = re.sub(
        r"\s*[\u4e00-\u9fff]{2,4}[1-9*\"'²³]?([,，、\s]+[\u4e00-\u9fff]{2,4}[1-9*\"'²³]?){1,8}$",
        "",
        candidate_line,
    ).strip()
    return stripped or candidate_line.strip()


def _score_title_match(line: str, queries: list[str]) -> float:
    candidate_line = _strip_running_journal_prefix(line)
    norm_line = normalize_for_match(candidate_line)
    if not norm_line or len(norm_line) > 120:
        return 0.0
    best = 0.0
    for q in queries:
        nq = normalize_for_match(q)
        if not nq or len(nq) < 3:
            continue
        if norm_line == nq:
            best = max(best, 1.0)
        elif norm_line.startswith(nq) and len(norm_line) <= len(nq) + 35:
            best = max(best, 0.95)
        elif nq in norm_line and len(norm_line) <= len(nq) + 35:
            best = max(best, 0.9)
        elif len(norm_line) >= 6 and norm_line in nq:
            best = max(best, 0.8)
        else:
            cjk_l = _cjk_chars(candidate_line)
            cjk_q = _cjk_chars(q)
            if len(cjk_q) >= 4 and len(cjk_l) >= 4:
                overlap = len(set(cjk_l) & set(cjk_q)) / max(len(set(cjk_q)), 1)
                if overlap >= 0.75 and len(cjk_l) <= len(cjk_q) + 25:
                    best = max(best, 0.85 * overlap)
    return best


def _looks_like_body_prose(line: str) -> bool:
    if any(p in line for p in ("。", "；", ";", "？", "！")):
        return True
    if line.endswith((",", "，", "、", ":", "：", "-", "—")):
        return True
    if "图版" in line or line.startswith("《"):
        return True
    if _FIG_TABLE_XREF_RE.search(line):
        return True
    if any(k in line for k in ("收稿日期", "作者简介", "通信作者", "通讯作者", "E-mail", "基金项目")):
        return True
    # Reject wrapped/multi-line affiliation strings (e.g. `3.北京新仁仁动物医院，北京昌平102200）`)
    if re.match(r"^\d+[.、．\-]", line) and (_AFFILIATION_KW_RE.search(line) or re.search(r"\d{6}", line)):
        return True
    if line.endswith(("）", ")")) and (
        _AFFILIATION_KW_RE.search(line) or re.search(r"\d{6}", line) or "；" in line or ";" in line
    ):
        return True
    # Body clauses containing mid-sentence comma/enumeration with long clauses (unlike short author names)
    if ("、" in line and len(line) > 20) or re.search(r"[\u4e00-\u9fff]{6,}[，,][\u4e00-\u9fff]{6,}", line):
        # Allow `长标题...作者1，作者2` only if the right side after comma is short (<= 6 chars per segment)
        parts = re.split(r"[，,]", line)
        if any(len(_cjk_chars(p)) > 6 for p in parts[1:]):
            return True
    if re.match(r"^\d+例", line) or "介绍如下" in line or "报告如下" in line:
        return True
    # Pure ASCII/English titles shouldn't split a Chinese journal article
    return not any("\u4e00" <= ch <= "\u9fff" for ch in line)


def _is_journal_or_received_line(raw_line: str) -> bool:
    if "收稿" in raw_line:
        return True
    if any(j in raw_line for j in _KNOWN_JOURNALS):
        return True
    return bool(_JOURNAL_HEADER_RE.search(raw_line))


def _find_article_anchors(
    entries: list[_LineEntry],
    queries: list[str],
) -> list[_ArticleAnchor]:
    anchors: list[_ArticleAnchor] = []
    in_references = False
    last_anchor_idx = -999
    last_page_pos = -1
    page_has_body_prose = False

    for idx, entry in enumerate(entries):
        if entry.page_pos != last_page_pos:
            last_page_pos = entry.page_pos
            page_has_body_prose = False

        raw_clean = entry.clean_line
        if _REFERENCES_HEADING_RE.match(raw_clean):
            in_references = True
            page_has_body_prose = False
            continue

        line = _strip_running_journal_prefix(raw_clean)
        if not line:
            continue

        is_body_or_section = bool(_BODY_SECTION_OR_PROSE_RE.search(line) or _FIG_TABLE_XREF_RE.search(line))

        if (
            _NON_TITLE_PREFIX_RE.match(line)
            or _looks_like_body_prose(line)
            or line.endswith("-")
            or len(line) < 4
            or len(line) > 90
        ):
            if is_body_or_section:
                page_has_body_prose = True
                if re.match(r"^\d{1,2}(?:[.．]\d{1,2})+\s*[\u4e00-\u9fff]", line):
                    in_references = False
            continue

        # Inspect next 4 non-header lines for article-start metadata
        lookahead: list[str] = []
        for j in range(idx + 1, min(len(entries), idx + 6)):
            la = _strip_running_journal_prefix(entries[j].clean_line)
            if la:
                lookahead.append(la)

        has_primary_meta = any(_PRIMARY_ARTICLE_META_RE.search(la) for la in lookahead[:4])
        has_article_no = any(_ARTICLE_NO_META_RE.search(la) for la in lookahead[:4])
        # `文章编号` alone may appear at the top of a right column on page 1 after left-column prose
        has_strong_meta = has_primary_meta or (
            has_article_no and not page_has_body_prose and entry.line_idx <= 8
        )

        if is_body_or_section:
            page_has_body_prose = True

        target_score = _score_title_match(line, queries)
        preceded_by_header_or_rec = any(
            entries[k].page_pos == entry.page_pos and _is_journal_or_received_line(entries[k].clean_line)
            for k in range(max(0, idx - 3), idx)
        )
        is_page_top = entry.line_idx <= 4
        followed_by_sec1_or_meta = any(
            re.match(r"^1\s+[\u4e00-\u9fff]", la)
            or _PRIMARY_ARTICLE_META_RE.search(la)
            or _ARTICLE_NO_META_RE.search(la)
            for la in lookahead[:2]
        )
        qualifies_by_target = (
            target_score >= 0.75
            and (is_page_top or preceded_by_header_or_rec)
            and followed_by_sec1_or_meta
            and not page_has_body_prose
        )

        if not (has_strong_meta or qualifies_by_target):
            continue

        if in_references and not (has_primary_meta or qualifies_by_target):
            continue

        if idx - last_anchor_idx >= 3:
            in_references = False
            page_has_body_prose = False
            last_anchor_idx = idx
            title_src = line
            if lookahead:
                next_la = lookahead[0]
                if (
                    not _PRIMARY_ARTICLE_META_RE.search(next_la)
                    and not _ARTICLE_NO_META_RE.search(next_la)
                    and not _NON_TITLE_PREFIX_RE.match(next_la)
                    and not _looks_like_body_prose(next_la)
                    and not re.search(r"[，,]\s*[\u4e00-\u9fff]{2,4}\d*", next_la)
                    and len(line) + len(next_la) <= 60
                ):
                    combined = f"{line}{next_la}"
                    if _score_title_match(combined, queries) > target_score or (
                        len(next_la) <= 16 and not re.search(r"\d", next_la)
                    ):
                        title_src = combined
            anchors.append(
                _ArticleAnchor(
                    entry_idx=idx,
                    page_pos=entry.page_pos,
                    line_idx=entry.line_idx,
                    title=_clean_candidate_title(title_src, queries),
                    has_strong_meta=has_strong_meta,
                    target_score=max(target_score, _score_title_match(title_src, queries)),
                )
            )

    # If target anchor was found on page >= 1 without an explicit anchor on page 0,
    # detect a standalone page-0 article title preceded by a journal header.
    if len(anchors) == 1 and anchors[0].page_pos > 0:
        for idx, entry in enumerate(entries):
            if entry.page_pos > 0:
                break
            line = _strip_running_journal_prefix(entry.clean_line)
            if (
                not line
                or line.startswith("【")
                or _NON_TITLE_PREFIX_RE.match(line)
                or _looks_like_body_prose(line)
                or line.endswith("-")
                or len(line) < 4
                or len(line) > 60
            ):
                continue
            preceded_by_journal = any(
                entries[k].page_pos == 0 and _is_journal_or_received_line(entries[k].clean_line)
                for k in range(max(0, idx - 3), idx)
            )
            if preceded_by_journal:
                anchors.insert(
                    0,
                    _ArticleAnchor(
                        entry_idx=idx,
                        page_pos=0,
                        line_idx=entry.line_idx,
                        title=_clean_candidate_title(line, queries),
                        has_strong_meta=False,
                        target_score=_score_title_match(line, queries),
                    ),
                )
                break

    return anchors


def _strip_reference_blocks(entries: list[_LineEntry]) -> list[_LineEntry]:
    """Strip contiguous `参考文献` citation spans without truncating subsequent clinical/plate sections."""
    kept: list[_LineEntry] = []
    i = 0
    n = len(entries)
    while i < n:
        entry = entries[i]
        if _REFERENCES_HEADING_RE.match(entry.clean_line) and i > 3:
            j = i + 1
            while j < n:
                cand = entries[j]
                if _RESUME_AFTER_REF_RE.match(cand.clean_line):
                    break
                if cand.page_pos != entries[j - 1].page_pos and not (
                    re.match(r"^\[\d+\]", cand.clean_line)
                    or re.search(r"(?:\[[JMC]\]|\d{4}\s*[,，]\s*\d+\s*\(\d+\))", cand.clean_line)
                ):
                    break
                j += 1
            i = j
            continue
        kept.append(entry)
        i += 1
    return kept


def _extract_plate_blocks_from_page(
    page_entries: list[_LineEntry],
) -> tuple[list[_LineEntry], list[tuple[str, list[_LineEntry]]]]:
    """Split a plate page into pre-plate entries and `[(plate_title, block_entries), ...]`."""
    pre_entries: list[_LineEntry] = []
    blocks: list[tuple[str, list[_LineEntry]]] = []
    current_title: str | None = None
    current_block: list[_LineEntry] = []

    for e in page_entries:
        m = _PLATE_HEADER_RE.match(e.clean_line)
        if m:
            if current_title is not None:
                blocks.append((current_title, current_block))
            current_title = m.group(1).strip()
            current_block = [e]
        elif current_title is not None:
            current_block.append(e)
        else:
            pre_entries.append(e)

    if current_title is not None:
        blocks.append((current_title, current_block))

    return pre_entries, blocks


def _filter_and_attach_figure_plates(
    article_entries: list[_LineEntry],
    tail_entries: list[_LineEntry],
    match_targets: list[str],
) -> list[_LineEntry]:
    """Keep matching `《target》图版` caption blocks and exclude unrelated `《other》图版` blocks."""
    # Group article_entries by page_pos while preserving relative order
    page_order: list[int] = []
    by_page: dict[int, list[_LineEntry]] = {}
    for e in article_entries:
        if e.page_pos not in by_page:
            page_order.append(e.page_pos)
            by_page[e.page_pos] = []
        by_page[e.page_pos].append(e)

    filtered: list[_LineEntry] = []
    seen_plate_pages: set[int] = set()
    for p_pos in page_order:
        p_entries = by_page[p_pos]
        if not any(_PLATE_HEADER_RE.match(e.clean_line) for e in p_entries):
            filtered.extend(p_entries)
            continue

        seen_plate_pages.add(p_pos)
        pre_entries, blocks = _extract_plate_blocks_from_page(p_entries)
        matching_blocks = [
            blk
            for title, blk in blocks
            if _score_title_match(title, match_targets) >= 0.70
        ]
        # Keep non-header pre-entries if any exist, or pre-entries when a matching block is present
        has_non_header_pre = any(_strip_running_journal_prefix(e.clean_line) for e in pre_entries)
        if has_non_header_pre or matching_blocks:
            filtered.extend(pre_entries)
        for blk in matching_blocks:
            filtered.extend(blk)

    # Also check tail_entries (pages after slice_end) for issue-end plate pages belonging to target article
    if tail_entries and match_targets:
        tail_by_page: dict[int, list[_LineEntry]] = {}
        tail_order: list[int] = []
        for e in tail_entries:
            if e.page_pos not in tail_by_page:
                tail_order.append(e.page_pos)
                tail_by_page[e.page_pos] = []
            tail_by_page[e.page_pos].append(e)

        for p_pos in tail_order:
            if p_pos in seen_plate_pages:
                continue
            p_entries = tail_by_page[p_pos]
            if not any(_PLATE_HEADER_RE.match(e.clean_line) for e in p_entries):
                continue
            _, blocks = _extract_plate_blocks_from_page(p_entries)
            for title, blk in blocks:
                if _score_title_match(title, match_targets) >= 0.70:
                    filtered.extend(blk)

    return filtered


def _format_page_range(page_numbers: list[int]) -> str:
    if not page_numbers:
        return ""
    if len(page_numbers) == 1:
        return str(page_numbers[0])
    return f"{page_numbers[0]}-{page_numbers[-1]}"


def _slice_entries_to_text(
    entries: list[_LineEntry],
    pages: list[PageArtifact],
) -> tuple[str, list[PageArtifact]]:
    """Group sliced entries by page and format with `--- PDF第N页 ---` markers."""
    by_page_pos: dict[int, list[str]] = {}
    for e in entries:
        by_page_pos.setdefault(e.page_pos, []).append(e.raw_line)

    sliced_pages: list[PageArtifact] = []
    chunks: list[str] = []
    for page_pos in sorted(by_page_pos.keys()):
        page = pages[page_pos]
        sliced_pages.append(page)
        chunks.append(f"--- PDF第{page.pdf_page_number}页 ---\n" + "\n".join(by_page_pos[page_pos]))
    return "\n\n".join(chunks), sliced_pages


def _filter_lab_rows_for_slice(
    sliced_pages: list[PageArtifact],
    case_text: str,
    is_shared_page_fn: set[int],
) -> list[LabTableRow]:
    norm_case = normalize_for_match(case_text)
    rows: list[LabTableRow] = []
    for page in sliced_pages:
        for row in page.lab_rows:
            if page.page_index in is_shared_page_fn:
                item_norm = normalize_for_match(row.item_raw)
                if item_norm and item_norm not in norm_case:
                    continue
            rows.append(row)
    return rows


def segment_journal_document(
    doc: DocumentRecord,
    pages: list[PageArtifact],
) -> list[CaseSegment]:
    """Segment a journal PDF (single-case, multi-case, or omnibus bound volume) into CaseSegments."""
    ordered_pages = sorted(pages, key=lambda p: p.page_index)
    stem = Path(doc.filename).stem.strip()
    queries: list[str] = []
    if doc.target_article_query and doc.target_article_query.strip():
        queries.append(doc.target_article_query.strip())
    if stem:
        queries.append(stem)
        stripped_stem = re.sub(r"(?:的)?(?:病例报告|诊治病例|病理诊断报告病例|病例)$", "", stem).strip()
        if len(stripped_stem) >= 4 and stripped_stem not in queries:
            queries.append(stripped_stem)

    entries = _build_line_entries(ordered_pages)
    if not entries:
        page_nums = [p.pdf_page_number for p in ordered_pages]
        return [
            CaseSegment(
                case_id=f"{doc.document_id}__case_001",
                document_id=doc.document_id,
                source_filename=doc.filename,
                source_type=SourceType.JOURNAL,
                case_order=1,
                article_title=stem,
                title=stem,
                page_indices=[p.page_index for p in ordered_pages],
                page_range=_format_page_range(page_nums),
                image_paths=[p.image_path for p in ordered_pages if p.image_path],
                raw_text="",
                boundary_uncertain=True,
                boundary_notes=["Document contains no extracted text; OCR or manual review required."],
            )
        ]

    all_clean_lines = [e.clean_line for e in entries]
    global_journal, global_pub_year = _extract_journal_and_pub_year(all_clean_lines)
    anchors = _find_article_anchors(entries, queries)

    boundary_uncertain = False
    boundary_notes: list[str] = []

    slice_start = 0
    slice_end = len(entries)
    extracted_title = ""

    if len(anchors) >= 2:
        # Score anchors primarily against target_article_query if set, else filename stem
        primary_queries = [doc.target_article_query.strip()] if doc.target_article_query and doc.target_article_query.strip() else queries
        scored = [(anchor, _score_title_match(anchor.title, primary_queries)) for anchor in anchors]
        max_score = max(s for _, s in scored)
        matching = [a for a, s in scored if s >= 0.75 and s >= max_score - 0.05]

        if len(matching) == 1:
            target_anchor = matching[0]
            anchor_pos = anchors.index(target_anchor)
            slice_start = target_anchor.entry_idx
            if anchor_pos + 1 < len(anchors):
                slice_end = anchors[anchor_pos + 1].entry_idx
            extracted_title = target_anchor.title
            boundary_notes.append(
                f"Omnibus PDF sliced to target article '{extracted_title}' (from {len(anchors)} detected articles)."
            )
        elif len(matching) > 1:
            target_anchor = matching[0]
            anchor_pos = anchors.index(target_anchor)
            slice_start = target_anchor.entry_idx
            if anchor_pos + 1 < len(anchors):
                slice_end = anchors[anchor_pos + 1].entry_idx
            extracted_title = target_anchor.title
            boundary_uncertain = True
            boundary_notes.append(
                f"Multiple article headers ({len(matching)}) matched target query ambiguously; selected first match."
            )
        else:
            boundary_uncertain = True
            extracted_title = anchors[0].title
            boundary_notes.append(
                f"Omnibus PDF contains {len(anchors)} articles, none clearly matching target query/filename '{primary_queries[0] if primary_queries else doc.filename}'."
            )
    elif len(anchors) == 1:
        target_anchor = anchors[0]
        slice_start = target_anchor.entry_idx
        extracted_title = target_anchor.title
        if doc.target_article_query and _score_title_match(target_anchor.title, [doc.target_article_query]) < 0.7:
            boundary_uncertain = True
            boundary_notes.append(
                f"Target article query '{doc.target_article_query}' did not closely match detected title '{extracted_title}'."
            )
        elif not doc.target_article_query:
            core_stem = _core_topic_cjk(stem)
            core_title = _core_topic_cjk(extracted_title)
            if (
                len(core_stem) >= 3
                and len(core_title) >= 3
                and len(set(core_stem) & set(core_title)) < 2
            ):
                boundary_uncertain = True
                boundary_notes.append(
                    f"Extracted article title '{extracted_title}' has minimal overlap with filename '{doc.filename}' (possible crosstalk)."
                )
    else:
        if doc.target_article_query:
            full_norm = normalize_for_match(" ".join(all_clean_lines))
            if normalize_for_match(doc.target_article_query) not in full_norm:
                boundary_uncertain = True
                boundary_notes.append(
                    f"Target article query '{doc.target_article_query}' not found in document text."
                )

    article_entries = entries[slice_start:slice_end]
    tail_entries = entries[slice_end:] if slice_end < len(entries) else []
    article_entries = _strip_reference_blocks(article_entries)
    match_targets = list(dict.fromkeys(([extracted_title] if extracted_title else []) + queries))
    article_entries = _filter_and_attach_figure_plates(article_entries, tail_entries, match_targets)

    article_lines = [e.clean_line for e in article_entries]
    slice_journal, slice_pub_year = _extract_journal_and_pub_year(article_lines)
    source_journal = slice_journal or global_journal
    publication_year = slice_pub_year or global_pub_year
    received_year = _extract_received_year(article_lines)
    if not received_year and 0 < slice_start < len(entries):
        start_page_pos = entries[slice_start].page_pos
        pre_header_lines = [
            entries[k].clean_line
            for k in range(max(0, slice_start - 3), slice_start)
            if entries[k].page_pos == start_page_pos
        ]
        received_year = _extract_received_year(pre_header_lines)
    article_title = extracted_title or stem

    # Check for 1-article-multi-case headings inside article_entries (stop scanning at 讨论/参考文献)
    case_starts: list[tuple[int, int]] = []
    seen_case_nums: set[int] = set()
    for rel_i, e in enumerate(article_entries):
        if _DISCUSSION_OR_REF_RE.match(e.clean_line) and case_starts:
            break
        if _MULTI_CASE_FALSE_PREFIX_RE.match(e.clean_line):
            continue
        m = _MULTI_CASE_RE.match(e.clean_line)
        if m:
            num = _parse_case_num(m.group(1))
            if num is not None and num not in seen_case_nums:
                seen_case_nums.add(num)
                case_starts.append((rel_i, num))

    if len(case_starts) < 2:
        raw_text, sliced_pages = _slice_entries_to_text(article_entries, ordered_pages)
        page_nums = [p.pdf_page_number for p in sliced_pages]
        return [
            CaseSegment(
                case_id=f"{doc.document_id}__case_001",
                document_id=doc.document_id,
                source_filename=doc.filename,
                source_type=SourceType.JOURNAL,
                case_order=1,
                source_journal=source_journal,
                publication_year=publication_year,
                received_year=received_year,
                article_title=article_title,
                title=article_title,
                page_indices=[p.page_index for p in sliced_pages],
                page_range=_format_page_range(page_nums),
                image_paths=[p.image_path for p in sliced_pages if p.image_path],
                raw_text=raw_text,
                lab_rows=[row for p in sliced_pages for row in p.lab_rows],
                boundary_uncertain=boundary_uncertain,
                boundary_notes=boundary_notes,
            )
        ]

    # Multi-case splitting: slice article_entries into N segments
    # Trim trailing 讨论/参考文献 after the last case start if a top-level discussion follows
    end_limit = len(article_entries)
    last_case_start = case_starts[-1][0]
    for rel_i in range(last_case_start + 1, len(article_entries)):
        if _DISCUSSION_OR_REF_RE.match(article_entries[rel_i].clean_line):
            end_limit = rel_i
            break

    # Detect pages shared by multiple cases so lab rows can be filtered cleanly
    segments: list[CaseSegment] = []
    case_entry_slices: list[tuple[int, int, list[_LineEntry]]] = []
    page_usage_counts: dict[int, int] = {}

    for idx, (start_pos, case_num) in enumerate(case_starts):
        stop_pos = case_starts[idx + 1][0] if idx + 1 < len(case_starts) else end_limit
        sub_entries = article_entries[start_pos:stop_pos]
        case_entry_slices.append((idx + 1, case_num, sub_entries))
        for p_idx in {e.page_index for e in sub_entries}:
            page_usage_counts[p_idx] = page_usage_counts.get(p_idx, 0) + 1

    shared_pages = {p_idx for p_idx, cnt in page_usage_counts.items() if cnt > 1}

    for order, case_num, sub_entries in case_entry_slices:
        raw_text, sliced_pages = _slice_entries_to_text(sub_entries, ordered_pages)
        page_nums = [p.pdf_page_number for p in sliced_pages]
        case_title = f"{article_title}（病例{case_num}）"
        segments.append(
            CaseSegment(
                case_id=f"{doc.document_id}__case_{order:03d}",
                document_id=doc.document_id,
                source_filename=doc.filename,
                source_type=SourceType.JOURNAL,
                case_order=order,
                source_journal=source_journal,
                publication_year=publication_year,
                received_year=received_year,
                article_title=article_title,
                case_number=case_num,
                title=case_title,
                page_indices=[p.page_index for p in sliced_pages],
                page_range=_format_page_range(page_nums),
                image_paths=[p.image_path for p in sliced_pages if p.image_path],
                raw_text=raw_text,
                lab_rows=_filter_lab_rows_for_slice(sliced_pages, raw_text, shared_pages),
                boundary_uncertain=boundary_uncertain,
                boundary_notes=list(boundary_notes),
            )
        )

    return segments
