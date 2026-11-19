# vet-case-extractor

面向中文兽医临床病例报告 PDF 与扫描版兽医临床书籍/译著的结构化抽取工具链（含原生文本质量评估、RapidOCR、双栏版面还原、检验表异常检测、三级边界切分、LLM 结构化抽取 / Agent 交互桥接、Raw/Normalized 双层规则规范化、原文 BBox 证据溯源、自动 QA 与兼容 Excel 导出）。

---

## 目录导航

- [核心特性](#核心特性)
- [环境要求与安装](#环境要求与安装)
  - [Windows 11 + PowerShell 安装（推荐）](#windows-11--powershell-安装推荐)
  - [Linux / WSL 安装](#linux--wsl-安装)
- [配置 LLM API（可选）](#配置-llm-api可选)
- [两种工作模式](#两种工作模式)
- [CLI 命令全览](#cli-命令全览)
- [输出结构与 Excel 交付口径](#输出结构与-excel-交付口径)
- [可选 MCP Server](#可选-mcp-server)
- [测试与开发](#测试与开发)
- [相关文档](#相关文档)

---

## 核心特性

1. **逐页智能 PDF 解析 (`pdf/`)**：PyMuPDF 首选 + `pdfplumber` 仅针对失败页备选 + 文本质量综合评分（字符数、CJK 比例、CNKI/方正可读标点 `U+A3xx`/`U+1001B0` 区分、真乱码率、全页扫描图判定）。
2. **OCR 与版面还原 (`ocr/`, `layout/`)**：基于 `rapidocr-onnxruntime`（支持 250/300 dpi、进程并发上限 3 与 `psutil` 内存自适应降级、ONNX Runtime 线程数控制、单页缓存断点续跑），实现页眉页脚剔除、通栏/左栏/右栏阅读顺序还原，以及血常规/生化/尿检表结构化提取与 OCR 陷阱检测（**绝不擅自改写原始数值**）。
3. **三级边界切分 (`segmentation/`)**：兼容期刊单病例、一文多病例、合订本按关键词定位目标文章，以及扫描书籍章标题识别、整行抗破折号变体正则、病例号连续性检查与逐例文本包生成。
4. **Raw / Normalized 双层设计 (`normalization/`)**：无损保存原文 `*_raw` 层，同时通过可审计规则执行「Dx 书写五则」、Rx 给药规范化（`po/iv/im/sc` + `sid/bid/tid/qid`）、推理剥离（`【原文推理/解读剥离】` + Tier C 保留）与死后剖检归类。
5. **原文证据溯源 (`provenance/`)**：精确/模糊定位每条字段对应的 PDF 页码、SHA-256、原文片段与 BBox；无证据支撑时诚实标注 `ungrounded`，绝不伪造坐标。
6. **自动 QA 与评测 (`validation/`)**：覆盖 Schema、连续性、治疗栏随访泄漏、PE/神经/专科划分、Dx 五则、占位符、OCR 检验异常与溯源一致性的结构/语义检查，并支持 Gold Standard 字段级 EM/P/R/F1 评测。
7. **完全兼容旧版 Excel 与完整交付包 (`export/`)**：保留期刊 9 列与书籍 7 列双工作表结构，并附带 `cases.jsonl`、`provenance.json`、`qa_report.json/.md`、`review_queue.json` 与 `run_metadata.json`。

---

## 环境要求与安装

- **Python 版本**：Python `>=3.10`（已在 Windows 11 + Python `3.13.0` 完整验证；WSL Ubuntu `3.12.3` 环境基础可运行）。
- **核心原则**：始终在项目独立的 `.venv` 虚拟环境中安装，不要污染全局 Python。

### Windows 11 + PowerShell 安装（推荐）

```powershell
cd d:\Antigravity\workspace\dev\vet_pdf\repo

# 1. 创建并激活虚拟环境
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# 2. 升级 pip 并安装完整工具链（含 OCR、pdfplumber、Gemini SDK、开发测试依赖）
python -m pip install --upgrade pip
pip install -e ".[ocr,pdfplumber,gemini,dev]"

# 若需同时启用可选 MCP Server：
# pip install -e ".[all]"

# 3. 验证运行环境
vetcase doctor
```

> 若使用 `uv`，也可用 `python -m uv venv .venv --seed` 与 `python -m uv pip install --python .venv\Scripts\python.exe -e ".[all]"` 加速安装。

### Linux / WSL 安装

```bash
cd /mnt/d/Antigravity/workspace/dev/vet_pdf/repo

python3 -m venv .venv
source .venv/bin/activate

python -m pip install --upgrade pip
pip install -e ".[ocr,pdfplumber,gemini,dev]"

vetcase doctor
```

> 注：在无桌面字体的精简 Linux 容器中运行合成 PDF 测试脚本时，需安装中文字体包（如 `fonts-noto-cjk` 或 `fonts-wqy-zenhei`）。

---

## 配置 LLM API（可选）

仅当需要独立 CLI 调用远端模型 API 自动完成语义抽取时，才需要配置 `.env`：

```powershell
Copy-Item .env.example .env
```

在 `.env` 中按需填写：

```ini
GEMINI_API_KEY=your_key_here
VETCASE_GEMINI_MODEL=gemini-2.5-flash

# 或使用 OpenAI 兼容接口：
OPENAI_API_KEY=your_key_here
OPENAI_BASE_URL=https://api.openai.com/v1
VETCASE_OPENAI_MODEL=gpt-4o-mini
```

- 不得假设 Google AI 订阅自动附带无条件 API 配额，程序不会也禁止通过非官方手段绕过认证。
- 未配置任何 API Key 时，PDF 解析、RapidOCR、版面还原、索引切分、交互式 Agent 抽取导入、QA 检查与 Excel 导出仍可 **100% 本地离线运行**。

---

## 两种工作模式

### 模式一：独立 CLI + LLM API（终端全自动批处理）

```powershell
# 一键从 PDF 到 Excel（需要已配置对应 Provider 的 API Key）
vetcase run --input .\pdfs --output .\runs\batch_01 --mode journal --provider gemini --format journal

# 或分步执行（支持断点续跑与失败重试）：
vetcase ingest --input .\pdfs --project .\runs\batch_01 --mode journal
vetcase ocr --project .\runs\batch_01
vetcase index --project .\runs\batch_01 --mode journal
vetcase extract --project .\runs\batch_01 --provider gemini
vetcase qa --project .\runs\batch_01
vetcase export --project .\runs\batch_01 --format journal
```

扫描书籍示例（设定目录页测得的页码偏移 `--page-offset`）：

```powershell
vetcase ingest --input .\book.pdf --project .\runs\book_01 --mode book --page-offset 11
vetcase ocr --project .\runs\book_01 --dpi 250 --workers 3
vetcase index --project .\runs\book_01 --mode book
vetcase extract --project .\runs\book_01 --provider gemini
vetcase qa --project .\runs\book_01
vetcase export --project .\runs\book_01 --format book --filename "兽医临床病例分析_全书抽取.xlsx"
```

### 模式二：AGY / Agent Skill 交互模式（无 API Key 也可闭环）

1. 让工具链完成本地预处理、OCR 与分批导出：
   ```powershell
   vetcase run --input .\pdfs --output .\runs\agent_proj --mode auto --provider none
   vetcase prepare-agent-batch --project .\runs\agent_proj --batch-size 8
   ```
2. Agent 根据 `SKILL.md` 与 `runs\agent_proj\EXTRACTION_SPEC.md` 读取 `cases/<case_id>/packet.txt`（必要时查看 `pages_img/`），输出符合 `LLMExtractedPayload` 的 JSON 文件（标记为 `agent_interactive`）。
3. 导入、校验、溯源与导出：
   ```powershell
   vetcase import-cases --project .\runs\agent_proj --input .\runs\agent_proj\batch_01_out.json
   vetcase qa --project .\runs\agent_proj
   vetcase export --project .\runs\agent_proj --format journal
   ```

---

## CLI 命令全览

| 命令 | 核心参数 | 说明 |
|---|---|---|
| `vetcase doctor` | `[--json-output]` | 检测 Python、venv、核心包、API Key 存在性、可用内存建议并发数及中文字体 |
| `vetcase ingest` | `--input` `--project` / `--output` `[--mode auto\|journal\|book]` `[--page-offset N]` `[--target-article TEXT]` `[--ocr-policy ...]` `[--retry-failed]` | 递归发现 PDF、SHA-256 去重、逐页评估质量、提取原生文本层并渲染页图 |
| `vetcase ocr` | `--project` `[--doc-id ID]` `[--dpi N]` `[--workers N]` `[--force]` `[--retry-failed]` | 对 `needs_ocr` 页面执行 RapidOCR、版面分栏还原与检验表异常检测 |
| `vetcase index` | `--project` `[--mode auto\|journal\|book]` `[--provider none\|gemini\|openai_compat]` | 切分期刊文章/多病例或书籍章节/病例、核查连续性、生成病例包 `packet.txt` |
| `vetcase extract` | `--project` `[--provider gemini\|openai_compat]` `[--model NAME]` `[--workers N]` `[--case-id ID]` `[--retry-failed]` `[--force]` | 调用独立 LLM API 抽取、严格 Schema 校验、规范化与原文证据溯源 |
| `vetcase prepare-agent-batch` | `--project` `[--output DIR]` `[--batch-size 8]` `[--include-review]` | 导出 `EXTRACTION_SPEC.md` 与分批待抽 JSON 清单 |
| `vetcase import-cases` | `--project` `--input <JSON>` `[--method agent_interactive]` `[--model-name ...]` | 导入 Agent 抽取 JSON，执行严格 Schema 校验、规范化与溯源落盘 |
| `vetcase qa` | `--project` `[--strict]` | 执行结构/语义/OCR/溯源 QA，输出 `qa_report.json/.md` 与 `review_queue.json` |
| `vetcase export` | `--project` `[--format auto\|journal\|book]` `[--layer normalized\|raw]` `[--output DIR]` `[--filename NAME]` | 导出兼容 Excel（`病例数据` + `说明`）及完整数据集束 |
| `vetcase status` | `--project` | 查看各文档与各病例当前状态统计 |
| `vetcase run` | `--input` `--output` / `--project` `[--mode ...]` `[--provider gemini\|openai_compat\|none]` `[--format ...]` | 一键流式运行全管线（`--provider none` 在建索引后干净停下并提示后续命令） |
| `vetcase eval` | `--pred` `--gold` `[--human-annotated]` `[--refuse-unannotated]` `[--output]` | 计算字段级 EM/Precision/Recall/F1（未标注合成集强制附加免责声明） |

**退出码**：`0` 成功 · `1` 运行时错误（IO/Provider/OCR 异常） · `2` 参数或配置错误 · `3` QA 失败（`FAIL` 或 `--strict` 下出现 `REVIEW_REQUIRED`）。

---

## 输出结构与 Excel 交付口径

- **期刊格式（9 列）**：`source | year | title | medical_history | medical_test | medical_treatment | Dx | follow-up | 备注`
- **书籍格式（7 列）**：`title | medical_history | medical_test | medical_treatment | Dx | follow-up | 备注`
- **工作表**：`病例数据`（每例一行、首行冻结 `A2`、自动换行、顶对齐、缺失留空）+ `说明`（数据口径、Dx 五则、OCR 与质量提示、工具与规则版本）。
- **伴随产物**（位于 `<project>/export/`）：`cases.jsonl`、`provenance.json`、`qa_report.json`、`qa_report.md`、`review_queue.json`、`run_metadata.json`。

---

## 可选 MCP Server

若已在 `.venv` 安装 `[mcp]` 依赖，可通过薄封装 MCP Server 暴露工具 `ingest_pdf`、`extract_cases`、`validate_cases`、`export_cases`、`get_status`：

```powershell
python -m vet_case_extractor.mcp_server
```

---

## 测试与开发

```powershell
# 静态 lint 检查
.venv\Scripts\python.exe -m ruff check src tests scripts

# 运行完整测试集（含真实 RapidOCR 扫描 PDF 识别与 11 项异常恢复测试）
New-Item -ItemType Directory -Force tmp | Out-Null
.venv\Scripts\python.exe -m pytest -v --basetemp=tmp/pytest_run

# 运行合成测试数据端到端 Demo
.venv\Scripts\python.exe scripts/run_e2e_demo.py --work-dir tmp/e2e_demo
```

---

## 相关文档

- [SKILL.md](SKILL.md) — Agent 入口技能文件
- [references/](references/) — 领域抽取细则（字段、Dx 五则、扫描书 SOP、OCR 陷阱、QA 规范）
- [RULE_DECISIONS.md](RULE_DECISIONS.md) — 原版规则冲突审计与解决决策
- [docs/RULE_TRACEABILITY.md](docs/RULE_TRACEABILITY.md) — 原 Skill 规则追踪矩阵
- [IMPLEMENTATION_REPORT.md](IMPLEMENTATION_REPORT.md) — 工程实现、实测结果与五阶段验收矩阵
- [KNOWN_LIMITATIONS.md](KNOWN_LIMITATIONS.md) — 已知限制与未验证项说明
