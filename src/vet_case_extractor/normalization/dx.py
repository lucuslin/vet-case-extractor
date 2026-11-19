"""Deterministic normalization of veterinary diagnoses implementing the 5 Dx rules and 4 parenthesis categories."""

from __future__ import annotations

import re

from vet_case_extractor.config import ExtractionRules
from vet_case_extractor.models import NormalizationChange

_DEFAULT_SPECIES_PREFIXES = [
    "大熊猫", "小熊猫", "美洲驼", "奶牛", "水牛", "牦牛", "山羊", "绵羊",
    "豚鼠", "仓鼠", "龙猫", "大鼠", "小鼠", "刺猬", "水貂", "雪貂",
    "羊驼", "骆驼", "鹦鹉", "蜥蜴",
    "犬", "猫", "马", "驴", "骡", "牛", "羊", "猪", "兔", "鹿", "狐",
    "猴", "虎", "狮", "熊", "鸡", "鸭", "鹅", "鸽", "鸟", "禽", "龟",
    "蛇", "鳄", "蛙", "鱼",
]

_DEFAULT_FORBIDDEN_PLACEHOLDERS = {
    "无", "未提及", "未见", "不适用", "n/a", "na", "无记录", "未详", "不详", "暂无", "—", "-"
}

_DEFAULT_NON_DX_SYMPTOMS = {
    "厌食", "应激", "消瘦", "发热", "沉郁", "嗜睡", "脱水", "虚弱", "呕吐", "腹泻",
    "精神沉郁", "食欲废绝", "食欲不振", "食欲减退", "食欲下降", "精神差", "体重下降",
}

_INTERPRETATION_CUES = (
    "升高", "降低", "正常", "一过性", "非原发性", "鉴别", "提示", "考虑", "可能",
    "疑似", "排除", "推断", "符合", "可见", "说明", "表明", "意义", "参考", "由于",
    "生长期", "数值", "指标", "待排", "待查", "待定", "待确诊", "倾向", "主要为",
    "ALT", "AST", "ALP", "GGT", "BUN", "CRE", "WBC", "RBC", "ALB", "TBIL",
)

_SECONDARY_PREFIX_RE = re.compile(r"^(继发(?:性)?|并发|合并|伴发|伴|并引起|引起|以及)\s*")
_ALIAS_PREFIX_RE = re.compile(r"^(?:即|又称|实为|学名|全称|规范名|同义词)\s*[:：]?\s*")
_ASCII_ABBR_RE = re.compile(r"^[A-Za-z][A-Za-z0-9\-/+]{1,15}$")

_ANATOMICAL_OR_QUALIFIER_SUFFIXES = (
    "侧", "端", "部", "区", "腔", "膜", "管", "叶", "段", "级", "期", "型", "度",
    "眼", "耳", "肢", "颈", "胸", "腹", "骨", "关节", "近端", "远端", "背侧", "腹侧",
    "急性", "慢性", "亚急性", "原发性", "继发性", "先天性", "特发性", "弥漫性", "局灶性",
    "良性", "恶性", "单侧", "双侧", "左侧", "右侧", "幼龄", "成年", "老年", "轻度", "中度", "重度",
)

_DISEASE_NOUN_SUFFIXES = (
    "病", "炎", "瘤", "癌", "症", "综合征", "综合症", "机能亢进", "机能减退",
    "衰竭", "梗阻", "阻塞", "感染", "中毒", "贫血", "结石", "穿孔", "破裂",
    "脱垂", "脱出", "增生", "坏死", "积液", "积水", "囊肿", "骨折", "疝",
    "溃疡", "损伤", "病变", "障碍", "麻痹", "瘫痪", "血栓", "栓塞", "出血",
    "水肿", "萎缩", "肥大", "发育不全", "畸形", "移位", "变性", "沉积症",
    "syndrome", "disease", "carcinoma", "sarcoma", "lymphoma", "tumor",
)


