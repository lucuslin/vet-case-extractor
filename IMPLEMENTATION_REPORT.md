# IMPLEMENTATION_REPORT.md — 实现总结、历史基准验证与五阶段验收矩阵

> **文档性质说明**：本文档记录 `vet-case-extractor` v1.0.0 在 **2026-11-19** 开发与公开发布重构阶段，于特定测试宿主机（Windows 11 + WSL Ubuntu）上实际执行的工程验证与基准测试结果。
> 文中的硬件配置（CPU 核数、可用内存、执行耗时）仅代表该次测试环境的客观快照，**并非软件运行的最低硬件要求**。新用户安装与跨平台使用指南请参阅 [README.md](README.md) 与 [docs/AGENT_INTEGRATION.md](docs/AGENT_INTEGRATION.md)。

---

## 一、历史验证环境快照（2026-11-19 实测）

### 1.1 Windows 11 测试环境（独立 `.venv` 虚拟环境）
由 `vetcase doctor` 实测获取：
- **OS / Python**：Windows 11 (`win32`)，Python `3.13.0`，`venv=True`
- **测试机硬件与并发自适应快照**：8 CPUs，测试时可用物理内存约 `4.8 / 15.8 GB`（`psutil` 自动将默认配置的 3 个 OCR 进程降级为推荐并发 `workers=1~2`）
- **已验证依赖版本**：
  - `pymupdf` `2.1`
  - `pdfplumber` `0.11.10`
  - `rapidocr-onnxruntime` `1.4.4` / `onnxruntime` `1.32.0`
  - `openpyxl` `3.1.5`
  - `pydantic` `2.14.3`
  - `google-genai` `3.6.1`
  - `httpx` `0.28.1`
  - `click` `8.5.1`
  - `mcp` `2.3.0`
  - 开发与测试依赖：`pytest` `9.2.0`、`ruff` `0.17.5`、`fpdf2` `2.9.0`
- **API Keys 状态**：`GEMINI_API_KEY=NOT SET`、`OPENAI_API_KEY=NOT SET`、`OPENAI_BASE_URL=NOT SET`
- **跨平台中文字体探测**：Windows 字体（`msyh.ttc`、`simsun.ttc`、`simhei.ttf`）探测为 `available`，非当前平台候选路径标记为 `not_applicable`。

### 1.2 Linux / WSL 子系统测试环境
- **WSL Ubuntu**：Linux Python `3.12.3`、`pip 24.0`、`venv`；在独立临时虚拟环境 `/tmp/vetcase_wsl_venv` 中完成 `pip install -e ".[dev]"`、`vetcase doctor` 及核心单元测试套件验证。

---

## 二、相比原版 legacy Skill 的核心工程演进

1. **从纯自然语言规程升级为标准 Python 包 + `vetcase` CLI + MCP Server**：支持独立终端运行、跨平台 Agent Skill 调用及标准 MCP `stdio` JSON-RPC 接入。
2. **逐页 PDF 质量评估 + 方正/CNKI 码位区分**：区分中国知网/方正 PDF 可确定修复的标点偏移（`U+A3AC/U+A3AE/U+A3BB/U+1001B0`）与真乱码/无文字层页，避免盲目对高可读原生文字页执行 OCR。
3. **双栏版面恢复与检验表防篡改异常检测**：自动恢复 `[[通栏]]`/`[[左栏]]`/`[[右栏]]` 阅读顺序；结构化提取 `LabTableRow`，捕获 `10⁹/10¹²`、`10%/L`、`33.3个`、前导 `1`、行列错位与单位异常，严格保留原值并转入复核队列。
4. **真文献驱动的三级切分加固**：针对真实期刊 PDF 中出现的文内换行伪标题、ASCII `;` 交叉引用行、多单位续行、版面右上栏前文残留 `参考文献` 误截断及 `《标题》图版` 多文章图版混排，完成抗干扰修复与回归测试。
5. **Raw / Normalized 双层设计 + 原文 BBox 溯源**：向下 100% 兼容 9 列/7 列 Excel 交付，同时在 `cases.jsonl` 与 `provenance.json` 中保存逐字原文、规则变更审计与页图坐标，无依据字段诚实标 `ungrounded`。
6. **去个人化与平台中立的 Agent 桥接**：消除对特定开发者机器路径或单一 Agent 平台的硬编码假设，`import-cases` 支持 `--agent-platform`、`--model-name`（默认 `unspecified-agent`）与 `--agent-version`。

---

