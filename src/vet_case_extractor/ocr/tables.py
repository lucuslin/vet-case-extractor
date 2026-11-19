"""Laboratory table row extraction and OCR trap anomaly detection."""

from __future__ import annotations

import re

from vet_case_extractor.config import ExtractionRules
from vet_case_extractor.models import BBox, LabTableRow, TextBlock

_SUPERSCRIPT_TRANS = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹", "0123456789")

_FLAG_TOKENS = {"↑", "↓", "↑↑", "↓↓", "H", "L", "HIGH", "LOW", "高", "低", "偏高", "偏低"}
_QUALITATIVE_VALUES = {
    "阴性",
    "阳性",
    "弱阳性",
    "强阳性",
    "+",
    "-",
    "±",
    "++",
    "+++",
    "正常",
    "未见",
    "未检出",
    "normal",
    "negative",
    "positive",
    "trace",
}

_UNIT_PATTERN = (
    r"(?:"
    r"[×xX*]?\s*10\s*(?:[\^＊*]?\s*(?:9|12|⁹|¹²)|[%个])(?:\s*/\s*[Ll个])?"
    r"|mg\s*/\s*(?:d[Ll]|m[Ll]|[Ll])"
    r"|[uμµ]g\s*/\s*(?:d[Ll]|m[Ll]|[Ll])"
    r"|ng\s*/\s*m[Ll]"
    r"|pg\s*/\s*m[Ll]"
    r"|g\s*/\s*(?:d[Ll]|[Ll])"
    r"|[mμuµnp]?mol\s*/\s*[Ll]"
    r"|mEq\s*/\s*[Ll]"
    r"|mOsm\s*/\s*kg"
    r"|mm\s*/\s*(?:min|h)"
    r"|mmHg"
    r"|[KkMm]\s*/\s*[uμµ][Ll]"
    r"|[GT]\s*/\s*[Ll]"
    r"|I?U\s*/\s*[Ll]"
    r"|/[HhLl][Pp][Ff]"
    r"|f[Ll]|pg|%|sec|s|秒|个\s*/\s*[Ll]"
    r")"
)

_UNIT_RE = re.compile(rf"^{_UNIT_PATTERN}$", re.IGNORECASE)
_UNIT_SEARCH_RE = re.compile(_UNIT_PATTERN, re.IGNORECASE)

_RANGE_RE = re.compile(
    r"[\(（]?\s*(?:参考(?:范围|区间|值)?\s*[:：]?\s*)?"
    r"(\d+(?:\.\d+)?)\s*[~～\-—－]\s*(\d+(?:\.\d+)?)"
    rf"(?:\s*({_UNIT_PATTERN}))?"
    r"\s*[\)）]?"
)
_ONE_SIDED_RANGE_RE = re.compile(
    r"[\(（]?\s*(?:参考(?:范围|区间|值)?\s*[:：]?\s*)?"
    r"([<>≤≥]=?)\s*(\d+(?:\.\d+)?)"
    rf"(?:\s*({_UNIT_PATTERN}))?"
    r"\s*[\)）]?"
)
_VALUE_TOKEN_RE = re.compile(
    r"^([↑↓+\-<>≤≥]*)\s*(\d+(?:\.\d+)?)\s*([%↑↓HhLl]?)$"
)
_EXP_RE = re.compile(r"10\s*(?:[\^＊*]?\s*(9|12)|([⁹¹²]+))")
_UNIT_CORRUPT_RE = re.compile(
    r"(?:[×xX*]?\s*10\s*%\s*/\s*[Ll]|10\s*个|个\s*/\s*[Ll]|/\s*个)",
    re.IGNORECASE,
)
_TABLE_HEADING_RE = re.compile(r"^(?:表\s*\d+|Table\s*\d+|检验结果|实验室检查|化验结果)")
_COL_HEADER_KEYWORDS = ("项目", "指标", "名称", "测定值", "结果", "检测值", "单位", "参考范围", "参考值")

