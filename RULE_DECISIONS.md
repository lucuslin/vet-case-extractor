# RULE_DECISIONS.md — 原版 Skill 规则冲突审计与决策记录

本文档记录在将 `legacy/SKILL.md` 升级为 `vet-case-extractor` 工程工具链过程中发现的规则张力/冲突、采用的工程决策、理由及对既有输出的影响。

---

## RD-001：逐字忠实（Verbatim）与诊断/给药频次标准化（Dx 五则 & Rx 缩略语）的冲突

- **冲突描述**：
  - `legacy/SKILL.md` 一方面要求「所有内容完全取自原文，缺失留空，绝不编造、不改写、不推理、不翻译」；
  - 另一方面又要求对 `medical_treatment` 中的给药途径/频次统一为 `po/iv/im + bid/tid/sid`（如「每日口服2次」→ `po, bid`），并要求按「Dx 书写五则」重排物种前缀、去除非诊断症状或将括号说明剥离至 `备注`。
- **解决方案（Raw / Normalized 双层架构）**：
  - 内部统一模型 `CaseRecord` 同时保存 Raw 层（`medical_history_raw`, `medical_test_raw`, `medical_treatment_raw`, `diagnosis_raw`, `follow_up_raw`, `notes_raw`）与 Normalized 层（`medical_history`, `medical_test`, `medical_treatment`, `diagnosis_normalized`, `follow_up`, `notes`）。
  - 所有规则化改写均写入 `normalization_changes` 审计日志（记录 `field_name`, `rule_id`, `before`, `after`, `reason`）。
  - Excel 导出默认交付 `normalized` 层以兼容原版 Skill 验收口径，同时提供 `--layer raw` 参数导出未经标准化的逐字原文层。
- **理由**：在不破坏原版 9 列 / 7 列交付口径的前提下，保证数据可审计、可回滚、不丢失原文证据。
- **对既有输出的影响**：默认导出的 Excel 与原版规则一致；内部 `cases.jsonl` 增加 Raw 层与变更记录。

---

## RD-002：规则剥离「推理/解读句」与「不可破坏含临床事实混合句」的冲突

- **冲突描述**：
  - `legacy/SKILL.md`「字段校准规则 1」要求凡带「提示 / 符合 / 疑似 / 不排除 / 初步诊断 …」的句子剥离到 `备注`；同时又提醒「剥后务必清掉语病残片」。
  - 实测中文兽医文献中常出现**事实与推理混写于同一长句**（如「X线检查见左肺野致密阴影，提示肺水肿或肿瘤转移」）。若整句直接删去，会丢失「左肺野致密阴影」这一客观检查事实；若按关键词整段切碎，容易留下残片或漏删。
- **解决方案**：
  - **LLM/Agent 抽取阶段**在提示词中要求优先拆分事实与推理子句；
  - **规则后处理（`normalization/fields.py`）**区分「纯结论/纯定位句」与「混合事实+推理句」：仅当整句以推理标志词开头且不含明确客观检查描述时才自动剥离到 `备注`（前缀 `【原文推理/解读剥离】`）并清理残片；若一句内既含检查数值/影像表现又含「提示/疑似」，保留原文并在 `review_reasons` 与 QA 报告中标记 `review_required` 供人工核对。
  - **Tier C 保留规则**：包含「外院 / 他院 / 主人 / 畜主 / 既往 / 曾诊断 / 转诊」等标志的怀疑/诊断语句，保留在 `medical_history` 原位不剥离。
- **理由**：宁可人工复核，也不盲目删除客观检查事实。
- **对既有输出的影响**：高风险混合句不会被静默截断，而是显式进入待复核队列。

---

## RD-003：CNKI / 方正排版 PDF 非标准码位（`0xA3xx` / `U+1001B0`）与「乱码必须触发 OCR」的冲突

