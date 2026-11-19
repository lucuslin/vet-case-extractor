# vet-case-extractor

**中文兽医临床病例报告 PDF 与扫描版临床书籍/译著的结构化抽取工具链** —— 集成本地 PDF 质量评估、RapidOCR、双栏版面恢复、检验表异常检测、三级边界切分、可插拔 LLM / 跨平台 Agent 交互抽取、Raw/Normalized 双层规则规范化、原文 BBox 证据溯源、自动 QA 与兼容 Excel 导出。

---

## 目录

1. [项目简介](#1-项目简介)
2. [功能概览](#2-功能概览)
3. [输入、输出及适用范围](#3-输入输出及适用范围)
4. [Quick Start（克隆、安装与首个合成案例运行）](#4-quick-start克隆安装与首个合成案例运行)
5. [CLI 使用指南](#5-cli-使用指南)
6. [Agent Skill 使用（Codex / Claude Code / Antigravity / 通用 Agent）](#6-agent-skill-使用codex--claude-code--antigravity--通用-agent)
7. [MCP Server 使用](#7-mcp-server-使用)
8. [支持的平台与已验证程度](#8-支持的平台与已验证程度)
9. [配置外部 LLM Provider（可选）](#9-配置外部-llm-provider可选)
10. [样例数据与自动 QA](#10-样例数据与自动-qa)
11. [测试、开发与文档分层](#11-测试开发与文档分层)
12. [已知限制、许可与免责声明](#12-已知限制许可与免责声明)

---

## 1. 项目简介

`vet-case-extractor` 旨在将非结构化的中文兽医临床文献（如《中国兽医杂志》《畜牧与兽医》《黑龙江畜牧兽医》等期刊病例报告 PDF，以及无文字层、双栏排版的扫描型兽医临床书籍/译著）批量转化为**可溯源、可质检、符合领域规范**的结构化病例数据集与评测基准（Benchmark）。

本项目同时支持三种调用形态：
- **独立命令行工具 (`vetcase` CLI)**：可在 Windows、Linux、WSL、macOS 终端直接运行。
- **跨平台 Agent Skill ([SKILL.md](SKILL.md))**：支持 Claude Code、OpenAI Codex、Google Antigravity 及任意具备终端与文件读写能力的 Coding Agent。
- **标准 MCP Server (`vet_case_extractor.mcp_server`)**：通过 MCP `stdio` 协议暴露 7 个结构化工具。

---

## 2. 功能概览

- **逐页 PDF 质量评估 (`pdf/`)**：以 PyMuPDF 为首选引擎，`pdfplumber` 仅在单页提取失败时备选；综合字符数、CJK 占比、中国知网/方正排版特殊标点码位（`U+A3AC/U+A3AE/U+A3BB/U+1001B0`，确定性修复且不误触发 OCR）与真字形损坏率，按需触发 OCR。
- **RapidOCR 与双栏/检验表还原 (`ocr/`, `layout/`)**：基于 `rapidocr-onnxruntime`（可配置 250/300 dpi、`psutil` 内存自适应进程并发、ONNX Runtime 线程限制、单页 JSON 缓存断点续跑），还原 `[[通栏]]` / `[[左栏]]` / `[[右栏]]` 阅读顺序；结构化提取检验表 `LabTableRow`，自动检测 `×10⁹`/`×10¹²` 量级冲突、`×10%/L`、前导 `1` 伪影、行列错位与形近缩写，**严禁根据医学常识静默篡改原始数值**。
- **三级边界切分 (`segmentation/`)**：支持期刊单病例、一文多病例自动拆行、合订本按题名关键词定位目标文章与 `《标题》图版` 归并；支持扫描书籍章节识别、抗破折号变体标题、目录页码偏移（`page_offset`）与章内病例号连续性检查，生成逐例文本包 `cases/<case_id>/packet.txt`。
- **Raw / Normalized 双层规范化 (`normalization/`)**：无损保留逐字原文 `*_raw` 层；Normalized 层自动执行「Dx 书写五则」（物种前置不空格、主病在前、中文分号 `；`、四类括号三分法、缩写全称在前）、Rx 途径与频次规范化（`po/iv/im/sc` + `sid/bid/tid/qid`）、Tier C 外院既往史保留、推理剥离（`【原文推理/解读剥离】`）及死后剖检归类，所有改动均记录在 `normalization_changes`。
- **原文 BBox 证据溯源 (`provenance/`)**：将抽取内容与源 PDF SHA-256、页索引、原文引文和 BBox 坐标锚定；无法在原文定位支撑证据的字段显式标为 `ungrounded`，绝不伪造坐标。
- **自动 QA 与人工复核队列 (`validation/`)**：覆盖 Schema、连续性、治疗栏随访泄漏、一般体格检查(PE)与专科检查划分、Dx 五则、禁用占位符（`无/未提及/N/A/—`）、OCR 检验表异常与溯源校验，输出 `qa_report.json`、`qa_report.md` 和带页图路径的 `review_queue.json`。

---

## 3. 输入、输出及适用范围

- **输入**：单个 PDF 或包含多个 PDF 的目录（原生文字 PDF、纯扫描图像 PDF、图文混合 PDF、双栏排版期刊/合订本、整本扫描临床病例书）。
- **核心输出（默认位于 `<project_dir>/export/`）**：
  1. **期刊 9 列 Excel (`病例抽取_总表.xlsx`)**：
     `source | year | title | medical_history | medical_test | medical_treatment | Dx | follow-up | 备注`
  2. **书籍 7 列 Excel (`全书病例结构化抽取.xlsx`)**：
     `title | medical_history | medical_test | medical_treatment | Dx | follow-up | 备注`
     （两个 Excel 均含 `病例数据` 与 `说明` 双工作表，首行冻结 `A2`、自动换行、缺失字段留空 `""`。）
  3. **结构化与质检交付束**：`cases.jsonl`、`provenance.json`、`qa_report.json`、`qa_report.md`、`review_queue.json`、`run_metadata.json`。

### 离线与联网边界说明（重要）
- **完全本地离线执行的步骤**：`vetcase doctor`、`ingest`、`ocr`（RapidOCR 本地 ONNX 模型推理）、`index`、`prepare-agent-batch`、`import-cases`（含规则规范化与原文 BBox 溯源）、`qa`、`export`、`status`、`eval`。这些步骤**不发起任何外部网络请求**。
- **涉及模型推理的步骤（二选一）**：
  - `vetcase extract --provider gemini|openai_compat`：由本地 CLI 直接调用用户配置的远端 LLM API（会产生相应 API 调用与 Token 消耗）。
  - 交互式 Agent 抽取（`prepare-agent-batch` → Agent 生成 JSON → `import-cases`）：无需在 `.env` 配置外部 API Key，但若您使用的 Agent 客户端（如 Claude Code、Codex、Antigravity）本身连接云端大模型，Agent 读取的 `packet.txt` 文本会通过该 Agent 客户端发送至其模型服务商，**不属于本地离线推理**。

---

## 4. Quick Start（克隆、安装与首个合成案例运行）

任何新用户均可在全新目录中按以下步骤完成安装，并在**无需配置任何 API Key** 的情况下跑通内置合成样本的端到端流程。

### 4.1 克隆与安装（Linux / macOS / WSL）

```bash
git clone https://github.com/lucuslin/vet-case-extractor.git
cd vet-case-extractor

python3 -m venv .venv
source .venv/bin/activate

python -m pip install --upgrade pip
pip install -e ".[ocr,pdfplumber,gemini,dev]"
# 如需同时启用 MCP Server，可安装全部可选依赖：
# pip install -e ".[all]"

vetcase doctor
```

### 4.2 克隆与安装（Windows PowerShell）

```powershell
git clone https://github.com/lucuslin/vet-case-extractor.git
cd vet-case-extractor

python -m venv .venv
.\.venv\Scripts\Activate.ps1

python -m pip install --upgrade pip
pip install -e ".[ocr,pdfplumber,gemini,dev]"
# 如需同时启用 MCP Server：
# pip install -e ".[all]"

vetcase doctor
```

### 4.3 运行首个合成案例测试（无需 API Key，1 分钟验活）

仓库内置了明确标注为**合成测试数据**的期刊与扫描书 PDF（位于 [examples/synthetic/pdfs/](examples/synthetic/pdfs/)）。激活虚拟环境后运行：

```bash
# 1. 对合成期刊 PDF 执行本地导入、按需 OCR 与病例切分（--provider none 表示在语义抽取前停下）
vetcase run --input examples/synthetic/pdfs/合成_猫血支原体感染病例报告.pdf \
  --output ./runs/quickstart_journal --mode journal --provider none

# 2. 生成交互式 Agent 待抽批次与规范说明 EXTRACTION_SPEC.md
vetcase prepare-agent-batch --project ./runs/quickstart_journal --batch-size 8

# 3. 查看生成的病例文本包与状态
vetcase status --project ./runs/quickstart_journal
```

您也可以直接运行完整合成端到端演示脚本（包含合成 PDF 生成、RapidOCR、JSON 导入、QA 与 9 列/7 列 Excel 导出）：

```bash
python scripts/run_e2e_demo.py --work-dir ./runs/demo_e2e
```

---

## 5. CLI 使用指南

激活虚拟环境后可直接使用 `vetcase` 命令（未激活时使用 `.venv/bin/vetcase` 或 `.venv\Scripts\vetcase.exe`）：

| 子命令 | 常用参数 | 功能说明 |
|---|---|---|
| `vetcase doctor` | `[--json-output]` | 检查 Python、虚拟环境、依赖包、API Key 设置状态（不输出密钥）、硬件并发建议与跨平台中文字体状态 |
| `vetcase ingest` | `--input <PDF或目录>` `--project <目录>`（或 `-o/--output`）`[--mode auto\|journal\|book]` `[--page-offset N]` `[--target-article TEXT]` `[--ocr-policy ...]` | 递归发现 PDF、SHA-256 去重、提取原生文本层并评估每页是否需要 OCR |
| `vetcase ocr` | `--project <目录>` `[--dpi 250\|300]` `[--workers N]` `[--force]` `[--retry-failed]` | 对 `needs_ocr` 页面执行 RapidOCR、双栏版面恢复与检验表异常检测 |
| `vetcase index` | `--project <目录>` `[--mode auto\|journal\|book]` | 识别文章/章节/病例边界、检查书籍连续性、生成 `cases/<case_id>/packet.txt` |
| `vetcase prepare-agent-batch` | `--project <目录>` `[--batch-size 8]` `[--include-review]` | 导出 `EXTRACTION_SPEC.md` 与分批待抽清单供任意 Agent 交互式抽取 |
| `vetcase import-cases` | `--project <目录>` `--input <JSON>` `[--method agent_interactive]` `[--agent-platform NAME]` `[--model-name NAME]` `[--agent-version VER]` | 严格校验并导入 Agent 抽取的 JSON，执行规则规范化与原文 BBox 溯源 |
| `vetcase extract` | `--project <目录>` `[--provider gemini\|openai_compat]` `[--model NAME]` `[--workers N]` `[--retry-failed]` | 使用已配置的外部 LLM API 自动执行结构化抽取 |
| `vetcase qa` | `--project <目录>` `[--strict]` | 执行结构、语义、OCR 与溯源 QA，输出 `qa_report.json/.md` 与 `review_queue.json` |
| `vetcase export` | `--project <目录>` `[--format auto\|journal\|book]` `[--layer normalized\|raw]` `[--output <目录>]` `[--filename <名称.xlsx>]` | 导出 9 列/7 列 Excel 工作簿及完整结构化交付束 |
| `vetcase status` | `--project <目录>` | 查看项目各阶段进度统计 |
| `vetcase run` | `--input <路径>` `--output <目录>` `[--mode ...]` `[--provider gemini\|openai_compat\|none]` `[--format ...]` | 一键串联全流程（`--provider none` 在建索引后干净停下） |
| `vetcase eval` | `--pred <文件>` `--gold <文件>` `[--human-annotated]` `[--refuse-unannotated]` | 计算字段级 EM/Precision/Recall/F1（未标注合成数据自动附加非临床准确率警示） |

**标准退出码**：`0` 成功 · `1` 运行时错误 · `2` 参数或配置错误（如未配置 API Key 调用 `extract`） · `3` QA 未通过（`FAIL` 或 `--strict` 下存在 `REVIEW_REQUIRED`）。

---

## 6. Agent Skill 使用（Codex / Claude Code / Antigravity / 通用 Agent）

本项目采用**「一个通用 Skill 核心 + 多平台轻量引导文件」**架构，避免多套规则漂移：

- **通用 Skill 入口**：[SKILL.md](SKILL.md) + 按需加载的 [references/](references/) 领域细则。
- **仓库内置自动引导文件**：
  - [AGENTS.md](AGENTS.md)：在仓库根目录启动 **OpenAI Codex**、**Google Antigravity**、**Cursor** 等支持 `AGENTS.md` 的客户端时自动加载。
  - [CLAUDE.md](CLAUDE.md)：在仓库根目录启动 **Claude Code** 时自动加载。
- **跨目录安装为全局 Skill 及详细平台配置**：请参阅 **[docs/AGENT_INTEGRATION.md](docs/AGENT_INTEGRATION.md)**。

### Agent 交互式抽取标准流程（无需外部 API Key）

```bash
# 1. 本地预处理、OCR 与建索引
vetcase run --input ./my_pdfs --output ./runs/job_01 --mode journal --provider none

# 2. 生成待抽批次与 EXTRACTION_SPEC.md
vetcase prepare-agent-batch --project ./runs/job_01 --batch-size 8

# 3. Agent 读取 ./runs/job_01/agent_batches/EXTRACTION_SPEC.md 与 cases/<id>/packet.txt，写出 rows_01.json 后导入：
vetcase import-cases --project ./runs/job_01 --input ./runs/job_01/agent_batches/rows_01.json \
  --method agent_interactive --agent-platform <codex|claude-code|antigravity|custom> --model-name <model-id>

# 4. 运行 QA 与导出 Excel
vetcase qa --project ./runs/job_01
vetcase export --project ./runs/job_01 --format journal
```

---

## 7. MCP Server 使用

安装 `pip install -e ".[mcp]"`（或 `".[all]"`）后，可将 `vet-case-extractor` 作为标准 `stdio` MCP Server 挂载至 Claude Code、Codex、Antigravity、Gemini CLI、Cursor 等客户端：

- **启动命令**：`<path-to-venv-python> -m vet_case_extractor.mcp_server`
- **暴露工具（共 7 个）**：`ingest_pdf`、`prepare_agent_batch`、`import_cases`、`extract_cases`、`validate_cases`、`export_cases`、`get_status`。

**通用 MCP JSON 配置示例（`.mcp.json` / `settings.json` / `mcp_config.json`）**：

```json
{
  "mcpServers": {
    "vet-case-extractor": {
      "command": "/path/to/vet-case-extractor/.venv/bin/python",
      "args": ["-m", "vet_case_extractor.mcp_server"],
      "cwd": "/path/to/vet-case-extractor"
    }
  }
}
```

Windows PowerShell / TOML 等各客户端完整配置模板与排障指南见 **[docs/AGENT_INTEGRATION.md](docs/AGENT_INTEGRATION.md)**。

---

## 8. 支持的平台与已验证程度

| 平台 / 维度 | 支持方式 | 当前验证状态 |
|---|---|---|
| **Windows 11 (PowerShell)** | CLI (`vetcase`) + RapidOCR + Excel 导出 + MCP `stdio` | **已完整实测通过 (PASS)** |
| **Linux / WSL (Ubuntu)** | CLI (`vetcase`) + 核心 PDF/切分/规范化/QA/Excel 导出 | **已实测核心与 41 项单测通过 (PARTIAL：WSL 内未跑 RapidOCR 大包)** |
| **macOS** | 纯 Python `pathlib` + 跨平台字体探测 (`PingFang.ttc` 等) + POSIX CLI/MCP | **代码与路径适配已就绪（未在物理 macOS 宿主机实测）** |
| **Google Antigravity** | `AGENTS.md` / `SKILL.md` / CLI / 交互式抽取 | **已通过真实客户端全流程实测 (PASS)** |
| **Claude Code / OpenAI Codex / Gemini CLI / Cursor** | `CLAUDE.md` / `AGENTS.md` / `SKILL.md` / `.mcp.json` / `config.toml` | **已按官方规范适配并通过 CLI & MCP `stdio` 协议级集成测试（未启动对应外部客户端进程实测）** |

---

## 9. 配置外部 LLM Provider（可选）

仅当您需要通过 `vetcase extract`（或 MCP `extract_cases`）由本地程序直接请求远端大模型 API 时，才需配置 `.env`：

```bash
cp .env.example .env
```

```ini
# 官方 Google GenAI SDK (Gemini)
GEMINI_API_KEY=your_gemini_api_key
VETCASE_GEMINI_MODEL=gemini-2.5-flash

# 或任意 OpenAI 兼容接口 (OpenAI / DeepSeek / Qwen / vLLM / Ollama 等)
OPENAI_API_KEY=your_openai_or_compat_key
OPENAI_BASE_URL=https://api.openai.com/v1
VETCASE_OPENAI_MODEL=gpt-4o-mini
```

- 模型 ID 可通过 `.env`、[config/default.yaml](config/default.yaml) 或 CLI `--model` 参数自由替换，不绑定特定模型版本。
- 外部 API 调用可能产生由对应模型服务商收取的 Token 费用；Agent 客户端自身的会员订阅不等同于 API Key。

---

## 10. 样例数据与自动 QA

- **合成测试样例**：[examples/synthetic/](examples/synthetic/) 包含 7 份明确标注 `【SYNTHETIC TEST FIXTURE / 合成测试数据 — 非真实临床病例】` 的 PDF，以及导出的期刊 9 列 Excel（[病例抽取_总表.xlsx](examples/synthetic/journal_export/病例抽取_总表.xlsx)）和书籍 7 列 Excel（[全书病例结构化抽取.xlsx](examples/synthetic/book_export/全书病例结构化抽取.xlsx)）。
- **QA 与页图复核**：`vetcase qa` 绝不静默删改可疑临床句子或检验表数字，所有存疑项均汇总至 `<project>/qa/review_queue.json` 并关联 `<project>/pages_img/<doc_id>/pNNN.png` 供人工核对。

---

## 11. 测试、开发与文档分层

```bash
# 静态代码检查
ruff check src tests scripts

# 运行全量自动化测试（覆盖 PDF、RapidOCR、切分、规范化、溯源、QA、导出、MCP stdio 及 11 项异常恢复）
pytest -v
```

### 仓库文档导航
- **用户与操作文档**：[README.md](README.md)、[SKILL.md](SKILL.md)、[references/](references/)（5 份兽医领域抽取细则）
- **跨 Agent 与 MCP 集成文档**：[docs/AGENT_INTEGRATION.md](docs/AGENT_INTEGRATION.md)、[AGENTS.md](AGENTS.md)、[CLAUDE.md](CLAUDE.md)
- **架构决策、审计与历史验证文档**：
  - [RULE_DECISIONS.md](RULE_DECISIONS.md)：原版规则冲突与工程决策记录
  - [docs/RULE_TRACEABILITY.md](docs/RULE_TRACEABILITY.md)：22 项领域规则到代码与测试的追踪矩阵
  - [docs/PUBLIC_RELEASE_AUDIT.md](docs/PUBLIC_RELEASE_AUDIT.md)：公开发布去个人化与信息安全审查报告
  - [IMPLEMENTATION_REPORT.md](IMPLEMENTATION_REPORT.md)：2026-11-19 历史基准测试环境与五阶段验收记录
  - [KNOWN_LIMITATIONS.md](KNOWN_LIMITATIONS.md)：软件通用边界与特定测试环境未验证项说明

---

## 12. 已知限制、许可与免责声明

- **已知限制**：详见 [KNOWN_LIMITATIONS.md](KNOWN_LIMITATIONS.md)（包括复杂并排子表需结合 `pages_img/` 人工复核、远端 API 需用户自备密钥等）。
- **许可证说明**：`pyproject.toml` 声明为 `MIT`（详见 [docs/PUBLIC_RELEASE_AUDIT.md](docs/PUBLIC_RELEASE_AUDIT.md) 关于根目录 `LICENSE` 文件的发布提示）。
- **临床与学术免责声明**：本工具仅用于兽医文献结构化整理与 AI 评测数据集构建，不提供任何临床兽医诊断建议；内置合成样本严禁作为真实临床研究数据使用。
