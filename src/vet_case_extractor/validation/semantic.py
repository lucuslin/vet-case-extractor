"""Semantic, OCR-propagation, and provenance-status QA checks for CaseRecords."""

from __future__ import annotations

import re

from vet_case_extractor.config import ExtractionRules
from vet_case_extractor.models import (
    CaseRecord,
    QAIssue,
    ReviewStatus,
    Severity,
    SourceType,
)

_SENTENCE_SPLIT_RE = re.compile(r"[。！？；;\n]+")
_CLAUSE_SPLIT_RE = re.compile(r"[。；;\n]+")
_PARENS_SPAN_RE = re.compile(r"（([^（）]*)）|\(([^()]*)\)")
_ABBR_BEFORE_CJK_PAREN_RE = re.compile(
    r"(?:^|[\u4e00-\u9fff；;\s])([A-Z]{2,10})\s*[（(][^）)]*[\u4e00-\u9fff][^）)]*[）)]"
)
_PRIOR_CARE_TIME_RE = re.compile(r"\d+\s*[个天周月年]前.{0,14}(?:手术|切除|诊断|治疗|就诊|病理)")

_TITLE_BOILERPLATE_RE = re.compile(
    r"(?:病例报告|诊治病例|临床病例|病理诊断和治疗|病理诊断|诊断和治疗|诊断与治疗|"
    r"诊断治疗|临床诊治|诊治|治疗|诊断|报告|分析|探讨|体会|一例|1例|\d+例|"
    r"合成|测试|纯扫描|双栏|混合|原生|扫描|一文多|合订本|病例|的|及|与|和|\.pdf)"
)
_HERBAL_SPECIES_FALSE_POSITIVES = (
    "牛黄",
    "马钱子",
    "鸡内金",
    "蛇床子",
    "龟板",
    "虎杖",
    "熊胆",
    "鹿茸",
    "鱼腥草",
)

_EXTERNAL_CARE_STRICT_MARKERS = (
    "外院",
    "他院",
    "转诊",
    "既往",
    "曾诊断",
    "来院前",
    "入院前",
    "此前",
)


class _SemanticCounter:
    def __init__(self) -> None:
        self.count = 0

    def next_id(self) -> str:
        self.count += 1
        return f"SEM-{self.count:04d}"


def _contains_pe_marker(text: str, marker: str) -> bool:
    if marker.isascii():
        return bool(re.search(rf"\b{re.escape(marker)}\b", text))
    return marker in text


def _extract_leading_species(text: str, sorted_species: list[str]) -> str | None:
    stripped = text.strip()
    for sp in sorted_species:
        if stripped.startswith(sp):
            return sp
    return None


def _extract_title_species(title: str, sorted_species: list[str]) -> set[str]:
    cleaned = title
    for fp in _HERBAL_SPECIES_FALSE_POSITIVES:
        cleaned = cleaned.replace(fp, "")
    found: set[str] = set()
    # Check longest prefixes while consuming matched ranges so "大熊猫" doesn't also yield "熊" or "猫"
    masked = cleaned
    for sp in sorted_species:
        if sp in masked:
            found.add(sp)
            masked = masked.replace(sp, " " * len(sp))
    return found


def _strip_parentheses(text: str) -> str:
    return _PARENS_SPAN_RE.sub("", text)


def _has_unbalanced_parens(text: str) -> bool:
    stack: list[str] = []
    pairs = {"(": ")", "（": "）"}
    closers = {")": "(", "）": "（"}
    for ch in text:
        if ch in pairs:
            stack.append(ch)
        elif ch in closers:
            if not stack or stack[-1] != closers[ch]:
                return True
            stack.pop()
    return bool(stack)


def _is_suspicious_dx_paren(inner: str, rules: ExtractionRules) -> tuple[bool, str]:
    inner_s = inner.strip()
    if not inner_s:
        return True, "空括号"
    # Valid short ASCII abbreviation like HNPE / FIP / IVDD / CKD
    if re.fullmatch(r"[A-Za-z0-9\-+/]{2,15}", inner_s):
        return False, ""
    for marker in rules.reasoning_markers:
        if marker in inner_s:
            return True, f"含推理/解读词 `{marker}`"
    for sym in rules.non_diagnosis_symptoms:
        if sym in inner_s:
            return True, f"含非诊断症状 `{sym}`"
    for clue in ("升高", "降低", "正常", "一过性", "原发性", "鉴别", "解读", "继发", "；", "，", ","):
        if clue in inner_s:
            return True, f"含解释/并列/指标描述 `{clue}`"
    if len(inner_s) > 18:
        return True, "括号内容过长，疑似解释性文本"
    return False, ""