## 三、真实测试执行记录（命令、退出码与结果）

| 阶段 | 执行命令（在仓库根目录下） | 退出码 | 实测结果摘要 |
|---|---|---:|---|
| 阶段 1：静态检查 | `ruff check src tests scripts` | `0` | `All checks passed!` |
| 阶段 1：完整测试集 | `pytest -v` | `0` | **全量测试通过**（覆盖 11 个测试文件：含真实 RapidOCR 扫描 PDF 识别、跨平台字体探测状态机、MCP `stdio` JSON-RPC 协议握手与工具调用、以及 11 项异常恢复测试） |
| 阶段 2：纯净 venv 安装 | 在独立临时虚拟环境中执行 `pip install -e ".[ocr,pdfplumber,gemini,dev]"`、`vetcase doctor`、`vetcase --help` | `0` | 全新隔离虚拟环境安装与 `vetcase doctor` 自检均通过 |
| 阶段 2：CLI 全部子命令帮助 | `vetcase --help` 及全部 12 个子命令 `--help` | `0` | 全部 12 个 CLI 子命令参数校验与帮助文档输出正常 |
| 阶段 3a：合成数据端到端 | `python scripts/run_e2e_demo.py --work-dir tmp/e2e_examples` | `0` | Journal QA `PASS` (2/2)、Book QA `PASS` (4/4)、产出 9 列/7 列 Excel 至 `examples/synthetic/`，评估标签强制标为 `UNANNOTATED_SYNTHETIC_NOT_CLINICAL_ACCURACY` |
| 阶段 3b：本地真实兽医期刊样本验证 | 使用 10 篇真实中文猫病临床期刊 PDF 在本地 gitignored 目录执行 `ingest` → `ocr` → `index` → `prepare-agent-batch` → 3 篇典型样本 `import-cases` → `qa` → `export --format journal` | `0` | 10 篇真实 PDF 全部导入且 10/10 完整切分（0 失败；自动甄别 5 页无文字/低 CJK 页并完成 RapidOCR；合订本正确剔除无关文章并保留对应图版）；导入 3 篇样本后 `completed=1, review_required=2`，准确检出真实表 1 行列交错与内嵌文字层 `×10%/L` / `33.3个` / `nmol/L` 异常，导出 9 列 Excel 与 15 条原文溯源锚点（真实文献仅在本地验证，未入库） |
| 阶段 4：异常与容错测试 | `pytest tests/test_exceptions_and_recovery.py -v` | `0` | 11/11 通过：空目录/缺失路径（exit 2）、SHA-256 重复去重、损坏 PDF 隔离、缺失 API Key（exit 2）、非法参数（exit 2）、单病例 LLM 失败隔离与 `--retry-failed` 恢复、截断 JSON 导入失败（exit 1）、损坏工作区 JSON 触发 QA 失败（exit 3）、OCR 缓存损坏清理重跑、ingest 恢复与文件名串扰 QA（exit 3）、429 限流异常隔离 |
| 阶段 5：WSL Linux 跨平台验证 | 在 WSL Ubuntu (Python `3.12.3`) 独立虚拟环境 `/tmp/vetcase_wsl_venv` 中执行 `pip install -e ".[dev]"`、`vetcase doctor` 与核心测试模块 | `0` | Linux Python `3.12.3` 下安装核心+测试依赖、`vetcase doctor` 及核心单测全部通过 |

---

## 四、独立验收矩阵（五阶段对照）

