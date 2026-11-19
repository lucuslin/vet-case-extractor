# 跨 Agent 平台集成与调用指南（Agent Integration Guide）

本文档说明如何将 `vet-case-extractor` 接入不同的 AI Coding Agent 与 MCP 客户端。

> **重要说明**：仓库根目录的 [SKILL.md](../SKILL.md) 是领域操作规程文件，**不等于所有客户端都会自动发现并加载它**。不同客户端的技能目录、项目指令文件（`AGENTS.md` / `CLAUDE.md` / `GEMINI.md`）与 MCP 配置文件位置各不相同，请按本文档对应平台章节配置。

---

## 一、支持平台与验证等级矩阵

为确保工程诚实性，下表严格区分各接入方式的**真实客户端验证**、**标准协议集成测试**与**官方配置规范核对**：

| 接入方式 / 目标平台 | 接入机制 | 验证状态 | 验证依据与说明 |
|---|---|---|---|
| **通用 CLI (`vetcase`)**（任何具备终端执行权限的 Agent） | 终端执行 `vetcase` 子命令 + 文件读写 | **已实测验证 (PASS)** | 在 Windows 11 PowerShell 与 WSL Ubuntu (Python 3.12.3) 完整实测通过 |
| **交互式 Agent 批次桥接 (`prepare-agent-batch` + `import-cases`)** | CLI 导出文本包 → Agent 生成 JSON → CLI 校验导入 | **已实测验证 (PASS)** | 在合成数据集与本地真实论文上完成端到端闭环验证 |
| **MCP Server (`vet_case_extractor.mcp_server`)** | 标准 MCP `stdio` JSON-RPC 2.0 协议（7 个工具） | **已通过协议级集成测试 (PASS)** | 通过独立子进程 `stdio` JSON-RPC (`initialize` → `tools/list` → `tools/call`) 自动化测试验证 |
| **Google Antigravity (AGY)** | 工作区 `AGENTS.md` / `.agents/skills/` / `~/.gemini/config/` | **已通过真实客户端验证 (PASS)** | 在 Antigravity 会话中完成全流程开发与交互式抽取实测 |
| **OpenAI Codex (CLI / IDE)** | 仓库根 `AGENTS.md` / `~/.codex/skills/` / `~/.codex/config.toml` | **官方规范核对 + CLI/MCP 协议兼容** | 已提供 `AGENTS.md` 与标准 `SKILL.md` / TOML 配置模板（未在宿主机器启动 Codex 客户端进程实测） |
| **Claude Code** | 仓库根 `CLAUDE.md` / `~/.claude/skills/` / `.mcp.json` | **官方规范核对 + CLI/MCP 协议兼容** | 已提供 `CLAUDE.md` 与标准 `SKILL.md` / `.mcp.json` 模板（未在宿主机器启动 Claude Code 客户端进程实测） |
| **Gemini CLI** | `~/.gemini/settings.json` (`mcpServers`) / `GEMINI.md` | **官方规范核对 + MCP 协议兼容** | 遵循 `~/.gemini/settings.json` 官方 MCP 配置格式 |
| **Cursor / Windsurf / Cline 等通用 Agent** | `.cursor/mcp.json` / `AGENTS.md` / 终端 CLI | **官方规范核对 + CLI/MCP 协议兼容** | 支持通过 `AGENTS.md` 导航调用 CLI 或挂载 stdio MCP Server |

---

## 二、通用 Agent 运行条件与工作流架构

无论使用哪个 Agent 平台，`vet-case-extractor` 均采用**「确定性本地管线 + 可插拔语义抽取」**架构：