def _check_dx_five_rules(
    record: CaseRecord,
    rules: ExtractionRules,
    sorted_species: list[str],
    counter: _SemanticCounter,
) -> list[QAIssue]:
    issues: list[QAIssue] = []
    dx = record.diagnosis_normalized.strip()
    if not dx:
        return issues

    # Rule 1 & 2: Species prefix & no space after species
    lead_species = _extract_leading_species(dx, sorted_species)
    if lead_species is None:
        issues.append(
            QAIssue(
                issue_id=counter.next_id(),
                category="semantic",
                rule_id="SEM_DX_SPECIES_PREFIX",
                severity=Severity.ERROR,
                case_id=record.case_id,
                document_id=record.document_id,
                field_name="diagnosis_normalized",
                message=f"Dx `{dx}` 未以合法物种前缀开头（46+ 物种表）。",
                suggestion="Dx 须遵循 `物种+疾病名称`，主病在前且仅在开头写一次物种。",
            )
        )
    else:
        after_species = dx[len(lead_species) :]
        if after_species and after_species[0].isspace():
            issues.append(
                QAIssue(
                    issue_id=counter.next_id(),
                    category="semantic",
                    rule_id="SEM_DX_SPACE_AFTER_SPECIES",
                    severity=Severity.ERROR,
                    case_id=record.case_id,
                    document_id=record.document_id,
                    field_name="diagnosis_normalized",
                    message=f"Dx `{dx}` 物种 `{lead_species}` 后存在多余空格。",
                    suggestion=f"去除物种与病名之间空格，例如 `{lead_species}{after_species.lstrip()}`。",
                    auto_fixable=True,
                )
            )

    # Rule 3: Chinese semicolon separator & no duplicate species / non-dx symptoms
    top_level = _strip_parentheses(dx)
    if any(sep in top_level for sep in (",", "，", "、", ";")):
        issues.append(
            QAIssue(
                issue_id=counter.next_id(),
                category="semantic",
                rule_id="SEM_DX_SEPARATOR",
                severity=Severity.WARNING,
                case_id=record.case_id,
                document_id=record.document_id,
                field_name="diagnosis_normalized",
                message=f"Dx `{dx}` 多诊断/并列使用了逗号、顿号或英文分号，未使用中文分号 `；`。",
                suggestion="并发/继发/多病诊断请统一使用中文分号 `；` 分隔。",
                auto_fixable=True,
            )
        )

    parts = [p.strip() for p in dx.split("；") if p.strip()]
    symptom_set = set(rules.non_diagnosis_symptoms)
    for idx, part in enumerate(parts):
        if idx > 0 and _extract_leading_species(part, sorted_species) is not None:
            issues.append(
                QAIssue(
                    issue_id=counter.next_id(),
                    category="semantic",
                    rule_id="SEM_DX_DUPLICATE_SPECIES",
                    severity=Severity.WARNING,
                    case_id=record.case_id,
                    document_id=record.document_id,
                    field_name="diagnosis_normalized",
                    message=f"Dx `{dx}` 次要诊断段 `{part}` 重复写了物种前缀。",
                    suggestion="全行只在最开头写一次物种前缀。",
                    auto_fixable=True,
                )
            )
        cleaned_part = part
        if idx == 0 and lead_species:
            cleaned_part = cleaned_part[len(lead_species) :].strip()
        if cleaned_part.startswith("继发"):
            cleaned_part = cleaned_part[2:].strip()
        if cleaned_part in symptom_set:
            issues.append(
                QAIssue(
                    issue_id=counter.next_id(),
                    category="semantic",
                    rule_id="SEM_DX_NON_DIAGNOSIS_SYMPTOM",
                    severity=Severity.WARNING,
                    case_id=record.case_id,
                    document_id=record.document_id,
                    field_name="diagnosis_normalized",
                    message=f"Dx `{dx}` 包含非诊断症状项 `{cleaned_part}`。",
                    suggestion="症状/体征不应作为诊断条目列出。",
                    auto_fixable=True,
                )
            )

    # Rule 4: Suspicious parentheses
    if _has_unbalanced_parens(dx):
        issues.append(
            QAIssue(
                issue_id=counter.next_id(),
                category="semantic",
                rule_id="SEM_DX_SUSPICIOUS_PARENTHESES",
                severity=Severity.ERROR,
                case_id=record.case_id,
                document_id=record.document_id,
                field_name="diagnosis_normalized",
                message=f"Dx `{dx}` 存在未闭合或不匹配的括号。",
            )
        )
    for match in _PARENS_SPAN_RE.finditer(dx):
        inner = match.group(1) if match.group(1) is not None else match.group(2)
        suspicious, reason = _is_suspicious_dx_paren(inner, rules)
        if suspicious:
            issues.append(
                QAIssue(
                    issue_id=counter.next_id(),
                    category="semantic",
                    rule_id="SEM_DX_SUSPICIOUS_PARENTHESES",
                    severity=Severity.WARNING,
                    case_id=record.case_id,
                    document_id=record.document_id,
                    field_name="diagnosis_normalized",
                    message=f"Dx `{dx}` 括号 `({inner})` 疑似需处理: {reason}。",
                    suggestion="解剖部位/缩写可保留；解读性内容移入备注；更规范病名替换外层俗称；非诊断症状丢弃。",
                )
            )

    # Rule 5: Abbreviation order (full name first, abbreviation in parentheses)
    abbr_match = _ABBR_BEFORE_CJK_PAREN_RE.search(dx)
    if abbr_match:
        issues.append(
            QAIssue(
                issue_id=counter.next_id(),
                category="semantic",
                rule_id="SEM_DX_ABBREVIATION_ORDER",
                severity=Severity.WARNING,
                case_id=record.case_id,
                document_id=record.document_id,
                field_name="diagnosis_normalized",
                message=f"Dx `{dx}` 缩写 `{abbr_match.group(1)}` 置于中文全称括号之前，顺序疑似倒置。",
                suggestion="缩写须遵循先全称后括号格式，例如 `犬颈椎C4~C5水合髓核脱出（HNPE）`。",
            )
        )

    return issues