| 验收项 | 状态 | 证据 / 说明 |
|---|---|---|
| 原版 Skill 审计与 `legacy/` 历史保留 | **PASS** | `legacy/` 保留原版业务规范并清理个人路径；建立 [RULE_DECISIONS.md](RULE_DECISIONS.md)、[RULE_TRACEABILITY.md](docs/RULE_TRACEABILITY.md) 与 [PUBLIC_RELEASE_AUDIT.md](docs/PUBLIC_RELEASE_AUDIT.md) |
| 标准 `pyproject.toml` 与独立虚拟环境清洁安装 | **PASS** | 隔离虚拟环境 `pip install -e ".[ocr,pdfplumber,gemini,dev]"` 与 `vetcase doctor` 退出码 `0` |
| PyMuPDF 首选 + `pdfplumber` 失败页备选 + 多维文本质量判定 | **PASS** | `test_pdf.py` + 10 篇真实期刊 PDF 样本实测仅对 5 个扫描/低质页触发 OCR |
| 方正/CNKI 编码标点修复与真乱码区分 | **PASS** | `test_quality_distinguishes_founder_pua_from_corrupted_pua` + 真实方正编码 PDF 无误触 OCR |
| RapidOCR 真实运行、并发内存限制、ORT 线程约束、单页缓存断点续跑 | **PASS** | `test_real_rapidocr_dual_column_caching_and_page_error_recovery` + 合成扫描书/期刊 E2E + 真实扫描页 OCR 验证 |
| 双栏版面恢复（`[[通栏]]`/`[[左栏]]`/`[[右栏]]`） | **PASS** | `test_ocr_layout.py` + 双栏期刊与扫描书分栏还原验证 |
| 检验表结构化与 OCR 陷阱检测（不静默篡改原值） | **PASS** | `test_extract_lab_rows_*` (3 tests) + 真实期刊检验表异常捕获并保持原值标复核 |
| 期刊单病例/一文多病例/合订本切分 | **PASS** | `test_segmentation.py` (12/12) + 真实合订本与期刊样本 10/10 切分验证 |
| 扫描书籍章节识别、破折号变体、连续性与页码偏移 | **PASS** | `test_book_segmentation_*` + `test_cli_book_e2e_with_real_rapidocr` (4/4 病例, `page_offset=2`, `QA=PASS`) |
| 独立 LLM Provider 接口与官方 `google-genai` SDK 实现（含重试/错误/Schema 校验） | **PASS (离线 Mock/Stub)** | `test_extraction_providers.py` (6/6) 覆盖缺失 Key、结构化输出、429/超时/JSON 重试与并发隔离；**真实联网 API 调用为 `NOT TESTED`**（无密钥） |
| 真实联网 Gemini / OpenAI API 端到端抽取 | **NOT TESTED** | 测试环境未配置 `GEMINI_API_KEY` / `OPENAI_API_KEY`，严格遵守不越权、不伪造原则 |
| 跨平台交互式 Agent 抽取工作流（`prepare-agent-batch` + `import-cases`） | **PASS** | 平台中立默认元数据（`unspecified-agent`）及自定义 `--agent-platform`/`--model-name` 均验证通过 |
| 原有字段口径 + Dx 五则 + Rx 规范化 + Tier C + 剖检归类 + 推理剥离 | **PASS** | `test_normalization.py` (5/5) + `test_validation.py` (8/8 含 12 参数组) |
| Raw / Normalized 双层架构与原文 BBox 证据溯源 | **PASS** | `test_provenance.py` (3/3) + `cases.jsonl`/`provenance.json` 验证；无证据字段标 `ungrounded` 不伪造坐标 |
| 结构 + 语义 + OCR + 溯源 QA 与报告生成 | **PASS** | `qa_report.json`、`qa_report.md`、`review_queue.json` 验证通过 |
| Gold Standard 评测接口（禁止合成集伪报临床准确率） | **PASS** | `test_evaluate_against_gold_synthetic_tag_and_metrics` 强制附带 `UNANNOTATED_SYNTHETIC_NOT_CLINICAL_ACCURACY` |
| 期刊 9 列 & 书籍 7 列 Excel（`病例数据` + `说明`）导出 | **PASS** | `test_export.py` (3/3) + `examples/synthetic/` 导出验证 |
| 10+ 项异常与断点恢复机制 | **PASS** | `test_exceptions_and_recovery.py` (11/11) 全通过 |
| 跨平台字体探测 (`available`/`missing`/`not_applicable`/`unchecked`) | **PASS** | `utils/fonts.py` + `test_cross_platform_font_probe_statuses` 验证通过 |
| 跨 Agent 集成文档与引导文件 (`SKILL.md`, `AGENTS.md`, `CLAUDE.md`, `docs/AGENT_INTEGRATION.md`) | **PASS** | 覆盖 Codex、Claude Code、Antigravity、Gemini CLI、Cursor 及通用 MCP 客户端 |
| MCP Server (`mcp_server.py`) `stdio` JSON-RPC 协议握手与工具发现/调用 | **PASS (协议级集成测试)** | 子进程 `stdio` JSON-RPC (`initialize` → `tools/list` → `tools/call`) 自动化测试通过；外部独立桌面客户端 GUI 未做人工连调（`PARTIAL`） |
| Linux / WSL 跨平台验证 | **PARTIAL** | WSL Python `3.12.3` 安装 `.[dev]`、`vetcase doctor` 与核心单测全部通过；WSL 内未额外安装 RapidOCR 大包跑图版推理 |
