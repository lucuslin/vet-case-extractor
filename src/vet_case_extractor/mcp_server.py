"""Optional MCP server wrapper exposing core vet-case-extractor operations."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from vet_case_extractor.config import load_config
from vet_case_extractor.export.bundle import export_project_bundle
from vet_case_extractor.models import SourceType
from vet_case_extractor.pipeline import (
    extract_project,
    get_project_status,
    index_project,
    ingest_project,
    ocr_project,
)
from vet_case_extractor.validation.qa import run_project_qa


def _create_mcp_instance(name: str = "vet-case-extractor") -> Any:
    """Instantiate MCPServer (mcp>=2.0) or FastMCP (mcp 1.x)."""
    try:
        from mcp.server.mcpserver import MCPServer

        return MCPServer(name)
    except ImportError:  # pragma: no cover - fallback for mcp 1.x
        from mcp.server.fastmcp import FastMCP

        return FastMCP(name)


def ingest_pdf(
    project_dir: str,
    input_path: str,
    mode: str = "auto",
    page_offset: int = 0,
    target_article_query: str | None = None,
    run_ocr: bool = True,
    build_index: bool = True,
) -> dict[str, Any]:
    """Ingest PDF file(s) into a project workspace, optionally running RapidOCR and case indexing."""
    cfg = load_config()
    src_type = SourceType(mode.lower())
    ingest_res = ingest_project(
        project_dir=project_dir,
        input_path=input_path,
        mode=src_type,
        page_offset=page_offset,
        target_article_query=target_article_query,
        cfg=cfg,
    )
    result: dict[str, Any] = {"ingest": ingest_res}
    if run_ocr:
        result["ocr"] = ocr_project(project_dir=project_dir, cfg=cfg)
    if build_index:
        idx = index_project(project_dir=project_dir, mode=src_type, cfg=cfg)
        result["index"] = {
            "total_documents": idx.total_documents,
            "total_cases": idx.total_cases,
            "chapter_continuity_issues": idx.chapter_continuity_issues,
        }
    return result


def extract_cases(
    project_dir: str,
    provider: str = "gemini",
    model: str | None = None,
    retry_failed: bool = False,
) -> dict[str, Any]:
    """Run LLM structured case extraction on pending/failed cases in a project workspace."""
    cfg = load_config()
    return extract_project(
        project_dir=project_dir,
        provider_name=provider,
        model=model,
        retry_failed=retry_failed,
        cfg=cfg,
    )


def validate_cases(project_dir: str) -> dict[str, Any]:
    """Run structural, semantic, OCR, and provenance QA checks and persist QA artifacts."""
    cfg = load_config()
    report = run_project_qa(Path(project_dir), cfg=cfg)
    return report.model_dump(mode="json")


def export_cases(
    project_dir: str,
    output_dir: str | None = None,
    format_mode: Literal["auto", "journal", "book"] = "auto",
    layer: Literal["normalized", "raw"] = "normalized",
    excel_filename: str | None = None,
) -> dict[str, str]:
    """Export Excel workbook, JSONL, provenance, QA reports, and run metadata bundle."""
    cfg = load_config()
    outputs = export_project_bundle(
        project_dir=project_dir,
        output_dir=output_dir,
        excel_filename=excel_filename,
        format_mode=format_mode,
        layer=layer,
        cfg=cfg,
    )
    return {k: str(v) for k, v in outputs.items()}


def get_status(project_dir: str) -> dict[str, Any]:
    """Return current processing status for a project workspace."""
    return get_project_status(project_dir)


def create_mcp_server(name: str = "vet-case-extractor") -> Any:
    """Create and configure the MCP server with all 5 vet-case-extractor tools."""
    server = _create_mcp_instance(name=name)
    server.tool()(ingest_pdf)
    server.tool()(extract_cases)
    server.tool()(validate_cases)
    server.tool()(export_cases)
    server.tool()(get_status)
    return server


def main() -> None:
    """Run the vet-case-extractor MCP server over stdio."""
    server = create_mcp_server()
    server.run()


if __name__ == "__main__":
    main()
