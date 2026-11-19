---
name: vet-case-extractor
description: >-
  将中文兽医期刊病例报告 PDF、扫描版兽医临床书籍/译著批量转换为带原文证据溯源的结构化病例数据，
  并导出 9 字段期刊 Excel 或 7 字段书籍 Excel。触发场景：「病例报告抽取」「抽取病例」「病例数据表格」
  「测试集病例」「全书病例抽取」「扫描书抽病例」「复查病例质量」，或用户提供临床病例报告 PDF /
  扫描病例书籍要求结构化整理。
---

# Vet Case Extractor（兽医临床病例结构化抽取 Skill）

> 本 Skill 是 `vet-case-extractor` 工具链的 Agent 执行入口，兼容 AGY、Codex 与 Claude Code。
> 所有执行步骤均通过已验证的 `vetcase` CLI 与 Python 模块完成，禁止跳过脚本或编造不存在的命令。

## 核心纪律（执行前必读）

1. **严格取自原文**：逐字忠实，不编造、不改写、不推理、不翻译；原文缺失字段（如教学书籍无治疗/无随访）直接留空 `""`，严禁写入 `无`、`未提及`、`N/A`、`—`。
2. **区分两条运行路径（不可混淆）**：
   - **独立 LLM API 模式**：用户已配置 `GEMINI_API_KEY` 或 `OPENAI_API_KEY` → 运行 `vetcase extract --provider gemini|openai_compat`。
   - **交互式 Agent 模式（无 API Key 或用户明确要求本地对话抽取）**：运行 `vetcase prepare-agent-batch`，由 Agent 分批（每批 6~10 例）读取 `cases/<case_id>/packet.txt` 与 `pages_img/` 生成严格 JSON，再调用 `vetcase import-cases --method agent_interactive` 校验落盘（绝不宣称等价于独立 API 抽取）。
3. **本地隐私默认**：真实临床病例 PDF 默认仅本地处理，未经用户明确授权外部 API，不得将全文上传至远端模型服务。
4. **领域规范按需加载**：详细规则全部存放在 `references/`，抽取或复核前按任务类型阅读：
   - 期刊/通用字段边界、PE 与专科拆分、Tier C 保留、剖检归属 → [field_rules.md](references/field_rules.md)
   - Dx 书写五则（物种前置、分号、四类括号、缩写） → [dx_rules.md](references/dx_rules.md)
   - 扫描书 7 列口径、页码偏移、双栏版面与连续性 → [book_scan_sop.md](references/book_scan_sop.md)
   - 检验表 OCR 陷阱（`10⁹`/`10¹²`、前导 `1`、行列错位）与方正码位 → [ocr_lab_traps.md](references/ocr_lab_traps.md)
   - QA 规则、人工复核门槛与评测规范 → [qa_and_review.md](references/qa_and_review.md)

---

## 标准执行流程

### 1. 环境与输入检查

1. 在工作区运行环境诊断，确认虚拟环境、PyMuPDF、RapidOCR 及 API Key 状态（不打印密钥内容）：
   ```powershell
   .venv\Scripts\vetcase.exe doctor
   ```
   （Linux/WSL 使用 `.venv/bin/vetcase doctor`。）
2. 统计输入 PDF 数量与类型：
   - **期刊病例报告 (`journal`)**：中文兽医期刊单篇、多篇或合订本；输出 **9 列** Excel。
   - **扫描兽医书籍 / 译著 (`book`)**：无文字层、双栏、按章编号（`病例N-等级X`）；先确认目录页到正文第 1 页的 `page_offset`（`PDF页号 - offset = 书内页码`）；输出 **7 列** Excel。

### 2. 预处理、OCR 与病例索引

