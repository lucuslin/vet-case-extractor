# 质量控制、人工复核与评测规范（QA, Review & Evaluation）

> 对应模块：`src/vet_case_extractor/validation/`、`vetcase qa`、`vetcase eval`。

## 1. QA 检查维度

### 结构检查 (`validation/structural.py`)
- Pydantic Schema 合法性与必填元数据完整度
- 重复 `document_id` / `case_id` 检测
- 文章/索引数量与已抽取病例数量一致性
- 书籍章节病例号连续性（缺号/重号）与 `title` 等级同 `index.json` 一致性
- 损坏、截断或不完整 JSON 文件检测

### 语义与领域检查 (`validation/semantic.py`)
- `medical_treatment` 残留随访/死亡/复查/好转/复发关键词（仅告警并提供拆分建议，**绝不不经人工核验自动删除完整句子**）
- `medical_test` 残留一般体格检查（PE）或诊断推理/神经学定位句
- `medical_test` / `medical_treatment` 误混入外院既往诊疗（尊重 Tier C）
- 死后剖检/尸检误放入 `medical_test`（自动豁免存活动物开腹/开胸探查与离体标本肉眼检查）
- `Dx` 五则合规性（物种前缀表、物种后无空格、多诊断中文分号 `；`、可疑解释性括号、缩写格式）
- 禁用占位符整格扫描（`无` / `未提及` / `未见` / `不适用` / `N/A` / `—`）
- 检验表 `LabTableRow.anomalies` 汇总至病例告警与页码引用
- 溯源一致性：标记 `ReviewStatus.UNGROUNDED`（无法在原文定位支撑片段的字段）、文件名与正文主题不符的串扰行检测

---

## 2. QA 产物与退出码

运行 `vetcase qa --project <项目目录>` 生成：
- `<项目目录>/qa/qa_report.json`（机器可读完整报告）
- `<项目目录>/qa/qa_report.md`（人类可读审查报告）
- `<项目目录>/qa/review_queue.json`（所有 `review_required` / `failed` 病例清单，含源 PDF、页码、页图路径与问题详情）

退出码约定：
- `0`：`PASS`、`PASS_WITH_WARNINGS` 或非严格模式下的 `REVIEW_REQUIRED`
- `3`：`overall_status == FAIL`（或 `--strict` 模式下为 `REVIEW_REQUIRED`）

---

## 3. 何时必须查看页图并转交人工复核

出现以下任一情形时，**禁止自动覆盖或猜测**，必须检查 `<项目目录>/pages_img/<doc_id>/pNNN.png` 并标记人工复核：
1. 检验表触发 `LabTableRow.anomalies`（量级冲突、前导 `1`、错位、形近缩写、单位异常）
2. 书籍病例等级超界或与目录/索引冲突（如 `等级13`）
3. 病例边界标记 `boundary_uncertain=true` 或合订本目标文章定位不明确
4. 事实列出现混合「事实 + 推理」长句无法无损拆分
5. 字段被标记为 `ReviewStatus.UNGROUNDED`（无原文证据锚点）
6. 文件名与抽取正文主题不一致（疑似串扰 PDF）

---

## 4. Gold Standard 评测纪律 (`vetcase eval`)

- `vetcase eval --pred <cases.jsonl|xlsx> --gold <gold.jsonl|xlsx>` 计算字段级 Exact Match、Token/Span Precision、Recall、F1。
- **未经人工标注的合成数据严禁宣称为真实临床抽取准确率**：未传入 `--human-annotated` 时，输出强制附带 `UNANNOTATED_SYNTHETIC_NOT_CLINICAL_ACCURACY` 警示标签；传入 `--refuse-unannotated` 则拒绝评分。
