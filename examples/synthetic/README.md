# Synthetic Sample Data (`examples/synthetic/`)

> ⚠️ **SYNTHETIC TEST DATA ONLY（合成测试数据声明）**
> 本目录下的所有 PDF（`pdfs/`）与抽取导出样例（`journal_export/`、`book_export/`）均由 `scripts/generate_synthetic_fixtures.py` 与 `scripts/run_e2e_demo.py` 自动生成，**仅用于验证工具链功能与输出格式**，严禁当作真实临床兽医病例或真实抽取准确率依据。

## 目录内容

- `pdfs/`：7 类合成测试 PDF（原生文字单病例、纯扫描双栏含检验表陷阱、混合文字/扫描、一文多病例、合订本、双栏扫描书籍含页码偏移与破折号变体、故意损坏的非法 PDF）。
- `journal_export/`：合成期刊病例 9 列 Excel（`病例数据` + `说明`）及配套 `cases.jsonl`、`provenance.json`、`qa_report.json`、`qa_report.md`、`review_queue.json`、`run_metadata.json`。
- `book_export/`：合成扫描书籍 7 列 Excel（`病例数据` + `说明`）及配套结构化交付束。
