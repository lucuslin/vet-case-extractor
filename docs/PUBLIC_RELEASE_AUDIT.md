# 公开发布信息安全、去个人化与跨平台可移植性审查报告（Public Release Audit）

- **审查日期**：2026-11-19
- **审查分支**：`refactor/public-release-portability`
- **目标仓库**：`https://github.com/lucuslin/vet-case-extractor`

---

## 一、审查范围

本次审查覆盖仓库内所有 Git 跟踪文件及 Git 提交历史，包括：
1. 文档与规范：`README.md`、`SKILL.md`、`AGENTS.md`、`CLAUDE.md`、`RULE_DECISIONS.md`、`IMPLEMENTATION_REPORT.md`、`KNOWN_LIMITATIONS.md`、`references/*.md`、`docs/*.md`、`legacy/*`。
2. 配置与构建：`pyproject.toml`、`requirements.txt`、`.env.example`、`.gitignore`、`config/*.yaml`、`schemas/*.schema.json`。
3. 源码与脚本：`src/vet_case_extractor/**/*.py`、`scripts/*.py`、`tests/*.py`。
4. 合成示例产物：`examples/synthetic/**`（PDF 清单、JSON、JSONL、Markdown、Excel）。
5. Git 历史提交与树对象（`1676d2f`..`764b079`）。

---

## 二、发现的问题清单与整改处置

| 编号 | 类别 | 发现位置 | 问题描述 | 整改方式与结果 |
|---|---|---|---|---|
| PRA-01 | 开发者本地路径 | `README.md` | Quick Start 中包含开发者本机绝对路径与 WSL 挂载路径 | 改为从 `git clone https://github.com/lucuslin/vet-case-extractor.git` + `cd vet-case-extractor` 开始的通用跨平台安装步骤 |
| PRA-02 | 开发者本地路径 | `tests/test_pdf.py` | 硬编码了开发者本地未入库 PDF 目录路径与具体文件名 | 移除硬编码本地路径与文件名；改为可选环境变量 `VETCASE_LOCAL_PAPERS_DIR` 驱动的通用语料冒烟测试（未设置时自动 skip） |
| PRA-03 | 开发者本地路径 & 字体路径 | `src/vet_case_extractor/pipeline.py`, `scripts/generate_synthetic_fixtures.py`, `tests/test_ocr_layout.py`, `examples/synthetic/pdfs/synthetic_fixtures_manifest.json` | `run_doctor()` 仅检查 Windows 字体路径；合成清单写入了 Windows 字体绝对路径 | 新增 `src/vet_case_extractor/utils/fonts.py`，支持 Windows / Linux / WSL / macOS 及 `VETCASE_CJK_FONT`，区分 `available` / `missing` / `not_applicable` / `unchecked` 四种状态；清单仅记录字体文件名 `msyh.ttc` |
| PRA-04 | 历史遗留路径与代号 | `legacy/README.md`, `legacy/SKILL.md`, `legacy/requirements.txt`, `tests/test_export.py` | `legacy/` 包含旧版 WorkBuddy 虚拟环境路径及个人批次代号 | 保留 `legacy/` 全部领域业务规则，将旧版固定虚拟环境路径替换为通用 `<path-to-venv>` 占位符，将批次代号替换为通用 `SampleBatch_` |
| PRA-05 | 历史测试报告路径 | `IMPLEMENTATION_REPORT.md`, `RULE_DECISIONS.md`, `KNOWN_LIMITATIONS.md` | 包含开发期本机工作区路径，且未明确区分单次测试机硬件快照与软件通用要求 | 移除本机绝对路径，明确标注 `IMPLEMENTATION_REPORT.md` 为 2026-11-19 历史基准测试记录，将通用限制与单次环境未测项分离 |
| PRA-06 | AGY 专属默认绑定 | `src/vet_case_extractor/cli.py`, `pipeline.py`, `providers/agent_bridge.py`, `examples/synthetic/**` | `vetcase import-cases --model-name` 默认值为 `agy-agent`，合成产物 `provenance.json` / `run_metadata.json` 带有 `agy-agent` 及反斜杠临时路径 | 将 `--model-name` 默认值改为平台中立的 `unspecified-agent`，新增 `--agent-platform`（默认 `unspecified`）与 `--agent-version`；导出路径统一为 POSIX 格式并重新生成 `examples/synthetic/` |
| PRA-07 | 跨 Agent 接入文档不足 | `SKILL.md`, `docs/` | 缺少 Codex、Claude Code、Gemini CLI、Cursor 等平台的明确发现路径与 MCP 配置示例 | 重构平台中立的 `SKILL.md`，新增根目录 `AGENTS.md`、`CLAUDE.md` 与完整的 [docs/AGENT_INTEGRATION.md](AGENT_INTEGRATION.md)，并在 `mcp_server.py` 中补齐 `prepare_agent_batch` 与 `import_cases` 工具 |

---

## 三、敏感凭证与私人临床资料审计结果

1. **API Key / Token / 密钥 / Cookie**：
   - 全仓库文件与 Git 历史提交扫描结果：**未发现任何真实 API Key、Token、私钥或凭证**。
   - `.env.example` 仅包含变量名占位符；`.gitignore` 已拦截 `.env`、`.env.*`、`*.pem`、`*.key`、`*credentials*.json`、`*secret*.json`。
2. **私人或受版权保护的真实临床 PDF 资料**：
   - 全仓库文件与 Git 历史树对象检查结果：**从未将任何真实临床论文 PDF 或本地 `runs/` 抽取产物提交至 Git 历史**。
   - `examples/synthetic/` 下的 7 份 PDF 均由 `scripts/generate_synthetic_fixtures.py` 纯代码生成，且页面顶部与元数据均带有 `【SYNTHETIC TEST FIXTURE / 合成测试数据 — 非真实临床病例】` 与 `UNANNOTATED_SYNTHETIC_NOT_CLINICAL_ACCURACY` 标记。

---

## 四、Git 历史残留评估与需人工决定事项

1. **Git 历史提交说明（无需强制重写，供仓库所有者知悉）**：
   - 当前分支已清除所有工作区路径、旧版 WorkBuddy 路径与 `agy-agent` 默认值。
   - 早期提交（`1676d2f`..`764b079`）的历史 diff 中曾出现过开发目录路径字符串与 Git 提交者邮箱（`git config user.name/user.email`），但**不包含任何密钥、密码或私人临床数据**。根据安全发布原则，本次未执行 `git push --force` 重写历史；如仓库所有者希望彻底抹除早期 commit diff 中的本地目录名，可自行决定是否在合并后做一次 squash / orphan branch 归档。
2. **许可证文件（建议仓库所有者确认）**：
   - `pyproject.toml` 中已声明 `license = { text = "MIT" }`，但仓库根目录尚未放置独立的 `LICENSE` 文本文件。为避免擅自替版权所有者签署法律文本，建议仓库所有者确认采用 MIT 许可后，在根目录补充标准 `MIT LICENSE` 文件（含版权年份与署名）。