_WBC_LINEAGE_ITEMS = {
    "WBC",
    "白细胞",
    "PLT",
    "血小板",
    "NEU",
    "NEUT",
    "中性粒细胞",
    "LYM",
    "LYMPH",
    "淋巴细胞",
    "MONO",
    "单核细胞",
    "EOS",
    "嗜酸性粒细胞",
    "BASO",
    "嗜碱性粒细胞",
}
_RBC_ITEMS = {"RBC", "红细胞"}
_HGB_ITEMS = {"HGB", "HB", "血红蛋白"}

_KNOWN_LAB_HINTS = {
    "WBC",
    "RBC",
    "HGB",
    "HB",
    "HCT",
    "PCV",
    "PLT",
    "MCV",
    "MCH",
    "MCHC",
    "RDW",
    "MPV",
    "NEU",
    "NEUT",
    "LYM",
    "LYMPH",
    "MONO",
    "EOS",
    "BASO",
    "RETIC",
    "ALT",
    "AST",
    "ALP",
    "ALKP",
    "GGT",
    "GCT",
    "TBIL",
    "DBIL",
    "TP",
    "TB",
    "ALB",
    "GLOB",
    "BUN",
    "UREA",
    "CREA",
    "CRE",
    "GLU",
    "TG",
    "TGA",
    "CHOL",
    "TC",
    "CA",
    "PHOS",
    "MG",
    "MA",
    "NA",
    "CL",
    "AMY",
    "AMYL",
    "LIPA",
    "CK",
    "LDH",
    "SDMA",
    "CRP",
    "SAA",
    "PT",
    "APTT",
    "白细胞",
    "红细胞",
    "血红蛋白",
    "红细胞压积",
    "血细胞比容",
    "血小板",
    "丙氨酸氨基转移酶",
    "谷丙转氨酶",
    "天门冬氨酸氨基转移酶",
    "谷草转氨酶",
    "碱性磷酸酶",
    "总蛋白",
    "白蛋白",
    "球蛋白",
    "总胆红素",
    "尿素氮",
    "尿素",
    "肌酐",
    "血糖",
    "甘油三酯",
    "总胆固醇",
    "胆固醇",
    "血钙",
    "血磷",
    "血镁",
    "血钾",
    "血钠",
    "血氯",
    "淀粉酶",
    "脂肪酶",
}


def _norm_unit_for_compare(unit: str) -> str:
    s = unit.translate(_SUPERSCRIPT_TRANS).strip()
    s = re.sub(r"\s+", "", s)
    s = s.replace("μ", "u").replace("µ", "u").replace("×", "*").replace("x", "*").replace("X", "*")
    s = s.replace("^", "")
    return s.lower()


def _extract_exponent(text: str) -> int | None:
    if not text:
        return None
    normalized = text.translate(_SUPERSCRIPT_TRANS)
    m = _EXP_RE.search(normalized)
    if not m:
        return None
    exp_str = m.group(1) or m.group(2)
    if exp_str in ("9", "12"):
        return int(exp_str)
    return None


def _parse_numeric_value(value_raw: str) -> tuple[float | None, str]:
    s = value_raw.strip()
    if not s:
        return None, ""
    m = re.search(r"(\d+(?:\.\d+)?)", s)
    if not m:
        return None, ""
    num_str = m.group(1)
    try:
        return float(num_str), num_str
    except ValueError:
        return None, ""


def _parse_ref_range(ref_range_raw: str) -> tuple[float | None, float | None, str]:
    if not ref_range_raw.strip():
        return None, None, ""
    m = _RANGE_RE.search(ref_range_raw)
    if m:
        low = float(m.group(1))
        high = float(m.group(2))
        unit = (m.group(3) or "").strip()
        return low, high, unit
    m2 = _ONE_SIDED_RANGE_RE.search(ref_range_raw)
    if m2:
        op = m2.group(1)
        val = float(m2.group(2))
        unit = (m2.group(3) or "").strip()
        if op.startswith("<"):
            return 0.0, val, unit
        return val, None, unit
    return None, None, ""


