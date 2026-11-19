# KNOWN_LIMITATIONS.md — 已知限制、软件边界与历史测试环境未验证项

本文档将**软件自身的通用技术边界**与**单次基准测试环境的未执行项**分开说明，帮助使用者与贡献者准确评估适用范围。

---

## 一、软件通用技术与领域边界（适用于所有用户）

### 1. 复杂多栏合并检验表与低质量扫描件需结合页图人工复核
- **左右并排双栏子表**（如部分期刊在同一表框内左右并列两组血常规指标）或**原 PDF 内嵌低质量 OCR 文字层异常**（如把上标 `×10⁹/L` 嵌为 `×10%/L`、把 `↑` 嵌为 `个`、把 `mmol/L` 嵌为 `nmol/L`、或小数点两侧带空格导致列切分偏移）：
  - 工具链严格遵守**「禁止根据医学常识静默篡改原始检验数字」**的原则。
  - `ocr/tables.py` 会将检出的量级冲突、前导 `1` 伪影、行列错位、参考区间倒置等写入 `LabTableRow.anomalies`，自动将病例标记为 `review_required`，并在 `<project>/qa/review_queue.json` 中关联 `<project>/pages_img/<doc_id>/pNNN.png` 页图路径，**必须由人工核对原页图后确认**。
- **严重倒置、倾斜或模糊扫描件**：
  - 默认配置关闭了方向分类器（`use_angle_cls: false`）以提升常规正向排版书籍/期刊的 OCR 吞吐；若遇到 90°/180° 旋转页面，需在 `config/default.yaml` 中开启 `use_angle_cls: true` 并结合页图复核。

### 2. 规则与启发式切分边界
- **混合「事实 + 推理」长句**：当同一分句既包含客观检查发现又包含「提示/疑似」等推理词时，为防止误删客观临床事实，规则后处理会保留原句并标记 `review_required` 交由人工确认。
- **非标准排版合订本/剪报 PDF**：对于缺少常规期刊元数据（无《刊名》、无中图分类号/收稿日期、无章节号）的非标拼合 PDF，自动切分器会设置 `boundary_uncertain=true` 并在 QA 报告中提示人工核对页码范围。

### 3. 合成测试数据与中文字体依赖边界
- **中文字体仅合成测试脚本需要**：`vetcase` 的核心生产功能（PDF 读取、RapidOCR、病例切分、抽取、QA、Excel 导出）**不依赖**本机安装中文字体；仅在运行 `scripts/generate_synthetic_fixtures.py` 动态渲染合成测试 PDF 时才需要系统存在至少一种中文字体（可通过 `VETCASE_CJK_FONT` 指定路径，或在 Linux 安装 `fonts-noto-cjk` / `fonts-wqy-zenhei`）。
- **合成数据不可用于临床准确率声明**：`examples/synthetic/` 下的全部数据均为程序生成的合成测试样例，`vetcase eval` 在未传入 `--human-annotated` 时会强制标记 `UNANNOTATED_SYNTHETIC_NOT_CLINICAL_ACCURACY`。

### 4. 离线与远程推理的隐私边界
- 本地预处理（`ingest`、`ocr`、`index`）、JSON 导入校验（`import-cases`）、`qa` 与 `export` 完全在本地离线执行。
- 若使用 `vetcase extract`（调用外部 Gemini / OpenAI-compatible API）或在连接云端大模型的 Agent 客户端（如 Claude Code、Codex、Antigravity 等）中执行交互式抽取，病例文本会发送至所选的模型服务商。处理涉密或未脱敏私人临床资料时，请使用本地部署的 OpenAI-compatible 模型服务（如本地 Ollama / vLLM）或取得合规授权。

---

## 二、2026-11-19 基准测试环境的未验证项（非软件缺陷）

以下项目在 2026-11-19 的开发验收环境中因缺少外部凭证或特定硬件宿主而未执行实测，已在 [IMPLEMENTATION_REPORT.md](IMPLEMENTATION_REPORT.md) 中如实标记：

1. **远端 LLM API 真实联网调用 (`NOT TESTED`)**：
   - 测试环境未配置 `GEMINI_API_KEY` 或 `OPENAI_API_KEY`。`GeminiProvider` 与 `OpenAICompatProvider` 的请求构造、Pydantic Schema 校验、缺失 Key 报错（退出码 `2`）、429 限流/超时/JSON 重试及并发错误隔离均已通过离线 Mock/Stub 测试验证；真实在线 API 调用需用户自行配置有效密钥后运行。
2. **Linux / WSL 下 RapidOCR 完整图像推理 (`PARTIAL`)**：
   - 已在 WSL Ubuntu (Python `3.12.3`) 中实测通过核心包安装、`vetcase doctor` 及 41 项核心单元测试；为避免在 WSL 临时目录重复下载大型 ONNX 运行时，RapidOCR 真实图像推理端到端测试在 Windows 11 主环境中完成。
3. **物理 macOS 宿主机与外部 Agent 客户端 GUI 实测 (`PARTIAL`)**：
   - macOS 字体路径（`PingFang.ttc` 等）与 POSIX 路径处理已通过单元测试验证，但未在物理 macOS 机器上运行测试。
   - Claude Code、OpenAI Codex、Gemini CLI、Cursor 的集成文件（`CLAUDE.md`、`AGENTS.md`、`SKILL.md`、MCP 配置模板）已按各平台官方规范编写，且 `vet_case_extractor.mcp_server` 已通过独立子进程 `stdio` JSON-RPC 2.0 协议级握手与工具调用测试，但未在上述外部客户端桌面进程内进行人工交互录屏验证。