```text
PDF 输入 ──► [1. vetcase ingest] (本地确定性：SHA-256 去重、PyMuPDF/pdfplumber、质量评分)
         ──► [2. vetcase ocr]    (本地确定性：RapidOCR、双栏版面恢复、检验表结构化与陷阱检测)
         ──► [3. vetcase index]  (本地确定性：期刊/合订本/书籍三级切分、生成 cases/<id>/packet.txt)
         ──► [4. 语义结构化抽取] (二选一)
               ├─► 路径 A (无外部 API Key / Agent 交互式)：
               │     vetcase prepare-agent-batch ──► Agent 读 packet.txt 写 JSON ──► vetcase import-cases
               └─► 路径 B (已配置 GEMINI_API_KEY / OPENAI_API_KEY)：
                     vetcase extract --provider gemini|openai_compat
         ──► [5. vetcase qa]     (本地确定性：结构 + 语义 + OCR + 溯源校验、生成 review_queue.json)
         ──► [6. vetcase export] (本地确定性：期刊 9 列 / 书籍 7 列 Excel + JSONL + Provenance)
```

### 关键边界说明
1. **无外部 API Key 时的交互式抽取（路径 A）**：
   - 不需要在 `.env` 中配置 `GEMINI_API_KEY` 或 `OPENAI_API_KEY`。
   - 由当前运行的 Agent（如 Claude Code、Codex、Antigravity）自身读取 `<project>/cases/<case_id>/packet.txt` 和 `<project>/agent_batches/EXTRACTION_SPEC.md`，利用 Agent 会话自身的推理能力生成 JSON，再交给 `vetcase import-cases` 做严格 Pydantic Schema 校验、Dx/Rx 规则规范化与原文 BBox 坐标锚定。
   - 导入时建议显式传入当前平台与模型标识：
     ```bash
     vetcase import-cases --project ./runs/job1 --input ./runs/job1/agent_batches/rows_01.json \
       --method agent_interactive --agent-platform claude-code --model-name claude-sonnet-4-5
     ```
     若未指定，`--agent-platform` 默认为 `unspecified`，`--model-name` 默认为 `unspecified-agent`（不会假定任何特定平台）。
2. **有外部 API Key 时的独立抽取（路径 B）**：
   - 在 `.env` 或环境变量中设置 `GEMINI_API_KEY` 或 `OPENAI_API_KEY` 后，Agent（或用户终端）直接执行 `vetcase extract --project ./runs/job1 --provider gemini`。
   - 注意：拥有某个 Agent 客户端的会员订阅（如 ChatGPT Plus、Claude Pro、Google AI 订阅）**不等于**拥有无条件的外部 API Key 配额。

---

## 三、各平台安装、发现与接入方法

前置条件：先克隆仓库并完成虚拟环境安装（详见 [README.md](../README.md)）：

```bash
git clone https://github.com/lucuslin/vet-case-extractor.git
cd vet-case-extractor
python3 -m venv .venv
# Linux/macOS/WSL:
.venv/bin/pip install -e ".[all]"
# Windows PowerShell:
# .\.venv\Scripts\pip.exe install -e ".[all]"
```

### 3.1 OpenAI Codex（CLI / IDE 环境）

1. **直接打开本仓库使用（自动发现）**：
   - 本仓库根目录已包含 [AGENTS.md](../AGENTS.md)，Codex 在仓库目录启动时会自动读取 `AGENTS.md` 并按指引加载 [SKILL.md](../SKILL.md) 与 `references/`。
2. **安装为全局 Codex Skill（跨目录复用）**：
   - Codex 扫描 `~/.codex/skills/<skill-name>/SKILL.md`。将 `SKILL.md` 与 `references/` 复制或软链接至 `~/.codex/skills/vet-case-extractor/`：
     ```bash
     # Linux / macOS / WSL
     mkdir -p ~/.codex/skills/vet-case-extractor
     cp -R SKILL.md references ~/.codex/skills/vet-case-extractor/
     ```
     ```powershell
     # Windows PowerShell
     New-Item -ItemType Directory -Force "$HOME\.codex\skills\vet-case-extractor" | Out-Null
     Copy-Item -Recurse -Force SKILL.md, references "$HOME\.codex\skills\vet-case-extractor\"
     ```