def _is_table_heading(text: str, rules: ExtractionRules) -> str | None:
    stripped = text.strip()
    if not stripped or len(stripped) > 40:
        return None
    for heading in rules.book_lab_table_headings:
        if heading in stripped and "。" not in stripped:
            return stripped
    if _TABLE_HEADING_RE.match(stripped) and "。" not in stripped:
        return stripped
    return None


def _is_col_header_line(text: str) -> bool:
    hits = sum(1 for kw in _COL_HEADER_KEYWORDS if kw in text)
    return hits >= 2


def _looks_like_analyte_name(text: str) -> bool:
    s = text.strip()
    if not s or len(s) > 32:
        return False
    if any(p in s for p in ("。", "；", "：", "为", "提示", "符合", "怀疑")):
        return False
    # Pure number is not an analyte name
    if re.fullmatch(r"[\d.\-~～%]+", s):
        return False
    return bool(re.search(r"[A-Za-z\u4e00-\u9fff]", s))


def _is_known_analyte(item_raw: str) -> bool:
    upper = item_raw.upper()
    for token in re.findall(r"[A-Za-z]+|[\u4e00-\u9fff]+", upper):
        if token in _KNOWN_LAB_HINTS:
            return True
    return any(hint in item_raw for hint in _KNOWN_LAB_HINTS if not hint.isascii())


