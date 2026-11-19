# 规则清单与模块追踪表 (Rule Traceability Matrix)

本表由 `legacy/SKILL.md` 审计提取，记录每条业务与工程规则在 `vet-case-extractor` 中的实现位置、配置项与自动化测试覆盖。

| 规则编号 | 原 Skill 来源与规则摘要 | 实现模块 / 文件 | 配置键 (`config/*.yaml`) | 测试文件（Phase 1-3 补齐并验证） |
|---|---|---|---|---|
| R-01 | 期刊 9 列固定顺序 (`source\|year\|title\|medical_history\|medical_test\|medical_treatment\|Dx\|follow-up\|备注`) | `export/excel.py` | `export.*` | `tests/test_export.py` |
| R-02 | 书籍 7 列固定顺序（删除 `source`/`year`） | `export/excel.py` | `export.*` | `tests/test_export.py` |
| R-03 | `source` 仅填中文刊名；`year` 为 `发表年（收稿年）` | `segmentation/journal.py`, `extraction/prompts.py`, `models.py` | — | `tests/test_segmentation.py` |
| R-04 | `medical_history` 含信号量/主诉/病史/**一般体格检查(PE)**/外院诊断(Tier C) | `extraction/prompts.py`, `normalization/fields.py`, `validation/semantic.py` | `general_pe_markers`, `tier_c_retention_markers` | `tests/test_normalization.py`, `tests/test_validation.py` |
| R-05 | `medical_test` 含实验室/影像/神经学客观发现/眼科口腔等专科检查/本次诊断性操作；不含一般 PE 与推理 | `extraction/prompts.py`, `normalization/fields.py`, `validation/semantic.py` | `general_pe_markers`, `specialty_or_neuro_markers` | `tests/test_normalization.py`, `tests/test_validation.py` |
| R-06 | `medical_treatment` 含 Tx/手术/Rx；不混入死亡/复查/好转等随访；Rx 途径与频次标准化 (`po/iv/im/sc` + `sid/bid/tid/qid`)；保留 Raw 审计 | `normalization/rx.py`, `normalization/fields.py`, `validation/semantic.py` | `rx_routes`, `rx_frequencies`, `treatment_followup_leak_keywords` | `tests/test_normalization.py`, `tests/test_validation.py` |
| R-07 | 诊断性操作归 `medical_test`；他院既往诊疗与病理结论留 `medical_history` | `extraction/prompts.py`, `normalization/fields.py`, `validation/semantic.py` | `tier_c_retention_markers` | `tests/test_validation.py` |
| R-08 | 死后剖检/尸检/安乐死后剖检及伴随检验全归 `follow-up`；存活动物术中探查及离体肉眼检查留 `medical_test` | `extraction/prompts.py`, `normalization/fields.py`, `validation/semantic.py` | `postmortem_markers`, `living_exploration_exclusions` | `tests/test_normalization.py`, `tests/test_validation.py` |
| R-09 | Dx 书写五则：物种前置不空格（全行仅一次物种）、主病在前、中文分号 `；` 间隔、四类括号三分法（解剖保留/解读移备注/规范名替换/非诊断症状丢弃）、缩写先全称后括号 | `normalization/dx.py`, `validation/semantic.py` | `species_prefixes`, `non_diagnosis_symptoms` | `tests/test_normalization.py`, `tests/test_validation.py` |
| R-10 | 推理/解读剥离至 `备注`（`【原文推理/解读剥离】`），清残片，Tier C 保留，混合句不盲删而标复核 | `normalization/fields.py`, `validation/semantic.py` | `reasoning_markers`, `tier_c_retention_markers` | `tests/test_normalization.py`, `tests/test_validation.py` |
| R-11 | 缺失字段留空，禁用 `无/未提及/N/A/—` 等占位符；不编造、不补写假设治疗 | `normalization/fields.py`, `validation/semantic.py`, `export/excel.py` | `forbidden_placeholders` | `tests/test_normalization.py`, `tests/test_validation.py` |
| R-12 | PyMuPDF 首选 + `pdfplumber` 备选（仅失败页）+ 文本质量综合判定（字符数/CJK 比例/乱码率/方正码位区分/全页图像）决定 OCR | `pdf/quality.py`, `pdf/reader.py`, `utils/text.py` | `pdf.*` | `tests/test_pdf.py` |
| R-13 | RapidOCR（250/300 dpi、闭角分类、`rec_batch_num=16`、内存感知并发<=3、ORT 线程限制、单页缓存断点续跑） | `ocr/engine.py` | `ocr.*` | `tests/test_ocr_layout.py` |
| R-14 | 双栏版面恢复（检测跨栏通栏元素 + 左右栏分栏 + Y 升序 + `[[左栏]]/[[右栏]]/[[通栏]]` 标记） | `layout/columns.py` | `layout.*` | `tests/test_ocr_layout.py` |
| R-15 | 检验表结构化与 OCR 陷阱检测（`10⁹`/`10¹²`、`10%/L`、前导 `1`、错位、形近词、参考区间冲突），保留原值不擅改，标记复核 | `ocr/tables.py`, `validation/semantic.py` | `ocr_known_typo_candidates`, `book_lab_table_headings` | `tests/test_ocr_layout.py`, `tests/test_validation.py` |
| R-16 | 期刊合订本定位目标文章 + 一文多病例拆行；保留文章与病例映射 | `segmentation/journal.py`, `segmentation/indexer.py` | `segmentation.*` | `tests/test_segmentation.py` |
| R-17 | 扫描书籍章节识别、整行标题正则（抗破折号变体/防交叉引用误判）、等级异常检测、页码偏移映射、章内病例号连续性与重号/缺号检查、病例文本包+页图生成 | `segmentation/book.py`, `segmentation/indexer.py` | `book_lab_table_headings`, `book_summary_headings` | `tests/test_segmentation.py` |
| R-18 | 独立 LLM Provider 抽象 + 官方 `google-genai` 结构化输出 + 重试/限流/超时/分块/Token 日志 + 显式 Agent 交互桥接 | `providers/*`, `extraction/*` | `llm.*` | `tests/test_extraction_providers.py` |
| R-19 | 原文证据溯源（PDF SHA-256、页码、原文片段、BBox、提取方法/模型/规则版本；无证据标注 `ungrounded` 不伪造坐标） | `provenance/tracker.py` | — | `tests/test_provenance.py` |
| R-20 | 增量落盘、稳定 ID/SHA-256 去重、状态机 (`pending/running/completed/review_required/failed`)、断点续跑与 `--retry-failed` | `ingestion/project.py`, `pipeline.py` | — | `tests/test_ingestion.py`, `tests/test_exceptions_and_recovery.py` |
| R-21 | Excel 双 Sheet（`病例数据` + `说明`）、冻结首行、自动换行、稳定排序、安全字符、辅助导出束 | `export/excel.py`, `export/bundle.py` | `export.*` | `tests/test_export.py` |
| R-22 | 全量 QA（结构+语义+OCR+溯源）+ JSON/MD 报告 + 人工复核清单 + Gold Standard 评价接口（合成数据禁用真实准确率宣称） | `validation/*` | — | `tests/test_validation.py` |
