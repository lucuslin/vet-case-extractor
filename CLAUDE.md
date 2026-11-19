# CLAUDE.md — Claude Code Project Guide

This repository contains **`vet-case-extractor`**, a cross-platform Python toolchain, Agent Skill, and MCP server for extracting structured veterinary clinical cases from Chinese journal PDFs and scanned textbooks into 9-column (journal) or 7-column (book) Excel workbooks with bounding-box provenance and automated QA.

## How to Operate in Claude Code

- **Extracting Cases / Running the Skill Workflow**: Read [SKILL.md](SKILL.md) first, then load the required domain rule files from [references/](references/) (`field_rules.md`, `dx_rules.md`, `book_scan_sop.md`, `ocr_lab_traps.md`, `qa_and_review.md`).
- **Interactive Agent Extraction (No External Gemini/OpenAI Key Required)**:
  1. Run `vetcase run --input <pdf_path> --output <project_dir> --mode <journal|book|auto> --provider none`
  2. Run `vetcase prepare-agent-batch --project <project_dir> --batch-size 8`
  3. Read `<project_dir>/agent_batches/EXTRACTION_SPEC.md` and each case's `cases/<case_id>/packet.txt`, write the validated JSON array, and import with:
     `vetcase import-cases --project <project_dir> --input <rows.json> --method agent_interactive --agent-platform claude-code --model-name <claude-model-id>`
  4. Run `vetcase qa --project <project_dir>` and `vetcase export --project <project_dir> --format <journal|book>`
- **Development & Testing**:
  - Lint: `ruff check src tests scripts`
  - Tests: `pytest -v`
  - Full integration & MCP instructions: [docs/AGENT_INTEGRATION.md](docs/AGENT_INTEGRATION.md)