def _parse_tokens_to_row(
    tokens: list[str],
    page_index: int,
    bbox: BBox | None,
    table_title: str,
    in_table_region: bool,
) -> LabTableRow | None:
    cleaned_tokens = [t.strip() for t in tokens if t and t.strip()]
    if len(cleaned_tokens) < 2:
        return None

    # Identify item_raw (leading non-numeric tokens unless table has shifted numeric column)
    # First check if a token has a range
    joined = " ".join(cleaned_tokens)
    if _is_col_header_line(joined):
        return None
    if len(joined) > 100 or "。" in joined:
        return None

    item_parts: list[str] = []
    idx = 0
    while idx < len(cleaned_tokens):
        tok = cleaned_tokens[idx]
        if _VALUE_TOKEN_RE.match(tok) or _RANGE_RE.match(tok) or _ONE_SIDED_RANGE_RE.match(tok):
            break
        # Check if value+unit are merged in one token like "15.2×10⁹/L" or "250U/L"
        if re.match(r"^[↑↓+\-<>≤≥]*\d+(?:\.\d+)?\s*(?:%|×|x|X|\*|[A-Za-zμµ个/])", tok):
            break
        if tok in _QUALITATIVE_VALUES and idx > 0:
            break
        if tok in _FLAG_TOKENS and idx > 0:
            break
        item_parts.append(tok)
        idx += 1

    # Handle column-shifted case where the first token itself is numeric inside an active table
    if not item_parts and in_table_region and _VALUE_TOKEN_RE.match(cleaned_tokens[0]):
        item_raw = cleaned_tokens[0]
        rest = cleaned_tokens[1:]
    elif item_parts:
        item_raw = " ".join(item_parts)
        rest = cleaned_tokens[idx:]
    else:
        return None

    if not rest:
        return None

    value_raw = ""
    unit_raw = ""
    ref_range_raw = ""
    flag_raw = ""

    rest_joined = " ".join(rest)

    # Extract reference range substring first if present
    range_match = _RANGE_RE.search(rest_joined) or _ONE_SIDED_RANGE_RE.search(rest_joined)
    if range_match:
        ref_range_raw = range_match.group(0).strip()
        # Clean wrapping parentheses only if matched around pure range? Keep raw substring!
        before_range = rest_joined[: range_match.start()].strip()
        after_range = rest_joined[range_match.end() :].strip()
        leftover = f"{before_range} {after_range}".strip()
    else:
        leftover = rest_joined

    # Check flags in leftover
    leftover_tokens = leftover.split()
    kept_leftover: list[str] = []
    for tok in leftover_tokens:
        if tok in _FLAG_TOKENS or tok.upper() in _FLAG_TOKENS:
            flag_raw = f"{flag_raw} {tok}".strip() if flag_raw else tok
        else:
            kept_leftover.append(tok)

    # Parse value and unit from kept_leftover
    for tok in kept_leftover:
        if not value_raw:
            m_val_unit = re.match(
                rf"^([↑↓]?)\s*(\d+(?:\.\d+)?)\s*([↑↓HhLl]?)\s*({_UNIT_PATTERN})?$",
                tok,
                re.IGNORECASE,
            )
            if m_val_unit:
                lead_flag, num, trail_flag, inline_unit = m_val_unit.groups()
                value_raw = num
                if lead_flag:
                    flag_raw = f"{lead_flag} {flag_raw}".strip() if flag_raw else lead_flag
                if trail_flag:
                    flag_raw = f"{flag_raw} {trail_flag}".strip() if flag_raw else trail_flag
                if inline_unit and not unit_raw:
                    unit_raw = inline_unit.strip()
                continue
            if tok in _QUALITATIVE_VALUES or tok.lower() in _QUALITATIVE_VALUES:
                value_raw = tok
                continue

        if _UNIT_RE.match(tok) or _UNIT_CORRUPT_RE.search(tok):
            unit_raw = f"{unit_raw} {tok}".strip() if unit_raw else tok
        elif not unit_raw and _UNIT_SEARCH_RE.search(tok):
            unit_raw = tok
        elif not value_raw:
            value_raw = tok
        elif not unit_raw:
            unit_raw = tok

    # Must have either a valid lab structure (ref_range or unit or in_table_region with value)
    if not value_raw and not ref_range_raw:
        return None
    if not in_table_region:
        has_num_val = _parse_numeric_value(value_raw)[0] is not None
        if not has_num_val:
            return None
        if not ref_range_raw and not unit_raw:
            return None
        if not _is_known_analyte(item_raw) and not (ref_range_raw and unit_raw):
            return None
        if not _looks_like_analyte_name(item_raw):
            return None

    return LabTableRow(
        item_raw=item_raw,
        value_raw=value_raw,
        unit_raw=unit_raw,
        ref_range_raw=ref_range_raw,
        flag_raw=flag_raw,
        page_index=page_index,
        bbox=bbox,
        table_title=table_title,
        anomalies=[],
    )