- **冲突描述**：
  - 审计 `papers/` 真实期刊 PDF（如《猫弥散性虹膜黑色素瘤的病例报告.pdf》《角膜结膜移植术治疗猫角膜穿孔的病例报告.pdf`）发现，部分中国知网/方正排版 PDF 的标点符号被映射到了彝文音节区（`0xA3AC` 对应逗号、`0xA3AE` 对应句号、`0xA3BB` 对应分号）和私用区 `U+1001B0`（间隔点），而汉字正文完全正确可读！
  - 若简单将所有 `>0xA000` 字符计为「乱码」并触发全篇 OCR，不仅浪费算力，还会引入不必要的 OCR 错别字；但若不区分可修复标点与真正损坏的字形（`U+FFFD`、私用区替换汉字），又会漏掉《犬心脏黏液肉瘤》式的真乱码 PDF。
- **解决方案**：
  - `pdf/quality.py` 将已知可确定映射的方正标点码位（`founder_pua_count`）与不可读替换符/非标字形损坏（`garbled_ratio`）分开统计：
    - 仅含可逆转标点偏移、且汉字占比正常的页面通过确定性映射表修复为标准中文标点，保留 PyMuPDF 原生文本精度；
    - 真正字形损坏、替换符过多或文本过短且含全页扫描图的页面才触发 RapidOCR。
- **理由**：真实中文兽医文献实证驱动，兼顾零错字与高召回。
- **对既有输出的影响**：提高方正排版期刊 PDF 的原文还原准确度，大幅减少误触发 OCR。

---

## RD-004：OCR 检验表异常值「照录不改」与「错字更正」的界线

- **冲突描述**：
  - `legacy/SKILL.md`「数值表 OCR 陷阱清单」一方面要求术语错字（如「户检→尸检」「地西洋→地西泮」「波尼松→泼尼松」）可更正并在备注注明；另一方面在参考区间异常、数值明显与常识矛盾时要求「照录不改，备注注明」，且严禁依据医学常识自动修正原始检验结果。
- **解决方案**：
  - **数字、参考区间、单位、检验项目原始行（`LabTableRow`）一律禁止自动修改**：所有 `×10⁹`/`×10¹²` 量级冲突、前导 `1` 伪影、`×10%/L` 异常、参考区间错配、形近检验缩写均保留 `*_raw` 原值，写入 `LabTableRow.anomalies` 并自动将病例标记为 `ocr_suspect` / `review_required`，附上页码与页图路径供人工核对页图确认。
  - 已知非数值术语拼写候选仅在 QA 提示建议，不静默覆盖原始 OCR 文本。
- **理由**：临床测试集数值一旦被擅自改写即丧失评测可信度；必须通过页图人工确认。
- **对既有输出的影响**：所有存疑检验行均有结构化告警，无静默数值篡改。

---

## RD-005：OCR 并发配置与 `rapidocr-onnxruntime` 运行时兼容

- **冲突描述**：
  - `legacy/SKILL.md` 写明在 8 核机器上 `并发 3 进程` + `use_angle_cls: false` + `rec_batch_num: 16` + 限制 ONNX Runtime 线程数。
  - 实测本机环境已安装的 `rapidocr-onnxruntime==1.2.3` 在构造函数直接传入 `rec_batch_num=16` 会因 `UpdateParameters.update_rec_params` 缺少 `rec_model_path` 而抛出 `KeyError: 'model_path'`。
- **解决方案**：
  - `ocr/engine.py` 在初始化 `RapidOCR` 时先安全传入 `use_angle_cls=False`（或检查版本兼容参数），初始化后再设置 `ocr.text_recognizer.rec_batch_num = cfg.ocr.rec_batch_num`；线程数通过子进程环境变量（`OMP_NUM_THREADS`, `OPENBLAS_NUM_THREADS`, `MKL_NUM_THREADS`）控制；进程数依据 `psutil` 物理内存自动降级（<4GB 降为 1 进程，<8GB 降为 2 进程，默认上限 3）。
- **理由**：确保在不同版本的 `rapidocr-onnxruntime` 和不同内存宿主机上均稳定可运行。
- **对既有输出的影响**：消除初始化崩溃风险，保持与原 Skill 一致的推理行为与内存控制。

---

## RD-006：独立 LLM API 自动抽取 vs AGY 交互式 Agent 抽取的边界

- **冲突描述**：
  - 用户环境可能未配置 `GEMINI_API_KEY` / `OPENAI_API_KEY`，但 AGY Agent 本身可在交互会话中读取病例文本包完成抽取。若混淆两者，容易出现「用 Agent 结果伪装 CLI 独立 API 验收」的不诚实状态。
- **解决方案**：
  - **独立 API 路径**：`vetcase extract --provider gemini|openai_compat` 严格通过 `LLMProvider` 调用官方 SDK/HTTP 接口，缺少 Key 时明确报错并提示配置 `.env`。
  - **AGY 交互路径**：显式拆分为 `vetcase prepare-agent-batch`（生成 `EXTRACTION_SPEC.md` 与待抽病例包清单）和 `vetcase import-cases --method agent_interactive`（严格校验 JSON Schema、执行规则规范化与证据溯源，并显式标记 `extraction_method="agent_interactive"`）。
- **理由**：遵守诚实工程纪律，绝不伪造 API 验收结果，同时确保无 API Key 时工具链依然完整闭环可用。
- **对既有输出的影响**：`provenance` 与 `run_metadata.json` 明确区分 `gemini` / `openai_compat` / `agent_interactive`。

---

## RD-007：Excel 兼容交付格式与内部扩展元数据

- **冲突描述**：
  - 新版工具链需要 `document_id`、`case_id`、`file_sha256`、`page_range`、`evidence_spans`、`review_status` 等内部元数据，但原版用户交付 Excel 严格限定期刊 9 列、书籍 7 列。
- **解决方案**：
  - `export/excel.py` 严格保持 `病例数据` 工作表的 9 列 / 7 列原始列顺序与列名（且不输出模型内部调试字段），将完整内部元数据、证据坐标、QA 报告与待复核队列输出到同批次交付目录下的 `cases.jsonl`、`provenance.json`、`qa_report.json`、`qa_report.md`、`review_queue.json`、`run_metadata.json`。
- **理由**：向下 100% 兼容现有 `vet_agent` 评测管线读取习惯，同时向上提供可溯源数据集底座。
- **对既有输出的影响**：Excel 文件结构与原版完全兼容。
