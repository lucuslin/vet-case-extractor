---
name: vet-case-extractor
description: >-
  将中文兽医期刊病例报告 PDF、扫描版兽医临床书籍/译著批量转换为带原文证据溯源的结构化病例数据，
  并导出 9 字段期刊 Excel 或 7 字段书籍 Excel。触发场景：「病例报告抽取」「抽取病例」「病例数据表格」
  「测试集病例」「全书病例抽取」「扫描书抽病例」「复查病例质量」，或用户提供临床病例报告 PDF /
  扫描病例书籍要求结构化整理。
---

# Vet Case Extractor（兽医临床病例结构化抽取 Skill）

> 本文件是 `vet-case-extractor` 的通用 Agent Skill 规范入口，遵循通用 `SKILL.md` 目录协议（兼容 Claude Code、OpenAI Codex、Google Antigravity 及其他支持文件读写与命令执行的 Coding Agent）。
> 各平台的具体安装、自动发现与 MCP 配置方法见 [docs/AGENT_INTEGRATION.md](docs/AGENT_INTEGRATION.md)。

## 核心纪律（执行前必读）

1. **严格取自原文**：所有事实列内容逐字依据原文，不编造、不改写、不推理、不翻译；原文缺失字段（如教学型病例无治疗、无随访）直接留空 `""`，严禁填入 `无`、`未提及`、`N/A`、`—` 等占位符。
2. **明确区分两种语义抽取路径（严禁混淆）**：
   - **路径 A — 独立外部 LLM API 模式**：当用户已配置并授权 `GEMINI_API_KEY` 或 `OPENAI_API_KEY` 时，通过 CLI 调用 `vetcase extract --provider gemini|openai_compat`（或 MCP 工具 `extract_cases`）由 Python 程序直接请求远端模型 API。
   - **路径 B — 交互式 Agent 辅助抽取模式（Interactive Agent Extraction）**：当未配置外部 API Key 或用户希望由当前会话中的 AI Agent 完成抽取时，调用 `vetcase prepare-agent-batch` 生成批次规范与待抽清单，由当前 Agent 读取 `cases/<case_id>/packet.txt`（必要时查看 `pages_img/`）写出 JSON，再调用 `vetcase import-cases --method agent_interactive` 进行严格 Schema 校验、规则规范化与原文 BBox 溯源。
   - ⚠️ **注意**：路径 B 使用的是当前宿主 Agent 会话自身的模型能力（若宿主 Agent 连接云端大模型，文本仍会经过宿主 Agent 所属的模型服务，不等同于本地纯离线推理，也不等价于路径 A 的独立 API 调用）。
3. **隐私与上传边界**：真实临床病例 PDF 默认仅在本地完成解析、RapidOCR 与切分；未经用户明确授权所选模型服务，不得将私人临床资料上传至未经许可的外部端点。
4. **领域细则按需加载（Progressive Disclosure）**：执行抽取或复核前，按任务类型按需读取 `references/` 下的对应规范文件：
   - 期刊/通用字段边界、一般体格检查(PE)与专科检查划分、Tier C 外院既往史保留、死后剖检归属 → [references/field_rules.md](references/field_rules.md)
   - Dx 书写五则（物种前置不空格、主病在前、中文分号 `；`、四类括号三分法、缩写格式） → [references/dx_rules.md](references/dx_rules.md)
   - 扫描书籍 7 列口径、目录页码偏移（`page_offset`）、双栏版面与章节连续性 → [references/book_scan_sop.md](references/book_scan_sop.md)
   - 检验数值表 OCR 陷阱（`10⁹`/`10¹²`、前导 `1` 伪影、行列错位）与方正码位 → [references/ocr_lab_traps.md](references/ocr_lab_traps.md)
   - 结构/语义 QA 规则、页图人工复核门槛与评测规范 → [references/qa_and_review.md](references/qa_and_review.md)

---

## 调用方式选择

Agent 可根据自身宿主环境支持的工具类型，任选以下一种已验证的调用接口：

1. **标准 CLI 命令（推荐，所有具备终端工具的 Agent 均适用）**：
   - 若虚拟环境已激活，直接运行 `vetcase <subcommand>`；
   - 若未激活虚拟环境：
     - POSIX (Linux / macOS / WSL)：`.venv/bin/vetcase <subcommand>`（或 `.venv/bin/python -m vet_case_extractor.cli <subcommand>`）
     - Windows (PowerShell / CMD)：`.venv\Scripts\vetcase.exe <subcommand>`（或 `.venv\Scripts\python.exe -m vet_case_extractor.cli <subcommand>`）
2. **MCP Server 工具调用（适用于已挂载 `vet-case-extractor` MCP Server 的客户端）**：
   - 可直接调用 MCP 工具：`ingest_pdf`、`prepare_agent_batch`、`import_cases`、`extract_cases`、`validate_cases`、`export_cases`、`get_status`。

---

## 标准五步操作规程

