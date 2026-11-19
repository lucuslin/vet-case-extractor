"""Full export bundle writer (Excel + JSONL + provenance + QA + run metadata)."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any, Literal

from vet_case_extractor import __version__
from vet_case_extractor.config import AppConfig, load_config
from vet_case_extractor.export.excel import (
    export_to_excel,
    resolve_export_format,
    sort_records_for_export,
)
from vet_case_extractor.models import (
    SCHEMA_VERSION,
    EvidenceSpan,
    SourceType,
    utc_now_iso,
)
from vet_case_extractor.utils.io import atomic_write_json, atomic_write_text
from vet_case_extractor.validation.qa import (
    build_qa_report,
    build_review_queue,
    render_qa_markdown,
)
from vet_case_extractor.validation.structural import check_project_json_integrity


def export_project_bundle(
    project_dir: str | Path,
    output_dir: str | Path | None = None,
    *,
    excel_filename: str | None = None,
    format_mode: Literal["auto", "journal", "book"] = "auto",
    layer: Literal["normalized", "raw"] = "normalized",
    cfg: AppConfig | None = None,
) -> dict[str, Path]:
    """Export Excel workbook plus JSONL, provenance, QA reports, review queue, and run metadata."""
    resolved_project = Path(project_dir)
    resolved_output = Path(output_dir) if output_dir is not None else (resolved_project / "export")
    resolved_output.mkdir(parents=True, exist_ok=True)
    resolved_cfg = cfg or load_config()

    json_errors, manifest, index, records = check_project_json_integrity(resolved_project)

    source_filenames: dict[str, str] = {}
    default_source_type = manifest.source_type if manifest is not None else SourceType.AUTO
    if manifest is not None:
        for doc_id, doc in manifest.documents.items():
            source_filenames[doc_id] = doc.filename
    if index is not None:
        for seg in index.cases:
            source_filenames.setdefault(seg.document_id, seg.source_filename)

    effective_format = resolve_export_format(
        records=records,
        format_mode=format_mode,
        default_source_type=default_source_type,
    )
    sorted_records = sort_records_for_export(
        records=records,
        format_mode=effective_format,
        source_filenames=source_filenames,
    )

    qa_report = build_qa_report(
        project_dir=resolved_project,
        records=sorted_records,
        manifest=manifest,
        index=index,
        cfg=resolved_cfg,
        json_errors=json_errors,
    )
    review_queue = build_review_queue(report=qa_report, records=sorted_records, index=index)
    qa_markdown = render_qa_markdown(report=qa_report, records=sorted_records)

    # Also keep project_dir/qa synchronized
    qa_dir = resolved_project / "qa"
    qa_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_json(qa_dir / "qa_report.json", qa_report)
    atomic_write_text(qa_dir / "qa_report.md", qa_markdown)
    atomic_write_json(qa_dir / "review_queue.json", review_queue)

    default_xlsx_name = (
        "全书病例结构化抽取.xlsx" if effective_format == "book" else "病例抽取_总表.xlsx"
    )
    xlsx_path = resolved_output / (excel_filename or default_xlsx_name)
    export_to_excel(
        records=sorted_records,
        output_path=xlsx_path,
        format_mode=effective_format,
        layer=layer,
        source_filenames=source_filenames,
        default_source_type=default_source_type,
        cfg=resolved_cfg,
        qa_report=qa_report,
    )

    # cases.jsonl
    cases_jsonl_path = resolved_output / "cases.jsonl"
    jsonl_lines = [
        json.dumps(rec.model_dump(mode="json"), ensure_ascii=False) for rec in sorted_records
    ]
    atomic_write_text(
        cases_jsonl_path,
        ("\n".join(jsonl_lines) + "\n") if jsonl_lines else "",
    )

    # provenance.json (flat array of EvidenceSpan matching schemas/provenance.schema.json)
    provenance_spans: list[dict[str, Any]] = []
    for rec in sorted_records:
        for span in rec.evidence_spans:
            if isinstance(span, EvidenceSpan):
                provenance_spans.append(span.model_dump(mode="json"))
    provenance_path = resolved_output / "provenance.json"
    atomic_write_json(provenance_path, provenance_spans)

    # QA artifacts inside bundle output_dir
    qa_json_path = resolved_output / "qa_report.json"
    qa_md_path = resolved_output / "qa_report.md"
    review_queue_path = resolved_output / "review_queue.json"
    atomic_write_json(qa_json_path, qa_report)
    atomic_write_text(qa_md_path, qa_markdown)
    atomic_write_json(review_queue_path, review_queue)

    # run_metadata.json
    methods = sorted({r.extraction_method for r in sorted_records if r.extraction_method})
    models_used = sorted({r.model_name for r in sorted_records if r.model_name})
    token_totals: Counter[str] = Counter()
    for rec in sorted_records:
        for k, v in rec.token_usage.items():
            token_totals[k] += int(v)

    run_metadata_path = resolved_output / "run_metadata.json"
    run_metadata = {
        "schema_version": SCHEMA_VERSION,
        "tool_version": __version__,
        "rule_version": resolved_cfg.rule_version,
        "generated_at": utc_now_iso(),
        "project_dir": str(resolved_project),
        "output_dir": str(resolved_output),
        "format": effective_format,
        "layer": layer,
        "total_documents": qa_report.total_documents,
        "total_cases": qa_report.total_cases,
        "exported_records": len(sorted_records),
        "extraction_methods": methods,
        "models_used": models_used,
        "token_usage_totals": dict(token_totals),
        "qa_overall_status": qa_report.overall_status,
        "files": {
            "excel": xlsx_path.name,
            "cases_jsonl": cases_jsonl_path.name,
            "provenance_json": provenance_path.name,
            "qa_report_json": qa_json_path.name,
            "qa_report_md": qa_md_path.name,
            "review_queue_json": review_queue_path.name,
        },
    }
    atomic_write_json(run_metadata_path, run_metadata)

    return {
        "excel": xlsx_path,
        "cases_jsonl": cases_jsonl_path,
        "provenance_json": provenance_path,
        "qa_report_json": qa_json_path,
        "qa_report_md": qa_md_path,
        "review_queue_json": review_queue_path,
        "run_metadata_json": run_metadata_path,
    }