def _check_title_crosstalk(
    record: CaseRecord,
    sorted_species: list[str],
    counter: _SemanticCounter,
) -> list[QAIssue]:
    issues: list[QAIssue] = []
    if "文件名与内容不符" in record.notes or "串扰" in record.notes:
        issues.append(
            QAIssue(
                issue_id=counter.next_id(),
                category="semantic",
                rule_id="SEM_TITLE_CONTENT_CROSSTALK",
                severity=Severity.ERROR,
                case_id=record.case_id,
                document_id=record.document_id,
                field_name="title",
                message=f"备注标明标题 `{record.title}` 与正文存在串扰/不符。",
                suggestion="核对合订本分页切片或 PDF 内容映射后重抽。",
            )
        )
        return issues

    if record.source_type == SourceType.BOOK:
        return issues

    combined_content = "\n".join(
        part
        for part in (
            record.diagnosis_normalized,
            record.diagnosis_raw,
            record.medical_history,
            record.medical_test,
            record.medical_treatment,
            record.follow_up,
            record.notes,
        )
        if part.strip()
    )
    cjk_in_content = re.findall(r"[\u4e00-\u9fff]", combined_content)
    if len(cjk_in_content) < 12:
        return issues

    title = record.title.strip()
    if not title:
        return issues

    title_species = _extract_title_species(title, sorted_species)
    dx_species = _extract_leading_species(
        record.diagnosis_normalized or record.diagnosis_raw, sorted_species
    )
    if (
        title_species
        and dx_species
        and dx_species not in title_species
        and not any(sp in combined_content for sp in title_species)
    ):
        issues.append(
            QAIssue(
                issue_id=counter.next_id(),
                category="semantic",
                rule_id="SEM_TITLE_CONTENT_CROSSTALK",
                severity=Severity.ERROR,
                case_id=record.case_id,
                document_id=record.document_id,
                field_name="title",
                message=(
                    f"标题物种 ({', '.join(sorted(title_species))}) 与诊断物种 ({dx_species}) "
                    f"及正文完全冲突，疑似跨篇串扰。"
                ),
                suggestion="检查合订本文章边界定位或 PDF 文件名与文本匹配。",
            )
        )
        return issues

    stem = _TITLE_BOILERPLATE_RE.sub("", title)
    for sp in sorted_species:
        stem = stem.replace(sp, "")
    stem_cjk = "".join(re.findall(r"[\u4e00-\u9fff]+", stem))
    if len(stem_cjk) >= 4:
        bigrams = {stem_cjk[i : i + 2] for i in range(len(stem_cjk) - 1)}
        if not any(bg in combined_content for bg in bigrams):
            issues.append(
                QAIssue(
                    issue_id=counter.next_id(),
                    category="semantic",
                    rule_id="SEM_TITLE_CONTENT_CROSSTALK",
                    severity=Severity.ERROR,
                    case_id=record.case_id,
                    document_id=record.document_id,
                    field_name="title",
                    message=(
                        f"标题核心病名 `{stem_cjk}` 与病例正文/诊断无任何双字词重叠，疑似标题与内容串扰。"
                    ),
                    suggestion="检查原 PDF 是否错页、合订本误切或文件内容错配。",
                )
            )
    return issues