### 第一步：环境诊断与文献类型确认

1. 运行环境检查（仅检查依赖与 API Key 是否设置，绝不输出密钥明文）：
   ```bash
   vetcase doctor
   ```
2. 检查输入 PDF 类型：
   - **期刊病例报告 (`--mode journal`)**：中文兽医期刊单篇、多篇或合订本，最终导出 **9 列** Excel。
   - **扫描版临床书籍/译著 (`--mode book`)**：无文字层、双栏排版、按章编号（`病例N-等级X`），需先通过目录页确认 `page_offset`（`PDF页号 - offset = 书内印刷页码`），最终导出 **7 列** Excel。

### 第二步：PDF 导入、按需 OCR 与三级索引切分

```bash
# 1. 导入 PDF、SHA-256 去重、评估每页文本层质量
vetcase ingest --input <PDF文件或目录> --project <工作区目录> --mode <journal|book|auto>
# 扫描书籍附加：--page-offset <N>
# 合订本定位单篇附加：--target-article "<文章标题关键词>"

# 2. 对标记为 needs_ocr 的无文字层或字形损坏页执行 RapidOCR 与双栏版面恢复（支持单页缓存断点续跑）
vetcase ocr --project <工作区目录>

# 3. 识别文章/章节/病例边界，核查书籍连续性，生成 cases/<case_id>/packet.txt
vetcase index --project <工作区目录> --mode <journal|book|auto>
```

> 快捷方式：运行 `vetcase run --input <PDF路径> --output <工作区目录> --mode <journal|book|auto> --provider none` 可一次完成 `ingest -> ocr -> index` 并在抽取前干净停下。

### 第三步：结构化抽取（按环境二选一）

#### 选项 A：独立 LLM API 自动抽取（需已配置 `GEMINI_API_KEY` 或 `OPENAI_API_KEY`）

```bash
vetcase extract --project <工作区目录> --provider gemini
# 仅重试失败病例：
vetcase extract --project <工作区目录> --provider gemini --retry-failed
```

#### 选项 B：交互式 Agent 辅助抽取（无需外部 API Key，任意具备文件读写能力的 Agent 均可执行）

1. 生成抽取规格说明书 `<工作区目录>/agent_batches/EXTRACTION_SPEC.md` 与分批待抽清单（建议每批 6~10 例以防长输出截断）：
   ```bash
   vetcase prepare-agent-batch --project <工作区目录> --batch-size 8
   ```
2. 阅读生成的 `EXTRACTION_SPEC.md` 及 `references/field_rules.md`、`references/dx_rules.md`。
3. 逐批读取 `batch_NN.json` 中列出的 `cases/<case_id>/packet.txt`，严格按 `LLMExtractedPayload` JSON Schema 生成结构化结果文件（如 `rows_01.json`）。
4. 导入并执行 Schema 校验、Raw/Normalized 转换与原文 BBox 证据溯源（可通过 `--agent-platform` 与 `--model-name` 记录当前实际平台与模型标识）：
   ```bash
   vetcase import-cases --project <工作区目录> --input <批次结果.json> \
     --method agent_interactive --agent-platform <codex|claude-code|antigravity|custom> --model-name <实际模型名或unspecified-agent>
   ```

### 第四步：自动 QA 与页图复核

```bash
vetcase qa --project <工作区目录>
```

读取 `<工作区目录>/qa/qa_report.md` 与 `<工作区目录>/qa/review_queue.json`：

- **必须查看页图并转交人工复核的情形**（查看 `<工作区目录>/pages_img/<doc_id>/pNNN.png`）：
  1. `LabTableRow.anomalies` 检验表告警（`×10⁹`/`×10¹²` 量级冲突、`×10%/L`、前导 `1` 伪影、行列错位、参考区间倒置、形近词）；
  2. 书籍病例等级超界或与 `index.json` 不一致（如 `等级13`），或章节内缺号/重号；
  3. 病例边界标记 `boundary_uncertain=true`；
  4. 事实列包含混合「事实 + 推理」长句无法无损拆分，或字段被标为 `ReviewStatus.UNGROUNDED`（原文无匹配证据锚点）；
  5. PDF 文件名与正文主题不一致（疑似合订本串扰）。

### 第五步：导出 Excel 与完整交付包

```bash
vetcase export --project <工作区目录> --format <journal|book>
```

最终交付目录（默认 `<工作区目录>/export/`）包含：
- **Excel 主表**（`病例数据` + `说明` 双工作表：期刊 9 列 / 书籍 7 列，首行冻结 `A2`、自动换行、缺失留空）；
- **结构化数据与证据**：`cases.jsonl`（含 Raw 与 Normalized 双层）、`provenance.json`（BBox 与页码溯源）；
- **QA 与复核清单**：`qa_report.json`、`qa_report.md`、`review_queue.json`；
- **运行元数据**：`run_metadata.json`（记录工具版本、规则版本、提取方法、Agent 平台与模型标识）。