def _parse_line_to_row(
    line: str,
    page_index: int,
    bbox: BBox | None,
    table_title: str,
    in_table_region: bool,
) -> LabTableRow | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("[["):
        return None

    # 1. Pipe-delimited format
    if "|" in stripped:
        parts = [p.strip() for p in stripped.split("|")]
        if len(parts) >= 3 and parts[0]:
            item_raw = parts[0]
            value_raw = parts[1] if len(parts) > 1 else ""
            unit_raw = parts[2] if len(parts) > 2 else ""
            ref_range_raw = parts[3] if len(parts) > 3 else ""
            flag_raw = parts[4] if len(parts) > 4 else ""
            if _is_col_header_line(stripped):
                return None
            return LabTableRow(
                item_raw=item_raw,
                value_raw=value_raw,
                unit_raw=unit_raw,
                ref_range_raw=ref_range_raw,
                flag_raw=flag_raw,
                page_index=page_index,
                bbox=bbox,
                table_title=table_title,
                anomalies=[],
            )

    # 2. Tab or multi-space columns
    if "\t" in stripped or re.search(r" {2,}", stripped):
        cols = [c.strip() for c in re.split(r"\t+| {2,}", stripped) if c.strip()]
        row = _parse_tokens_to_row(cols, page_index, bbox, table_title, in_table_region)
        if row is not None:
            return row

    # 3. Single-space tokens
    tokens = stripped.split()
    row = _parse_tokens_to_row(tokens, page_index, bbox, table_title, in_table_region)
    if row is not None:
        return row

    # 4. Space-collapsed OCR tokens (e.g. "PCV134%24~45%" or "WBC15.2×10⁹/L 5.5~19.5")
    range_match = _RANGE_RE.search(stripped) or _ONE_SIDED_RANGE_RE.search(stripped)
    if range_match:
        before_range = stripped[: range_match.start()].strip()
        range_str = range_match.group(0).strip()
        after_range = stripped[range_match.end() :].strip()
        m_compact = re.match(
            r"^([A-Za-z\u4e00-\u9fff()（）/\-]+?)\s*([↑↓]?\d+(?:\.\d+)?.*)$",
            before_range,
        )
        if m_compact:
            item_part, val_part = m_compact.group(1).strip(), m_compact.group(2).strip()
            compact_tokens = [item_part, val_part, range_str]
            if after_range:
                compact_tokens.extend(after_range.split())
            return _parse_tokens_to_row(
                compact_tokens,
                page_index,
                bbox,
                table_title,
                in_table_region,
            )

    return None


def _merge_bboxes(blocks: list[TextBlock]) -> BBox | None:
    if not blocks:
        return None
    return BBox(
        x0=min(b.bbox.x0 for b in blocks),
        y0=min(b.bbox.y0 for b in blocks),
        x1=max(b.bbox.x1 for b in blocks),
        y1=max(b.bbox.y1 for b in blocks),
    )