def _effective_species(rules: ExtractionRules) -> list[str]:
    base = rules.species_prefixes if rules.species_prefixes else _DEFAULT_SPECIES_PREFIXES
    return sorted({s.strip() for s in base if s.strip()}, key=len, reverse=True)


def _effective_symptoms(rules: ExtractionRules) -> set[str]:
    base = set(rules.non_diagnosis_symptoms) if rules.non_diagnosis_symptoms else set()
    return base | _DEFAULT_NON_DX_SYMPTOMS


def _is_placeholder(text: str, rules: ExtractionRules) -> bool:
    s = text.strip()
    if not s:
        return True
    placeholders = {p.lower() for p in (rules.forbidden_placeholders or [])} | _DEFAULT_FORBIDDEN_PLACEHOLDERS
    return s.lower() in placeholders


def _split_top_level(text: str, seps: tuple[str, ...] = ("；", ";", "，", ",", "、")) -> list[str]:
    """Split diagnosis text at top level (parenthesis depth == 0), protecting numeric commas."""
    items: list[str] = []
    buf: list[str] = []
    depth = 0
    for idx, ch in enumerate(text):
        if ch in ("(", "（"):
            depth += 1
            buf.append(ch)
        elif ch in (")", "）"):
            depth = max(0, depth - 1)
            buf.append(ch)
        elif depth == 0 and ch in seps:
            if ch == "," and 0 < idx < len(text) - 1 and text[idx - 1].isdigit() and text[idx + 1].isdigit():
                buf.append(ch)
                continue
            part = "".join(buf).strip()
            if part:
                items.append(part)
            buf.clear()
        else:
            buf.append(ch)
    tail = "".join(buf).strip()
    if tail:
        items.append(tail)
    return items


def _strip_species_prefix(text: str, species_list: list[str]) -> tuple[str, str]:
    cleaned = text.strip()
    for sp in species_list:
        if cleaned.startswith(sp):
            rest = cleaned[len(sp) :].lstrip(" :：")
            return sp, rest
    return "", cleaned


def _clean_cjk_spacing(text: str) -> str:
    s = re.sub(r"\s+", " ", text).strip()
    s = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", s)
    s = re.sub(r"\s*([（(])\s*", "（", s)
    s = re.sub(r"\s*([）)])\s*", "）", s)
    return s.strip()


def _is_canonical_disease_name(outer: str, inner: str) -> bool:
    if _ASCII_ABBR_RE.match(inner):
        return False
    if _ALIAS_PREFIX_RE.match(inner):
        return True
    if any(inner.endswith(suf) for suf in _ANATOMICAL_OR_QUALIFIER_SUFFIXES):
        return False
    if re.match(r"^[A-Za-z0-9~～\-/]+$", inner):
        return False
    inner_lower = inner.lower()
    if any(inner_lower.endswith(suf) for suf in _DISEASE_NOUN_SUFFIXES):
        cjk_count = sum(1 for c in inner if "\u4e00" <= c <= "\u9fff")
        if (cjk_count >= 3 or "syndrome" in inner_lower or "disease" in inner_lower) and inner != outer:
            return True
    return False


def _is_interpretation_paren(inner: str) -> bool:
    if any(cue in inner for cue in _INTERPRETATION_CUES):
        return True
    return bool(("；" in inner or ";" in inner or "。" in inner) and len(inner) >= 12)