```powershell
# 第一步：导入 PDF、SHA-256 去重、评估每页文本质量并渲染需审查/OCR 页图
.venv\Scripts\vetcase.exe ingest --input <PDF路径或目录> --project <工作区目录> --mode <journal|book|auto>

# 若为扫描书籍且存在页码偏移，加上 --page-offset <N>
# 若为合订本中仅抽某篇目标文章，加上 --target-article "<文章题名关键词>"

# 第二步：对触发 needs_ocr 的无文字层/损坏乱码页执行 RapidOCR 与双栏版面恢复（支持断点续跑）
.venv\Scripts\vetcase.exe ocr --project <工作区目录>

# 第三步：识别文章/章节/病例边界，检验书籍连续性，生成 cases/<case_id>/packet.txt
.venv\Scripts\vetcase.exe index --project <工作区目录> --mode <journal|book|auto>
```

> 也可直接使用 `vetcase run --input <PDF路径> --output <工作区目录> --mode <journal|book|auto> --provider none` 一次性跑完 `ingest -> ocr -> index` 并停在抽取前。

### 3. 结构化抽取（二选一）

- **路径 A：已配置外部 LLM API（独立 API 自动抽取）**
  ```powershell
  .venv\Scripts\vetcase.exe extract --project <工作区目录> --provider gemini
  # 失败病例可断点重试：
  .venv\Scripts\vetcase.exe extract --project <工作区目录> --provider gemini --retry-failed
  ```
- **路径 B：无外部 API Key 或交互式 Agent 抽取**
  1. 导出规格说明书与待抽批次清单（每批默认 8 例，防止上下文/输出截断）：
     ```powershell
     .venv\Scripts\vetcase.exe prepare-agent-batch --project <工作区目录> --batch-size 8
     ```
  2. 阅读生成的 `<工作区目录>/EXTRACTION_SPEC.md` 及 `references/field_rules.md`、`references/dx_rules.md`。
  3. 逐批读取 `batch_NN.json` 中列出的 `cases/<case_id>/packet.txt`，按严格 JSON 结构逐例写出结果文件（每完成 1 批立即落盘，避免长输出截断）。
  4. 调用校验与规则化导入工具（自动执行 Schema 校验、Raw/Normalized 拆分、Dx/Rx 规范化与原文 BBox 证据溯源）：
     ```powershell
     .venv\Scripts\vetcase.exe import-cases --project <工作区目录> --input <批次输出.json> --method agent_interactive
     ```

### 4. 自动 QA、人工复核与页图核查

```powershell
.venv\Scripts\vetcase.exe qa --project <工作区目录>
```

检查 `<工作区目录>/qa/qa_report.md` 与 `<工作区目录>/qa/review_queue.json`：

- **可自动交付**：`overall_status` 为 `PASS` 或仅有 `info` 级提示。
- **必须查看页图并标记/通知人工复核**（打开 `<工作区目录>/pages_img/<doc_id>/pNNN.png`）：
  1. `LabTableRow.anomalies` 告警（`×10⁹`/`×10¹²` 冲突、`×10%/L`、前导 `1` 伪影、行列错位、单位混用、形近缩写）；
  2. 书籍病例等级超界或与 `index.json` 不一致（如 `等级13`），或章节缺号/重号；
  3. 病例边界标记 `boundary_uncertain=true`；
  4. 事实字段含「事实+推理」混合长句、或溯源标记 `ReviewStatus.UNGROUNDED`（无原文定位）；
  5. 文件名与抽取正文主题不一致（疑似合订本/错乱 PDF 串扰）。

### 5. 最终导出与交付

```powershell
.venv\Scripts\vetcase.exe export --project <工作区目录> --format <journal|book>
```

最终交付物位于 `<工作区目录>/export/`（可通过 `--output` 与 `--filename` 自定义）：
1. **Excel 主表**（`病例数据` + `说明` 双工作表：期刊 9 列 / 书籍 7 列，冻结首行、自动换行、缺失留空）；
2. **结构化数据与证据**：`cases.jsonl`、`provenance.json`；
3. **质量报告与复核清单**：`qa_report.json`、`qa_report.md`、`review_queue.json`；
4. **可复现运行元数据**：`run_metadata.json`（含模式、规则版本、提取方法与版本）。

汇报结果时，必须如实列出：**实际处理文档/病例数、QA 发现的所有待复核/存疑点及对应页码/页图路径、当前抽取方式（独立 API 或交互式 Agent）**。