def _detect_row_anomalies(row: LabTableRow, rules: ExtractionRules) -> list[str]:
    anomalies: list[str] = []
    item_upper = row.item_raw.upper()
    val, num_str = _parse_numeric_value(row.value_raw)
    low, high, ref_unit = _parse_ref_range(row.ref_range_raw)

    # 1. OCR unit corruption: ×10%/L or 个
    combined_unit_val = f"{row.value_raw} {row.unit_raw} {row.ref_range_raw}"
    if _UNIT_CORRUPT_RE.search(combined_unit_val):
        anomalies.append("ocr_unit_corruption: suspicious '×10%/L' or '个' in unit/value")

    # 2. Look-alike abbreviations from rules.ocr_known_typo_candidates
    for candidate, suggestion in rules.ocr_known_typo_candidates.items():
        if candidate.isascii():
            pattern = re.compile(rf"(?<![A-Za-z0-9]){re.escape(candidate)}(?![A-Za-z0-9])")
            matched = bool(pattern.search(row.item_raw))
        else:
            matched = candidate in row.item_raw
        if matched:
            anomalies.append(f"look_alike_abbreviation: {candidate}->{suggestion}")

    # 3. ×10⁹ vs ×10¹² magnitude conflict
    unit_exp = _extract_exponent(row.unit_raw)
    ref_exp = _extract_exponent(row.ref_range_raw)
    if unit_exp is not None and ref_exp is not None and unit_exp != ref_exp:
        anomalies.append(
            f"magnitude_exponent_conflict: unit 10^{unit_exp} vs ref_range 10^{ref_exp}"
        )
    elif unit_exp is not None:
        item_tokens = set(re.findall(r"[A-Za-z]+|[\u4e00-\u9fff]+", item_upper))
        if (item_tokens & _WBC_LINEAGE_ITEMS) and unit_exp == 12:
            anomalies.append(
                "magnitude_exponent_conflict: leukocyte/platelet item has ×10¹² instead of ×10⁹"
            )
        elif (item_tokens & _RBC_ITEMS) and unit_exp == 9:
            anomalies.append(
                "magnitude_exponent_conflict: RBC item has ×10⁹ instead of ×10¹²"
            )

    # 4. Unit mismatch between unit_raw and ref_range unit or incompatible analyte unit
    if row.unit_raw and ref_unit:
        nu = _norm_unit_for_compare(row.unit_raw)
        nr = _norm_unit_for_compare(ref_unit)
        if nu and nr and nu != nr:
            anomalies.append(f"unit_mismatch: '{row.unit_raw}' vs ref_range '{ref_unit}'")
    if row.unit_raw and low is not None and high is not None:
        nu = _norm_unit_for_compare(row.unit_raw)
        item_tokens = set(re.findall(r"[A-Za-z]+|[\u4e00-\u9fff]+", item_upper))
        if (item_tokens & _HGB_ITEMS) and nu == "g/dl" and high >= 50:
            anomalies.append("unit_mismatch: HGB unit 'g/dL' conflicts with g/L-scale ref_range")
        elif (item_tokens & _HGB_ITEMS) and nu == "g/l" and high <= 25:
            anomalies.append("unit_mismatch: HGB unit 'g/L' conflicts with g/dL-scale ref_range")
        elif "ALB" in item_tokens and nu == "g/dl" and high >= 15:
            anomalies.append("unit_mismatch: ALB unit 'g/dL' conflicts with g/L-scale ref_range")

    # 5. Leading '1' artifact (e.g. HGB 111.5, PCV 134%)
    is_leading_one = False
    if val is not None and num_str.startswith("1"):
        int_part = num_str.split(".")[0]
        if len(int_part) >= 2 and num_str[1] != ".":
            stripped_val = float(num_str[1:])
            has_percent = "%" in row.unit_raw or "%" in row.value_raw or "%" in row.ref_range_raw
            if has_percent and val > 100.0 and stripped_val <= 100.0:
                anomalies.append(
                    f"leading_one_artifact: percentage {row.value_raw} exceeds 100% (without leading 1: {num_str[1:]})"
                )
                is_leading_one = True
            elif (
                high is not None
                and low is not None
                and val > high
                and low <= stripped_val <= high
            ):
                anomalies.append(
                    f"leading_one_artifact: value {row.value_raw} falls inside ref_range without leading 1 ({num_str[1:]})"
                )
                is_leading_one = True
            elif (
                high is None
                and set(re.findall(r"[A-Za-z]+|[\u4e00-\u9fff]+", item_upper)) & _HGB_ITEMS
                and _norm_unit_for_compare(row.unit_raw) == "g/dl"
                and val > 30.0
                and 5.0 <= stripped_val <= 25.0
            ):
                anomalies.append(
                    f"leading_one_artifact: HGB {row.value_raw} g/dL suspicious leading 1 ({num_str[1:]})"
                )
                is_leading_one = True

    # 6. Missing decimal / extreme ratio vs ref_range
    if low is not None and high is not None and low > high:
        anomalies.append(f"ref_range_anomaly: inverted range '{row.ref_range_raw}'")
    elif val is not None and low is not None and high is not None and not is_leading_one:
        if high > 0 and val >= high * 5.0:
            if "." not in num_str and (low <= val / 10.0 <= high or low <= val / 100.0 <= high):
                anomalies.append(
                    f"missing_decimal_or_extreme_ratio: {row.value_raw} vs ref_range {row.ref_range_raw}"
                )
            else:
                anomalies.append(
                    f"extreme_ratio_vs_ref_range: {row.value_raw} vs ref_range {row.ref_range_raw} ({val / high:.1f}x upper limit)"
                )
        elif low > 0 and val > 0 and val <= low / 5.0:
            anomalies.append(
                f"missing_decimal_or_extreme_ratio: {row.value_raw} vs ref_range {row.ref_range_raw}"
            )

    # 7. Column/row misalignment at row level
    if re.fullmatch(r"[\d.\-~～]+", row.item_raw.strip()):
        anomalies.append("column_row_misalignment: numeric value in item_raw column")
    if not row.value_raw.strip() and row.ref_range_raw.strip():
        anomalies.append("column_row_misalignment: missing value_raw with ref_range present")
    elif (
        row.value_raw.strip()
        and val is None
        and row.value_raw.strip().lower() not in _QUALITATIVE_VALUES
        and low is not None
    ):
        anomalies.append("column_row_misalignment: non-numeric value_raw with numeric ref_range")

    return anomalies


