"""Prompt builders embedding veterinary case extraction rules for journal and book sources."""

from __future__ import annotations

from vet_case_extractor.config import ExtractionRules
from vet_case_extractor.models import CaseSegment, SourceType


def build_system_prompt(
    rules: ExtractionRules,
    source_type: SourceType = SourceType.JOURNAL,
) -> str:
    """Build the system instruction embedding all clinical extraction, boundary, and formatting rules."""
    species_preview = "、".join(rules.species_prefixes[:25]) + (
        f" 等共 {len(rules.species_prefixes)} 种" if rules.species_prefixes else ""
    )
    reasoning_preview = "、".join(rules.reasoning_markers[:10])
    tier_c_preview = "、".join(rules.tier_c_retention_markers[:8])
    postmortem_preview = "、".join(rules.postmortem_markers)
    living_preview = "、".join(rules.living_exploration_exclusions)
    pe_preview = "、".join(rules.general_pe_markers[:10])
    specialty_preview = "、".join(rules.specialty_or_neuro_markers[:10])
    placeholders_preview = "、".join(rules.forbidden_placeholders[:8])

    source_note = (
        "当前来源为【扫描兽医书籍/译著】：无期刊刊名与发表年，请将 `source`、`publication_year`、`received_year` 留空字符串 `\"\"`。"
        if source_type == SourceType.BOOK
        else "当前来源为【兽医期刊病例报告】：`source` 仅填中文刊名（如“中国兽医杂志”）；`publication_year` 填刊期发表年；`received_year` 填“收稿日期”年份（无收稿日期则留空 `\"\"`）。"
    )

    return f"""你是一位严谨的中文兽医临床病例结构化抽取专家（规则版本 {rules.rule_version}）。
请严格遵守以下全部抽取纪律与字段口径，输出符合 JSON Schema 的结构化数据：

### 一、最高纪律（零编造、逐字取自原文）
1. 所有抽取内容必须**逐字取自原文**，禁止编造、禁止润色改写、禁止翻译、禁止补写原文不存在的诊疗或检验结果。
2. 缺失字段**必须返回空字符串 `""`**，严禁输出任何占位符（如 `{placeholders_preview}` 等）。
3. {source_note}
4. 在 `evidence_quotes` 中为每个非空抽取字段提供 1~3 条**原文逐字短句**（不得改字），以便后续自动定位 PDF 页码与坐标。
5. 若遇到疑似 OCR 错别字/数值异常（如量级冲突、前导数字伪影、表格错位）或边界疑点，严禁擅自修改原始数值，请如实保留原文并写入 `uncertainties` 与 `notes`。

### 二、字段映射与边界规则
1. **`medical_history`（病史与一般体格检查）**：
   - 包含：动物基本信息（品种、年龄、性别、体重、免疫/驱虫情况）、主诉（CC）、现病史/既往史。
   - **一般体格检查（PE）必须归入 `medical_history`**（例如：{pe_preview} 等）。即使原文把“体格检查”与“实验室检验”连写，也必须拆出体格检查放到 `medical_history`。
   - **Tier C 保留原则**：凡带有 `{tier_c_preview}` 等主诉/转诊背景的表述（如“外院怀疑为…”，“既往曾诊断…并手术切除”），属于临床实际可获背景信息，**必须原样保留在 `medical_history`**，不得剥离。
2. **`medical_test`（辅助检查与专科检查）**：
   - 包含：实验室检查（血常规/CBC、生化、尿液分析、凝血、血气等）、影像学检查（X线、B超/超声、CT、MRI、超声心动图）、细胞学与组织病理学、**神经学检查客观发现**与**专科检查**（例如：{specialty_preview} 等）。
   - **本次就诊的诊断性操作**（切除活检、穿刺采样、存活动物术中探查及离体标本肉眼检查，如 `{living_preview}`）及其病理/细胞学结果归入 `medical_test`。
   - **严禁混入**：一般体格检查、诊断推理/临床意义/神经学定位结论（如“病变定位于…”）、死后剖检/尸检结果。
3. **`medical_treatment`（治疗与处置）**：
   - 包含：治疗方案、手术过程、药物处方（药名、剂量、给药途径与频次）。
   - **严禁混入随访/转归**（如“复查”、“好转”、“临床症状消失”、“死亡”、“安乐死”、“痊愈”等必须归入 `follow_up`）。
   - 若为教学病例书籍，严禁将“解析”中的假设性诊疗建议当作实际已实施治疗；原文未治疗则留空 `""`。
4. **`follow_up`（转归、随访与死后剖检）**：
   - 包含：治疗后恢复情况、复诊时间线、好转/复发/康复/死亡/安乐死信息。
   - **全部死后剖检/尸检结果**（如 `{postmortem_preview}` 及其伴随的尸体组织学/实验室检验）必须归入 `follow_up`；注意与存活动物术中探查/离体取材（留 `medical_test`）区分。
5. **`diagnosis_raw`（诊断结论，遵守 Dx 书写五则）**：
   - ① 物种前置且不空格（支持 {species_preview}），整行只在最前面写一次物种；
   - ② 主病在前，次要/并发/继发诊断排后；
   - ③ 多诊断以中文分号 `；` 间隔；
   - ④ 括号内容精准处理（保留解剖部位/独立限定词；解读说明放入 `notes`；更规范病名替换外层俗称；非诊断症状丢弃）；
   - ⑤ 英文缩写放在全称后的括号内，如 `犬颈椎C4~C5水合髓核脱出（HNPE）`。
6. **`notes`（推理/解读剥离与质量标注）**：
   - 原文中带有 `{reasoning_preview}` 等结论性推理、临床意义解释、神经学定位句子（非 Tier C 背景），必须从事实字段剥离并记录到 `notes`；若客观事实与推理紧密混于同一句导致难以无损切分，请保留客观事实并在 `uncertainties` 中提示需人工复核。
"""


def build_case_user_prompt(
    segment: CaseSegment,
    chunk_text: str,
    chunk_index: int = 1,
    total_chunks: int = 1,
) -> str:
    """Build the user prompt for a single case segment (or one chunk of a long case)."""
    lab_section = ""
    if segment.lab_rows:
        lines = [
            f"- p{row.page_index + 1} [{row.table_title}] {row.item_raw}: "
            f"value={row.value_raw}, unit={row.unit_raw}, ref={row.ref_range_raw}, "
            f"flag={row.flag_raw}, anomalies={row.anomalies}"
            for row in segment.lab_rows
        ]
        lab_section = "\n\n【已解析检验表原始行（供核对，禁止篡改数字）】\n" + "\n".join(lines)

    chunk_info = (
        f"（当前为第 {chunk_index}/{total_chunks} 分块；请仅抽取本分块出现的信息，缺失留空）"
        if total_chunks > 1
        else ""
    )

    return f"""请抽取以下兽医病例的结构化信息 {chunk_info}

【病例元数据】
- case_id: {segment.case_id}
- source_type: {segment.source_type.value}
- source_filename: {segment.source_filename}
- source_journal: {segment.source_journal}
- publication_year: {segment.publication_year}
- received_year: {segment.received_year}
- title: {segment.title}
- page_range: {segment.page_range}
- book_page_range: {segment.book_page_range}

【病例原文文本】
{chunk_text}{lab_section}
"""
