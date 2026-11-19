# 扫描图版书籍 / 译著处理 SOP（Book Scan SOP）

> 适用于《兽医临床病例分析》（Sharkey & Radin）等无文字层、双栏排版、上百病例的扫描型兽医教材与译著。

## 1. 交付 Schema（7 列）
`title | medical_history | medical_test | medical_treatment | Dx | follow-up | 备注`
- 书籍无刊名与发表年概念，删除 `source` 与 `year`。

## 2. 页码偏移标定（Page Offset）
- 公式：`book_page_number = pdf_page_number - page_offset`（例如 `p012` 为书页第 1 页时，`--page-offset 11`）。
- 运行 `vetcase ingest --mode book --page-offset <N>` 前，先核对目录页与正文第 1 页对应的 PDF 页码。
- `PageArtifact` 独立保留 0-based `page_index`、1-based `pdf_page_number` 及 `book_page_number`，不覆盖原始 PDF 页索引。

## 3. OCR 与并发配置（实战标定值）
- **渲染质量**：书籍默认 `250 dpi`（可通过 `--dpi` 覆盖）。
- **引擎参数**：RapidOCR 关闭方向分类（`use_angle_cls=false`），`rec_batch_num=16`，限制 ONNX Runtime 线程数（`OMP_NUM_THREADS=2`）。
- **进程并发**：默认上限 **3 进程**（8 核实测 >3 进程受内存带宽制约收益为负），并基于 `psutil` 可用物理内存自动降级（<4 GB → 1 进程，<8 GB → 2 进程）。
- **断点续跑**：每页结果独立缓存至 `pages_ocr/<doc_id>/pNNN.json`，中断后重跑 `vetcase ocr` 自动跳过已完成页。

## 4. 双栏版面恢复
- `layout/columns.py` 先分离页眉/页脚与跨栏通栏元素（宽标题、通栏表格），再在每个纵向带内按 X 区间沟槽切分左栏/右栏，按栏内 Y 坐标升序拼接，并输出 `[[通栏]]` / `[[左栏]]` / `[[右栏]]` 标记。

## 5. 章节、病例边界与连续性校验
- **章节识别**：匹配 `第X章 ...`。
- **病例标题**：仅整行匹配 `^病例\s*(\d+)\s*[一\-—－~、·.]*\s*等级\s*(\d+|[一二三四五])$`（兼容 OCR 把破折号误识为 `一`），屏蔽正文中的交叉引用（如「见第二章病例9」）。
- **等级异常检测**：OCR 常将「等级3」误识为「等级13」等超界值；系统自动设置 `boundary_uncertain=true` 并在 QA 报告中要求核对页图 `pages_img/`。
- **连续性自检**：`vetcase index` 与 `vetcase qa` 按章核对 `1..max` 是否缺号或重号。
- **title 格式**：`<章节名> · 病例<N> 等级<X>`。

## 6. 各字段取数位置与防编造纪律
- `medical_history`：病例标题之后、首个检验表标题（`血常规检查` / `生化检查` / `尿液分析` / `腹腔液分析` 等）或 `解析` 之前的叙述段。
- `medical_test`：检验表原始 OCR 数值行 + `解析` 中明确陈述的客观异常结果（纯判读推理剥离至 `备注`）。
- `medical_treatment`：叙述段与 `病例总结` 中实际执行的治疗。**教学书籍常见无治疗病例，原文未给必须留空，绝不把 `解析` 中的假设性建议写成已执行治疗。**
- `Dx` / `follow-up`：从 `病例总结和结果` / `病例总结及结果` / `病例总结和结论` / `病例总结` 小节提取；无转归则 `follow-up` 留空。

## 7. 分批与增量落盘
- 交互式 Agent 抽取使用 `vetcase prepare-agent-batch --batch-size 8`（每批 6~10 例防止长输出截断）。
- `vetcase import-cases` 与 `vetcase extract` 均为**每病例独立原子文件落盘**（`cases/<case_id>/extracted.json`），避免整批覆盖写入导致数据丢失。