3. **在 Codex 中配置 MCP Server（`~/.codex/config.toml`）**：
   ```toml
   [mcp_servers.vet-case-extractor]
   command = "/path/to/vet-case-extractor/.venv/bin/python"
   args = ["-m", "vet_case_extractor.mcp_server"]
   cwd = "/path/to/vet-case-extractor"
   ```
   （Windows 将 `command` 替换为 `"C:\\path\\to\\vet-case-extractor\\.venv\\Scripts\\python.exe"`。）

---

### 3.2 Claude Code

1. **直接打开本仓库使用（自动发现）**：
   - 本仓库根目录已提供 [CLAUDE.md](../CLAUDE.md)，Claude Code 进入仓库时自动加载并引导至 [SKILL.md](../SKILL.md) 与 `vetcase` CLI。
2. **安装为全局或项目级 Claude Code Skill**：
   - Claude Code 支持 `~/.claude/skills/<skill-name>/SKILL.md`（用户级）或 `<workspace>/.claude/skills/<skill-name>/SKILL.md`（项目级）：
     ```bash
     # Linux / macOS / WSL
     mkdir -p ~/.claude/skills/vet-case-extractor
     cp -R SKILL.md references ~/.claude/skills/vet-case-extractor/
     ```
     ```powershell
     # Windows PowerShell
     New-Item -ItemType Directory -Force "$HOME\.claude\skills\vet-case-extractor" | Out-Null
     Copy-Item -Recurse -Force SKILL.md, references "$HOME\.claude\skills\vet-case-extractor\"
     ```
3. **配置 MCP Server（项目级 `.mcp.json` 或 `~/.claude.json`）**：
   在工作区根目录创建 `.mcp.json`（或使用 `claude mcp add`）：
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

---

### 3.3 Google Antigravity (AGY)

1. **直接打开本仓库使用**：
   - Antigravity 自动识别仓库根目录的 [AGENTS.md](../AGENTS.md) 与 [SKILL.md](../SKILL.md)。
2. **安装为工作区或全局 Skill**：
   - 工作区级：复制到 `<workspace>/.agents/skills/vet-case-extractor/`（含 `SKILL.md` 与 `references/`）。
   - 全局级：复制到 `~/.gemini/config/skills/vet-case-extractor/`。
3. **配置 MCP Server（`~/.gemini/config/mcp_config.json`）**：
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

---

### 3.4 Gemini CLI

1. **指令加载**：在项目根目录或 `~/.gemini/GEMINI.md` 中引用 `AGENTS.md` / `SKILL.md`。
2. **配置 MCP Server（`~/.gemini/settings.json`）**：
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

---

### 3.5 Cursor 及其他通用 Coding Agent

- **Cursor**：在 `<workspace>/.cursor/mcp.json` 或 `~/.cursor/mcp.json` 中使用与上方相同的 `mcpServers` JSON 结构挂载 `vet_case_extractor.mcp_server`；并通过仓库根目录的 `AGENTS.md` 获取 CLI 工作流说明。
- **任意支持终端的 Coding Agent（Aider / Cline / Windsurf / OpenHands 等）**：无需特殊插件，直接让 Agent 阅读 `SKILL.md` 并通过终端运行 `vetcase` 命令即可完成从 PDF 预处理、批次导出、JSON 校验导入到 QA 与 Excel 导出的完整闭环。

---

## 四、MCP Server 详细说明与故障排查

### 4.1 启动命令与环境要求

- 启动命令：`<venv-python> -m vet_case_extractor.mcp_server`
- 通信协议：标准 MCP `stdio`（JSON-RPC 2.0 over stdin/stdout）
- 依赖要求：需安装 `[mcp]` 额外依赖（`pip install -e ".[mcp]"` 或 `pip install -e ".[all]"`）。
- 环境变量（全部可选）：
  - `VETCASE_CONFIG_DIR`：指定 `config/` 目录路径（当 `cwd` 不在仓库根目录时使用）。
  - `GEMINI_API_KEY` / `OPENAI_API_KEY`：仅在调用 `extract_cases` 工具时才需要；使用 `ingest_pdf`、`prepare_agent_batch`、`import_cases`、`validate_cases`、`export_cases`、`get_status` 均不需要外部 API Key。