def validate_record_semantic(record: CaseRecord, rules: ExtractionRules) -> list[QAIssue]:
    """Run semantic, OCR-propagation, and ungrounded provenance QA checks on one CaseRecord."""
    issues: list[QAIssue] = []
    counter = _SemanticCounter()
    sorted_species = sorted(rules.species_prefixes, key=len, reverse=True)

    # 1. Forbidden placeholder exact match across delivered text fields
    placeholder_set = {p.strip().upper() for p in rules.forbidden_placeholders if p.strip()}
    checked_fields = {
        "source_title": record.source_title,
        "publication_year": record.publication_year,
        "received_year": record.received_year,
        "title": record.title,
        "medical_history": record.medical_history,
        "medical_test": record.medical_test,
        "medical_treatment": record.medical_treatment,
        "diagnosis_normalized": record.diagnosis_normalized,
        "follow_up": record.follow_up,
        "notes": record.notes,
    }
    for fname, val in checked_fields.items():
        stripped = val.strip()
        if stripped and stripped.upper() in placeholder_set:
            issues.append(
                QAIssue(
                    issue_id=counter.next_id(),
                    category="semantic",
                    rule_id="SEM_FORBIDDEN_PLACEHOLDER",
                    severity=Severity.ERROR,
                    case_id=record.case_id,
                    document_id=record.document_id,
                    field_name=fname,
                    message=f"字段 `{fname}` 填入了禁用占位符 `{stripped}`，缺失字段必须留空 `\"\"`。",
                    suggestion="清空占位符为空字符串。",
                    auto_fixable=True,
                )
            )

    # 2. Treatment containing follow-up / death / revisit keywords (suggest split, never blindly delete)
    tx = record.medical_treatment.strip()
    if tx:
        tx_check = tx.replace("预防复发", "").replace("防止复发", "")
        leaked_kws = [kw for kw in rules.treatment_followup_leak_keywords if kw in tx_check]
        if leaked_kws:
            issues.append(
                QAIssue(
                    issue_id=counter.next_id(),
                    category="semantic",
                    rule_id="SEM_TREATMENT_FOLLOWUP_LEAK",
                    severity=Severity.WARNING,
                    case_id=record.case_id,
                    document_id=record.document_id,
                    field_name="medical_treatment",
                    message=f"medical_treatment 疑似混入随访/转归关键词: {leaked_kws}",
                    suggestion="建议人工或规则拆出结果/回访/死亡句至 follow_up，切勿直接删除治疗部分。",
                )
            )

    # 3. General PE in medical_test
    test = record.medical_test.strip()
    if test:
        pe_hits = [m for m in rules.general_pe_markers if _contains_pe_marker(test, m)]
        if pe_hits:
            issues.append(
                QAIssue(
                    issue_id=counter.next_id(),
                    category="semantic",
                    rule_id="SEM_PE_IN_MEDICAL_TEST",
                    severity=Severity.WARNING,
                    case_id=record.case_id,
                    document_id=record.document_id,
                    field_name="medical_test",
                    message=f"medical_test 混入一般体格检查 (PE) 指标: {pe_hits}",
                    suggestion="将一般体格检查内容移入 medical_history，仅保留实验室/影像/神经学/专科检查于 medical_test。",
                )
            )

    # 4. Post-mortem necropsy in medical_test instead of follow_up (respecting living exclusions)
    if test:
        unambiguous_pm = [m for m in rules.postmortem_markers if m != "剖检"]
        pm_flagged = False
        for clause in _CLAUSE_SPLIT_RE.split(test):
            c = clause.strip()
            if not c:
                continue
            hit_unambiguous = [m for m in unambiguous_pm if m in c]
            has_living_excl = any(excl in c for excl in rules.living_exploration_exclusions)
            if hit_unambiguous or ("剖检" in rules.postmortem_markers and "剖检" in c and not has_living_excl):
                pm_flagged = True
                break
        if pm_flagged:
            issues.append(
                QAIssue(
                    issue_id=counter.next_id(),
                    category="semantic",
                    rule_id="SEM_POSTMORTEM_IN_MEDICAL_TEST",
                    severity=Severity.ERROR,
                    case_id=record.case_id,
                    document_id=record.document_id,
                    field_name="medical_test",
                    message="medical_test 混入死后剖检/尸检结果（应归入 follow_up）。",
                    suggestion="死后/安乐死后剖检及其伴随病理检验应全部移至 follow_up；活体术中探查及离体标本肉眼检查保留在 medical_test。",
                )
            )

    # 5. Diagnostic reasoning / neuro-localization in fact columns (respecting Tier C in medical_history)
    fact_fields = (
        ("medical_history", record.medical_history),
        ("medical_test", record.medical_test),
        ("medical_treatment", record.medical_treatment),
        ("follow_up", record.follow_up),
    )
    for fname, content in fact_fields:
        if not content.strip():
            continue
        matched_reasoning: list[str] = []
        for sent in _SENTENCE_SPLIT_RE.split(content):
            s = sent.strip()
            if not s:
                continue
            if fname == "medical_history" and any(t in s for t in rules.tier_c_retention_markers):
                continue
            for rm in rules.reasoning_markers:
                if rm in s and rm not in matched_reasoning:
                    matched_reasoning.append(rm)
        if matched_reasoning:
            issues.append(
                QAIssue(
                    issue_id=counter.next_id(),
                    category="semantic",
                    rule_id="SEM_REASONING_IN_FACT_FIELD",
                    severity=Severity.WARNING,
                    case_id=record.case_id,
                    document_id=record.document_id,
                    field_name=fname,
                    message=f"事实列 `{fname}` 含有推理/临床意义/神经定位标志词: {matched_reasoning}",
                    suggestion="核对是否为混合事实+推理句；纯推理/定位句应剥离至备注（【原文推理/解读剥离】），保留客观检查事实。",
                )
            )

    # 6. External / past care misplaced outside medical_history
    for fname, content in (
        ("medical_test", record.medical_test),
        ("medical_treatment", record.medical_treatment),
    ):
        if not content.strip():
            continue
        ext_hits = [m for m in _EXTERNAL_CARE_STRICT_MARKERS if m in content]
        if ext_hits or _PRIOR_CARE_TIME_RE.search(content):
            issues.append(
                QAIssue(
                    issue_id=counter.next_id(),
                    category="semantic",
                    rule_id="SEM_EXTERNAL_CARE_MISPLACED",
                    severity=Severity.WARNING,
                    case_id=record.case_id,
                    document_id=record.document_id,
                    field_name=fname,
                    message=f"`{fname}` 疑似混入外院/来院前既往诊疗信息 ({ext_hits or ['既往时间线']})",
                    suggestion="他院或来院前既往诊疗/手术/诊断结论应归入 medical_history，本次诊断性操作归入 medical_test。",
                )
            )

    if "【原文推理/解读剥离】" in record.notes:
        for line in record.notes.splitlines():
            if "【原文推理/解读剥离】" in line and any(
                m in line for m in _EXTERNAL_CARE_STRICT_MARKERS
            ):
                issues.append(
                    QAIssue(
                        issue_id=counter.next_id(),
                        category="semantic",
                        rule_id="SEM_TIER_C_OVERSTRIPPED",
                        severity=Severity.WARNING,
                        case_id=record.case_id,
                        document_id=record.document_id,
                        field_name="notes",
                        message="notes 中【原文推理/解读剥离】包含 Tier C（外院/既往）语句，疑似过度剥离。",
                        suggestion="Tier C 外院/既往怀疑或诊断属于转诊背景，应保留在 medical_history。",
                    )
                )
                break

    # 7. Dx 5-rules check
    issues.extend(_check_dx_five_rules(record, rules, sorted_species, counter))

    # 8. LabTableRow.anomalies propagation & OCR suspect / typo candidates
    emitted_ocr_anomaly = False
    for row in record.lab_rows:
        if row.anomalies:
            emitted_ocr_anomaly = True
            issues.append(
                QAIssue(
                    issue_id=counter.next_id(),
                    category="ocr",
                    rule_id="OCR_LAB_ANOMALY",
                    severity=Severity.WARNING,
                    case_id=record.case_id,
                    document_id=record.document_id,
                    field_name="medical_test",
                    page_indices=[row.page_index],
                    message=f"检验行 `{row.item_raw}` (第{row.page_index + 1}页) 存在 OCR/数值异常: {'; '.join(row.anomalies)}",
                    suggestion="请核对原页图中的检验表原值与参考区间，禁止静默修改数字。",
                )
            )

    if record.review_status == ReviewStatus.OCR_SUSPECT and not emitted_ocr_anomaly:
        issues.append(
            QAIssue(
                issue_id=counter.next_id(),
                category="ocr",
                rule_id="OCR_SUSPECT_RECORD",
                severity=Severity.WARNING,
                case_id=record.case_id,
                document_id=record.document_id,
                message="该病例被标记为 ocr_suspect，需人工核对原页图。",
            )
        )

    for typo_raw, typo_cand in rules.ocr_known_typo_candidates.items():
        if typo_raw.isascii():
            hit = bool(re.search(rf"\b{re.escape(typo_raw)}\b", record.medical_test)) or any(
                re.search(rf"\b{re.escape(typo_raw)}\b", r.item_raw) for r in record.lab_rows
            )
        else:
            all_fact_text = "\n".join(
                (
                    record.medical_history,
                    record.medical_test,
                    record.medical_treatment,
                    record.diagnosis_normalized,
                    record.follow_up,
                )
            )
            hit = typo_raw in all_fact_text
        if hit:
            issues.append(
                QAIssue(
                    issue_id=counter.next_id(),
                    category="ocr",
                    rule_id="OCR_KNOWN_TYPO_CANDIDATE",
                    severity=Severity.WARNING,
                    case_id=record.case_id,
                    document_id=record.document_id,
                    field_name="medical_test",
                    message=f"检测到常见 OCR 形近/错字候选 `{typo_raw}` -> 建议核对是否为 `{typo_cand}`。",
                    suggestion="对照页图确认后决定是否修正并在备注说明。",
                )
            )

    # 9. Ungrounded provenance checks
    ungrounded_spans = [s for s in record.evidence_spans if s.review_status == ReviewStatus.UNGROUNDED]
    for span in ungrounded_spans:
        issues.append(
            QAIssue(
                issue_id=counter.next_id(),
                category="provenance",
                rule_id="PROV_UNGROUNDED_FIELD",
                severity=Severity.WARNING,
                case_id=record.case_id,
                document_id=record.document_id,
                field_name=span.field_name,
                page_indices=[span.page_index] if span.page_index is not None else [],
                message=f"字段 `{span.field_name}` 未能在原文页文本中找到匹配证据（UNGROUNDED）。",
                suggestion="人工核对原文页图确认该字段是否来自原文，切勿编造证据坐标。",
            )
        )
    if record.review_status == ReviewStatus.UNGROUNDED and not ungrounded_spans:
        issues.append(
            QAIssue(
                issue_id=counter.next_id(),
                category="provenance",
                rule_id="PROV_UNGROUNDED_RECORD",
                severity=Severity.WARNING,
                case_id=record.case_id,
                document_id=record.document_id,
                message="病例整体溯源状态为 UNGROUNDED，存在无法定位原文的抽取内容。",
                suggestion="对照病例文本包与页图核实原文出处。",
            )
        )

    # 10. Title-vs-content crosstalk
    issues.extend(_check_title_crosstalk(record, sorted_species, counter))

    return issues


def validate_semantic(records: list[CaseRecord], rules: ExtractionRules) -> list[QAIssue]:
    """Run semantic, OCR-propagation, and provenance QA checks across all records."""
    all_issues: list[QAIssue] = []
    for rec in records:
        all_issues.extend(validate_record_semantic(rec, rules))
    # Re-number issue_ids uniquely across the batch
    for idx, issue in enumerate(all_issues, start=1):
        issue.issue_id = f"SEM-{idx:04d}"
    return all_issues