def _detect_cross_row_shifts(rows: list[LabTableRow]) -> None:
    parsed_refs = [_parse_ref_range(r.ref_range_raw)[:2] for r in rows]
    parsed_vals = [_parse_numeric_value(r.value_raw)[0] for r in rows]

    for i, row in enumerate(rows):
        val_i = parsed_vals[i]
        low_i, high_i = parsed_refs[i]
        if val_i is None or low_i is None or high_i is None:
            continue
        if low_i <= val_i <= high_i:
            continue

        # Check if val_i fits adjacent row j while row j's value does not fit row j
        for j in (i - 1, i + 1):
            if not (0 <= j < len(rows)):
                continue
            val_j = parsed_vals[j]
            low_j, high_j = parsed_refs[j]
            if low_j is None or high_j is None or val_j is None:
                continue
            if (
                not (low_j <= val_j <= high_j)
                and (low_j <= val_i <= high_j)
                and (high_i < low_j or high_j < low_i)
            ):
                msg = (
                    f"column_row_misalignment: value {row.value_raw} fits adjacent row "
                    f"'{rows[j].item_raw}' ref_range ({rows[j].ref_range_raw})"
                )
                if msg not in row.anomalies:
                    row.anomalies.append(msg)
                break


def _extract_from_blocks(
    blocks: list[TextBlock],
    page_index: int,
    rules: ExtractionRules,
) -> list[LabTableRow]:
    non_empty = [b for b in blocks if b.text and b.text.strip()]
    if not non_empty:
        return []

    # Cluster blocks into spatial horizontal bands by y_mid proximity
    heights = sorted(b.bbox.height for b in non_empty if b.bbox.height > 0)
    median_h = heights[len(heights) // 2] if heights else 14.0
    row_tol = max(4.0, median_h * 0.55)

    y_sorted = sorted(non_empty, key=lambda b: (0.5 * (b.bbox.y0 + b.bbox.y1), b.bbox.x0))
    spatial_rows: list[list[TextBlock]] = []
    for blk in y_sorted:
        blk_ymid = 0.5 * (blk.bbox.y0 + blk.bbox.y1)
        if not spatial_rows:
            spatial_rows.append([blk])
            continue
        prev_ymid = sum(0.5 * (b.bbox.y0 + b.bbox.y1) for b in spatial_rows[-1]) / len(
            spatial_rows[-1]
        )
        if abs(blk_ymid - prev_ymid) <= row_tol:
            spatial_rows[-1].append(blk)
        else:
            spatial_rows.append([blk])

    extracted: list[LabTableRow] = []
    current_table_title = ""
    in_table_region = False

    for band in spatial_rows:
        band.sort(key=lambda b: b.bbox.x0)
        if len(band) == 1:
            blk = band[0]
            lines = [ln.strip() for ln in blk.text.splitlines() if ln.strip()]
            for ln in lines:
                heading = _is_table_heading(ln, rules)
                if heading:
                    current_table_title = heading
                    in_table_region = True
                    continue
                if _is_col_header_line(ln):
                    in_table_region = True
                    continue
                row = _parse_line_to_row(
                    ln,
                    page_index=page_index,
                    bbox=blk.bbox,
                    table_title=current_table_title,
                    in_table_region=in_table_region,
                )
                if row is not None:
                    extracted.append(row)
                elif in_table_region and ("。" in ln or len(ln) > 55):
                    in_table_region = False
                    current_table_title = ""
        else:
            # Multi-block horizontal band: check if blocks are individual cells of one row
            # vs two separate side-by-side column rows (e.g., left column + right column).
            single_block_rows = [
                _parse_line_to_row(
                    b.text.strip(),
                    page_index=page_index,
                    bbox=b.bbox,
                    table_title=current_table_title,
                    in_table_region=in_table_region,
                )
                for b in band
                if "\n" not in b.text.strip()
            ]
            # If >=2 blocks independently form full lab rows, keep them separate!
            if sum(1 for r in single_block_rows if r is not None) < 2:
                band_text = " ".join(b.text.strip() for b in band)
                heading = _is_table_heading(band_text, rules)
                if heading:
                    current_table_title = heading
                    in_table_region = True
                    continue
                if _is_col_header_line(band_text):
                    in_table_region = True
                    continue

                first_tok = band[0].text.strip()
                first_has_value = bool(re.search(r"\d+(?:\.\d+)?", first_tok)) and (
                    _parse_line_to_row(
                        first_tok,
                        page_index=page_index,
                        bbox=band[0].bbox,
                        table_title=current_table_title,
                        in_table_region=False,
                    )
                    is not None
                )
                can_merge_band = (
                    not first_has_value
                    and _looks_like_analyte_name(first_tok)
                    and (
                        in_table_region
                        or _is_known_analyte(first_tok)
                        or bool(_RANGE_RE.search(band_text))
                    )
                )
                if can_merge_band:
                    cell_tokens = [b.text.strip() for b in band if b.text.strip()]
                    row = _parse_tokens_to_row(
                        cell_tokens,
                        page_index=page_index,
                        bbox=_merge_bboxes(band),
                        table_title=current_table_title,
                        in_table_region=in_table_region,
                    )
                    if row is not None:
                        extracted.append(row)
                        continue

            # Otherwise evaluate each block individually (prevents merging 2-column prose!)
            for blk in band:
                for ln in blk.text.splitlines():
                    ln = ln.strip()
                    if not ln:
                        continue
                    h = _is_table_heading(ln, rules)
                    if h:
                        current_table_title = h
                        in_table_region = True
                        continue
                    if _is_col_header_line(ln):
                        in_table_region = True
                        continue
                    row = _parse_line_to_row(
                        ln,
                        page_index=page_index,
                        bbox=blk.bbox,
                        table_title=current_table_title,
                        in_table_region=in_table_region,
                    )
                    if row is not None:
                        extracted.append(row)

    return extracted


def extract_lab_rows(
    blocks: list[TextBlock],
    layout_text: str,
    page_index: int,
    rules: ExtractionRules,
) -> list[LabTableRow]:
    """Extract structured laboratory rows while preserving raw strings and flagging OCR traps."""
    rows: list[LabTableRow] = []
    if blocks:
        rows = _extract_from_blocks(blocks, page_index=page_index, rules=rules)

    # Fallback to layout_text lines if blocks were unavailable or yielded no rows
    if not rows and layout_text:
        current_table_title = ""
        in_table_region = False
        for raw_line in layout_text.splitlines():
            ln = raw_line.strip()
            if not ln or ln.startswith("[["):
                continue
            heading = _is_table_heading(ln, rules)
            if heading:
                current_table_title = heading
                in_table_region = True
                continue
            if _is_col_header_line(ln):
                in_table_region = True
                continue
            row = _parse_line_to_row(
                ln,
                page_index=page_index,
                bbox=None,
                table_title=current_table_title,
                in_table_region=in_table_region,
            )
            if row is not None:
                rows.append(row)
            elif in_table_region and ("。" in ln or len(ln) > 55):
                in_table_region = False
                current_table_title = ""

    for row in rows:
        row.anomalies = _detect_row_anomalies(row, rules)

    _detect_cross_row_shifts(rows)
    return rows