def _process_clause_parens(
    clause: str,
    symptoms: set[str],
    changes: list[NormalizationChange],
) -> tuple[list[str], list[str]]:
    """Process parenthetical groups in one clause; returns (resulting_dx_clauses, moved_notes)."""
    moved_notes: list[str] = []
    extra_clauses: list[str] = []

    # Normalize parentheses first for uniform parsing
    work = clause.replace("(", "（").replace(")", "）").strip()

    # Rule 5 reverse order: `HNPE（颈椎C4~C5水合髓核脱出）` -> `颈椎C4~C5水合髓核脱出（HNPE）`
    rev_match = re.match(r"^([A-Za-z][A-Za-z0-9\-/]{1,14})\s*（([^（）]+)）$", work)
    if rev_match:
        abbr, inner = rev_match.group(1).strip(), rev_match.group(2).strip()
        if any("\u4e00" <= ch <= "\u9fff" for ch in inner) and not _is_interpretation_paren(inner):
            swapped = f"{inner}（{abbr}）"
            changes.append(
                NormalizationChange(
                    field_name="diagnosis",
                    rule_id="DX_RULE5_ABBR_ORDER",
                    before=clause,
                    after=swapped,
                    reason="Reordered abbreviation into parentheses after full Chinese disease name.",
                )
            )
            return [swapped], moved_notes

    paren_matches = list(re.finditer(r"（([^（）]+)）", work))
    if not paren_matches:
        return [_clean_cjk_spacing(work)], moved_notes

    rebuilt_parts: list[str] = []
    cursor = 0
    for m in paren_matches:
        rebuilt_parts.append(work[cursor : m.start()])
        cursor = m.end()
        inner = m.group(1).strip()
        prefix_outer = "".join(rebuilt_parts).strip()

        # Category 2: Explanatory / lab / differential interpretation -> move to notes
        if _is_interpretation_paren(inner) and not _SECONDARY_PREFIX_RE.match(inner):
            moved_notes.append(inner)
            changes.append(
                NormalizationChange(
                    field_name="diagnosis",
                    rule_id="DX_RULE4_PAREN_INTERPRETATION_TO_NOTES",
                    before=f"（{inner}）",
                    after="",
                    reason="Moved explanatory/lab interpretation parenthetical to notes.",
                )
            )
            continue

        # Category 4: Secondary/concurrent clauses or symptoms inside parens
        sec_match = _SECONDARY_PREFIX_RE.match(inner)
        sub_items = _split_top_level(inner, seps=("、", "，", ",", "；", ";"))
        all_are_symptoms = bool(sub_items) and all(
            _SECONDARY_PREFIX_RE.sub("", it).strip() in symptoms for it in sub_items
        )
        if sec_match or all_are_symptoms:
            connector = sec_match.group(1) if sec_match else ""
            kept_subs: list[str] = []
            dropped_subs: list[str] = []
            for idx_s, sub in enumerate(sub_items):
                sub_clean = sub.strip()
                sub_conn = connector if (idx_s == 0 or not kept_subs and connector) else ""
                if idx_s == 0 and sec_match:
                    core = sub_clean[len(sec_match.group(0)) :].strip()
                else:
                    core = _SECONDARY_PREFIX_RE.sub("", sub_clean).strip()
                    if sub_clean != core and not sub_conn:
                        sub_conn = sub_clean[: len(sub_clean) - len(core)].strip()
                if core in symptoms or not core:
                    if core:
                        dropped_subs.append(core)
                    continue
                item_text = f"{sub_conn}{core}" if sub_conn else core
                kept_subs.append(_clean_cjk_spacing(item_text))

            extra_clauses.extend(kept_subs)
            changes.append(
                NormalizationChange(
                    field_name="diagnosis",
                    rule_id="DX_RULE4_PAREN_SECONDARY_AND_SYMPTOMS",
                    before=f"（{inner}）",
                    after="；".join(kept_subs),
                    reason=(
                        f"Split secondary diagnoses ({kept_subs}) and dropped non-diagnosis symptoms ({dropped_subs})."
                    ),
                )
            )
            continue

        # Category 3: Canonical/more accurate disease name replacing colloquial outer name
        alias_clean = _ALIAS_PREFIX_RE.sub("", inner).strip()
        if prefix_outer and _is_canonical_disease_name(prefix_outer, alias_clean) and not work[cursor:].strip():
            rebuilt_parts = [alias_clean]
            changes.append(
                NormalizationChange(
                    field_name="diagnosis",
                    rule_id="DX_RULE4_PAREN_CANONICAL_REPLACE",
                    before=f"{prefix_outer}（{inner}）",
                    after=alias_clean,
                    reason="Replaced colloquial outer diagnosis with canonical name from parentheses.",
                )
            )
            continue

        # Category 1 & Rule 5: Anatomical site / independent qualifier / abbreviation -> retain
        rebuilt_parts.append(f"（{inner}）")

    rebuilt_parts.append(work[cursor:])
    main_dx = _clean_cjk_spacing("".join(rebuilt_parts))
    result_clauses = [main_dx] if main_dx else []
    result_clauses.extend(extra_clauses)
    return result_clauses, moved_notes


