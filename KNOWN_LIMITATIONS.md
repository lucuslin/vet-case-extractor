# KNOWN_LIMITATIONS.md — 已知限制、条件性能力与未验证范围

本文档如实记录 `vet-case-extractor` v1.0 在当前环境下客观存在的边界限制、未验证能力及原因，不掩盖未测试项。

---

## 1. 远端 LLM API 真实联网抽取未执行（`NOT TESTED`）

- **原因**：本机未配置 `GEMINI_API_KEY`、`GOOGLE_API_KEY` 或 `OPENAI_API_KEY`（经 `vetcase doctor` 核验均为 `NOT SET`）；按项目安全与合规纪律，不得通过非官方渠道或浏览器会话绕过 API 授权。
- **已验证部分**：
  - `GeminiProvider`（官方 `google-genai` SDK，`response_mime_type="application/json"`，`response_schema`）、`OpenAICompatProvider`、`get_provider`、缺失 Key 报错（`ProviderConfigError`、退出码 `2`）、429 限流/超时/JSON 解析重试、分块与并发抽取隔离均已通过离线单元测试与 Mock Transport / Stub 测试验证。
  - 真实端到端语义抽取已通过显式区分的 **交互式 Agent 路径**（`vetcase prepare-agent-batch` + `vetcase import-cases --method agent_interactive`）在合成数据与本地真实兽医论文上完成闭环验收。
- **未验证部分**：真实网络环境下调用 Gemini / OpenAI 远端 API 端点的结构化输出延迟、真实 Token 计费与长文档在线吞吐未执行真实联网验收（`NOT TESTED`）。

---

## 2. Linux / WSL 环境覆盖范围（`PARTIAL`）

- **已验证部分**：在 Windows 11 宿主机的 WSL `Ubuntu`（Python `3.12.3`）中新建隔离虚拟环境 `/tmp/vetcase_wsl_venv`，执行 `pip install -e ".[dev]"`（核心包 + 测试依赖）、`vetcase doctor` 与 5 个核心单元测试模块（`test_normalization.py`、`test_segmentation.py`、`test_validation.py`、`test_export.py`、`test_provenance.py`），**41 项测试全部通过**（`2.26s`）。
- **未覆盖部分**：
  - WSL 中未额外安装 `[ocr,pdfplumber,gemini,mcp]` 可选大包，未在 WSL 内执行 RapidOCR 推理。
  - `vetcase doctor` 的 `chinese_fonts` 检测列表当前仅检查 Windows 字体路径（`C:\Windows\Fonts\...`），在 Linux/WSL 下全部显示 `missing`；在无桌面字体的精简 Linux 容器中运行 `scripts/generate_synthetic_fixtures.py` 时，需先安装中文字体（`fonts-noto-cjk` / `fonts-wqy-zenhei`）或通过 `--font` 指定路径。

---

## 3. 复杂多栏检验表与严重降质扫描件限制

- **左右并排双栏子表**（如真实论文《猫乳腺纤维腺瘤样增生的病理诊断和治疗》p1 表 1，同一图版内左右各列一组血常规指标）及**原 PDF 内嵌文字层异常**（如《猫胱氨酸结石的病例报告》p2 表 1 含 `×10%/L`、`33.3个`、`nmol/L` 与小数点空格断裂）：
  - 工具链严格遵守「不基于医学常识静默篡改原始数值」原则，`ocr/tables.py` 会将异常写入 `LabTableRow.anomalies` 并自动将病例标为 `review_required`，在 `qa/review_queue.json` 中给出页码与 `pages_img/` 路径供人工核对页图确认，不能保证全自动无错拆解此类复杂双栏合并表格。
- **严重模糊、倾斜或印章遮挡页**：已关闭角度分类（`use_angle_cls=false`）以优化正常排版扫描书速度；若扫描件出现 180° 倒置或极端模糊，仍需人工检查页图或开启相应 OCR 配置。

---

## 4. 规则与启发式边界限制

- **混合「事实 + 推理」长句**：当同一长句同时包含客观检验发现与推理字样（如「超声见……，提示……」），规则后处理为避免误删客观事实，会保留原句并标记 `review_required` 供人工确认，而非激进切割。
- **期刊版面极端异常（如双栏内插页混排）**：尽管已基于 `papers/` 真实 PDF 修复了文内换行伪标题、单位续行伪标题、前文残留 `参考文献` 误截断与 `《标题》图版` 过滤问题，但对于跨期无标准元数据的剪报 PDF，仍可能触发 `boundary_uncertain=true` 需人工确认。
- **合成基准数据集声明**：`examples/synthetic/` 及 `scripts/run_e2e_demo.py` 生成的全部 PDF 与 Excel 仅用于程序功能与回归测试；在缺少人工标注真实 Gold Standard 之前，不得将其评测指标宣称为真实临床抽取准确率。

---

## 5. P2 范围与未实现项

- 未构建 GUI 图形桌面端界面（已优先保障 CLI、Agent Skill 及薄封装 MCP Server）。
- 未训练独立兽医专病微调模型（严格遵循任务边界）。
- `mcp_server.py` 已实现核心工具封装及基本调用测试，但未在外部独立 MCP Client（如 Claude Desktop / 外部 stdio 宿主）下做长会话集成压测（`PARTIAL`）。
