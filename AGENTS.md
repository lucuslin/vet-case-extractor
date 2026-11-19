# AGENTS.md — Workspace Agent Navigation Guide

This repository provides **`vet-case-extractor`**, a Python CLI, library, Agent Skill, and MCP server for extracting structured, provenance-grounded veterinary clinical case data from Chinese veterinary journal PDFs and scanned clinical textbooks.

## Quick Navigation for Coding Agents

1. **Task Execution Skill & Workflow**: When asked to extract veterinary cases from PDFs, run QA, or export 9-column (journal) / 7-column (book) Excel tables, read and follow [SKILL.md](SKILL.md).
2. **Domain Extraction Rules** (load on demand from [references/](references/)):
   - Field boundaries, PE vs. specialty tests, Tier C history, necropsy routing: [references/field_rules.md](references/field_rules.md)
   - Diagnosis 5-rule normalization (`Dx 书写五则`): [references/dx_rules.md](references/dx_rules.md)
   - Scanned textbook 7-column SOP & page offset: [references/book_scan_sop.md](references/book_scan_sop.md)
   - Laboratory table OCR traps (never auto-mutate numbers): [references/ocr_lab_traps.md](references/ocr_lab_traps.md)
   - Structural/semantic QA & page-image review triggers: [references/qa_and_review.md](references/qa_and_review.md)
3. **Cross-Agent & MCP Setup**: See [docs/AGENT_INTEGRATION.md](docs/AGENT_INTEGRATION.md).

## Verified CLI Entry Points

Run commands inside the project virtual environment (`vetcase` when activated, `.venv/bin/vetcase` on Linux/macOS/WSL, or `.venv\Scripts\vetcase.exe` on Windows):

- Environment diagnostics: `vetcase doctor`
- Local preprocessing + RapidOCR + indexing (no external API key required):
  `vetcase run --input <pdf_or_dir> --output <project_dir> --mode <auto|journal|book> --provider none`
- Interactive Agent batch export & import:
  `vetcase prepare-agent-batch --project <project_dir> --batch-size 8`
  `vetcase import-cases --project <project_dir> --input <rows.json> --method agent_interactive --agent-platform <platform> --model-name <model>`
- Standalone API extraction (requires `GEMINI_API_KEY` or `OPENAI_API_KEY` in `.env`):
  `vetcase extract --project <project_dir> --provider <gemini|openai_compat>`
- Quality assurance & Excel bundle export:
  `vetcase qa --project <project_dir>`
  `vetcase export --project <project_dir> --format <journal|book>`