def normalize_dx(
    diagnosis_raw: str,
    rules: ExtractionRules,
) -> tuple[str, str, list[NormalizationChange]]:
    """Normalize diagnosis string according to Dx 5 rules and 4 parenthesis categories.

    Returns `(diagnosis_normalized, moved_notes, changes)`.
    """
    raw = (diagnosis_raw or "").strip()
    if not raw:
        return "", "", []

    if _is_placeholder(raw, rules):
        return (
            "",
            "",
            [
                NormalizationChange(
                    field_name="diagnosis",
                    rule_id="DX_PLACEHOLDER_CLEARED",
                    before=raw,
                    after="",
                    reason="Cleared forbidden placeholder in diagnosis field.",
                )
            ],
        )

    species_list = _effective_species(rules)
    symptoms = _effective_symptoms(rules)
    changes: list[NormalizationChange] = []
    moved_notes_parts: list[str] = []

    top_clauses = _split_top_level(raw)
    expanded_clauses: list[str] = []
    for clause in top_clauses:
        sp, body = _strip_species_prefix(clause, species_list)
        sub_clauses, moved = _process_clause_parens(body, symptoms, changes)
        moved_notes_parts.extend(moved)
        for idx, sub in enumerate(sub_clauses):
            if idx == 0 and sp:
                expanded_clauses.append(f"{sp}{sub}")
            else:
                expanded_clauses.append(sub)

    # Filter out standalone non-diagnosis symptoms when real diagnoses exist
    real_clauses: list[str] = []
    dropped_symptoms: list[str] = []
    for cl in expanded_clauses:
        _, body = _strip_species_prefix(cl, species_list)
        if body in symptoms:
            dropped_symptoms.append(cl)
        else:
            real_clauses.append(cl)

    if real_clauses and dropped_symptoms:
        changes.append(
            NormalizationChange(
                field_name="diagnosis",
                rule_id="DX_DROP_SYMPTOMS",
                before="；".join(expanded_clauses),
                after="；".join(real_clauses),
                reason=f"Dropped non-diagnosis symptom item(s): {dropped_symptoms}",
            )
        )
        final_source = real_clauses
    else:
        final_source = expanded_clauses

    # Rule 1: Keep species prefix once at the start without space; strip repeated species on later clauses
    primary_species = ""
    normalized_clauses: list[str] = []
    for idx, cl in enumerate(final_source):
        sp, rest = _strip_species_prefix(cl, species_list)
        if idx == 0:
            primary_species = sp
            cleaned = _clean_cjk_spacing(f"{sp}{rest}" if sp else rest)
            if cleaned:
                normalized_clauses.append(cleaned)
        else:
            if sp and (sp == primary_species or primary_species):
                cleaned = _clean_cjk_spacing(rest)
                if cleaned:
                    normalized_clauses.append(cleaned)
            else:
                cleaned = _clean_cjk_spacing(cl)
                if cleaned:
                    normalized_clauses.append(cleaned)

    diagnosis_normalized = "；".join(normalized_clauses)
    moved_notes = "；".join(dict.fromkeys(p for p in moved_notes_parts if p))

    if diagnosis_normalized != raw and not changes:
        changes.append(
            NormalizationChange(
                field_name="diagnosis",
                rule_id="DX_FORMAT_NORMALIZED",
                before=raw,
                after=diagnosis_normalized,
                reason="Normalized species prefix spacing and semicolon delimiters.",
            )
        )

    return diagnosis_normalized, moved_notes, changes