### 4.2 暴露的 7 个 MCP 工具

| 工具名 | 参数 | 功能说明 |
|---|---|---|
| `ingest_pdf` | `project_dir`, `input_path`, `mode="auto"`, `page_offset=0`, `target_article_query=None`, `run_ocr=True`, `build_index=True` | 导入 PDF、按需执行 RapidOCR 并建立三级病例索引 |
| `prepare_agent_batch` | `project_dir`, `output_dir=None`, `batch_size=8`, `include_review=False` | 导出 `EXTRACTION_SPEC.md` 与分批待抽清单供 Agent 交互式抽取 |
| `import_cases` | `project_dir`, `json_path`, `method="agent_interactive"`, `model_name="unspecified-agent"`, `agent_platform="mcp-client"`, `agent_version=""` | 校验并导入 Agent 抽取的 JSON，执行规则规范化与原文证据溯源 |
| `extract_cases` | `project_dir`, `provider="gemini"`, `model=None`, `retry_failed=False` | 调用已配置 API Key 的外部 LLM Provider 自动抽取 |
| `validate_cases` | `project_dir` | 运行结构、语义、OCR 与溯源 QA 并落盘 `qa_report.json/.md` 与 `review_queue.json` |
| `export_cases` | `project_dir`, `output_dir=None`, `format_mode="auto"`, `layer="normalized"`, `excel_filename=None` | 导出 9 列/7 列 Excel 工作簿及完整数据集束 |
| `get_status` | `project_dir` | 查询项目当前文档、OCR、索引、抽取、QA 与导出状态 |

### 4.3 Windows 与 Linux/macOS 标准 `stdio` 配置对照

**Windows 示例**（注意 JSON 中反斜杠需转义为 `\\` 或使用正斜杠 `/`）：

```json
{
  "mcpServers": {
    "vet-case-extractor": {
      "command": "C:/path/to/vet-case-extractor/.venv/Scripts/python.exe",
      "args": ["-m", "vet_case_extractor.mcp_server"],
      "cwd": "C:/path/to/vet-case-extractor",
      "env": {
        "VETCASE_LOG_LEVEL": "WARNING"
      }
    }
  }
}
```

**Linux / macOS / WSL 示例**：

```json
{
  "mcpServers": {
    "vet-case-extractor": {
      "command": "/path/to/vet-case-extractor/.venv/bin/python",
      "args": ["-m", "vet_case_extractor.mcp_server"],
      "cwd": "/path/to/vet-case-extractor",
      "env": {
        "VETCASE_LOG_LEVEL": "WARNING"
      }
    }
  }
}
```

### 4.4 权限范围与故障排查

- **文件系统权限**：MCP Server 与 CLI 仅在传入的 `project_dir` 和 `output_dir` 内创建文件，对 `input_path` 下的原始 PDF 仅做只读打开。请确保宿主 Agent 授予工作目录读写权限。
- **常见故障排查**：
  1. `ModuleNotFoundError: No module named 'mcp'`：请确认 MCP 配置中的 `command` 指向已安装 `.[mcp]` 或 `.[all]` 的 `.venv` Python 解释器，而非系统全局 Python。
  2. `找不到 config/default.yaml`：在 MCP 配置中设置 `"cwd": "/path/to/vet-case-extractor"` 或设置环境变量 `"VETCASE_CONFIG_DIR": "/path/to/vet-case-extractor/config"`。
  3. 检验表告警或 `REVIEW_REQUIRED`：打开 `<project_dir>/qa/review_queue.json` 中列出的 `image_paths`（`<project_dir>/pages_img/<doc_id>/pNNN.png`）核对原页图，禁止盲目覆盖原始数值。
