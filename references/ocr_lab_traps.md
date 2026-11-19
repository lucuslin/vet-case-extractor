# 检验表 OCR 陷阱与方正码位核查清单（OCR & Lab Table Traps）

> 对应模块：`src/vet_case_extractor/ocr/tables.py`、`src/vet_case_extractor/pdf/quality.py`、`src/vet_case_extractor/utils/text.py`、`src/vet_case_extractor/validation/semantic.py`。

## 严禁自动修改原始检验数值

**硬性原则**：严禁依据医学常识自动修正原始检验结果、单位或参考区间！
系统对检验表每行保存结构化原始字段：
`item_raw | value_raw | unit_raw | ref_range_raw | flag_raw | page_index | bbox | anomalies`

所有检测到的可疑陷阱均写入 `LabTableRow.anomalies`，自动触发病例 `ocr_suspect` / `review_required`，并附带页码与 `pages_img/` 页图路径，由人工对照页图核验后再确认。

---

## 高频检验表 OCR 陷阱

| 陷阱类别 | 典型表现 | 系统检测方式与处置 |
|---|---|---|
| **量级指数混淆** | `×10⁹/L` 识别成 `×10¹²/L`（或反之） | 比对数值单位指数与参考区间指数冲突，记录 `magnitude_mismatch` 并标记复核。 |
| **指数/箭头字符损坏** | `×10%/L`（上标 `9` 误识为 `%`）、`33.3个`（`↑` 误识为 `个`） | 实测于真实中国兽医杂志内嵌 OCR 层；记录 `exponent_or_arrow_corruption`，标记复核。 |
| **小数点丢失 / 数量级偏离** | `CREA 155`（参考区间 `0.8~1.8`） | 数值较参考区间上下界偏离超阈值，记录 `decimal_or_scale_outlier`，标记复核。 |
| **前导「1」伪影** | `HGB 111.5`（参考 `8.0~15.0`）、`PCV 134%`（参考 `24~45%`） | 检出首字符为 `1` 且去掉首 `1` 后落入合理量级，记录 `leading_one_artifact`，对照页图确认。 |
| **表格行列错位** | 项目名与数值整体上移或下移一行 | 检查列对齐与数值/参考区间匹配度，记录 `row_column_misalignment`，禁止按文本顺序硬配，须回页图逐格对齐。 |
| **单位混用或损坏** | `mg/dL` 与 `mg/mL` 混排、`mmol/L` 误识为 `nmol/L` | 结合项目与参考区间检出 `unit_mismatch_suspect`，照录原文并在备注标明。 |
| **形近检验缩写** | `GGT→GCT`、`TG→TGA`、`Mg→Ma`、`TP→TB` | 对照 `config/extraction_rules.yaml` 中 `ocr_known_typo_candidates` 发出告警，保留原文供核对。 |
| **原书印刷异常** | 原页图本身数值与参考区间矛盾 | 核对页图确系原书印刷如此时，**照录不改**，在 `备注` 注明「原书/页图如此」。 |

---

## 中文期刊 PDF 编码陷阱（实证于 `中国兽医杂志`）

1. **方正/CNKI 标点码位偏移**：
   - `U+A3AC (ꎬ)` → `，`，`U+A3AE (ꎮ)` → `。`，`U+A3BB (ꎻ)` → `；`，`U+1001B0` → `.`。
   - 汉字正文正常可读，不应误判为真乱码而盲目全页 OCR；`utils/text.py` 提供确定性清洗同时保留 `raw_text`。
2. **混合型 PDF（部分页纯图、部分页文字层）**：
   - 逐页独立评估质量，仅对无文字层或字形真损坏页执行 RapidOCR。
3. **带内嵌低质量 OCR 层的扫描期刊 PDF**：
   - `PageQualityMetrics.full_page_image=true` 会被记录；若需强制用 RapidOCR 重识图像页，使用 `--ocr-policy force_image_pages`。
