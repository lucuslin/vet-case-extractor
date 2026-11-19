"""Click CLI (`vetcase`) with strict validation and deterministic exit codes."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import click

from vet_case_extractor import __version__
from vet_case_extractor.config import AppConfig, load_config
from vet_case_extractor.export.bundle import export_project_bundle
from vet_case_extractor.models import CaseRecord, OCRPolicy, SourceType
from vet_case_extractor.pipeline import (
    ConfigUsageError,
    QAFailureError,
    extract_project,
    get_project_status,
    import_agent_cases,
    index_project,
    ingest_project,
    ocr_project,
    prepare_agent_batch_for_project,
    run_doctor,
    run_full_pipeline,
)
from vet_case_extractor.providers import ProviderConfigError, ProviderError
from vet_case_extractor.utils.io import atomic_write_json
from vet_case_extractor.utils.logging import setup_logging
from vet_case_extractor.validation.evaluation import evaluate_against_gold
from vet_case_extractor.validation.qa import run_project_qa

EXIT_OK = 0
EXIT_RUNTIME_ERROR = 1
EXIT_USAGE_CONFIG_ERROR = 2
EXIT_QA_FAILURE = 3


def _load_records_for_eval(path: Path) -> list[CaseRecord] | list[dict[str, Any]]:
    if not path.exists():
        raise ConfigUsageError(f"评测文件不存在: {path}")
    suffix = path.suffix.lower()
    if suffix == ".jsonl":
        items: list[dict[str, Any]] = []
        for idx, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                obj = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise ConfigUsageError(f"JSONL 第 {idx} 行解析失败 ({path}): {exc}") from exc
            if not isinstance(obj, dict):
                raise ConfigUsageError(f"JSONL 第 {idx} 行不是 JSON 对象 ({path})")
            items.append(obj)
        return items
    if suffix == ".json":
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ConfigUsageError(f"JSON 解析失败 ({path}): {exc}") from exc
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            for key in ("cases", "rows", "items", "records"):
                if isinstance(data.get(key), list):
                    return data[key]
        raise ConfigUsageError(f"JSON 评测文件必须是数组或包含 cases/rows/items 的对象: {path}")
    if suffix == ".xlsx":
        from openpyxl import load_workbook

        wb = load_workbook(path, read_only=True, data_only=True)
        sheet_name = "病例数据" if "病例数据" in wb.sheetnames else wb.sheetnames[0]
        ws = wb[sheet_name]
        rows_iter = ws.iter_rows(values_only=True)
        headers_raw = next(rows_iter, None)
        if not headers_raw:
            wb.close()
            return []
        headers = [str(h or "").strip() for h in headers_raw]
        records: list[dict[str, Any]] = []
        for row in rows_iter:
            if not any(cell is not None and str(cell).strip() for cell in row):
                continue
            item = {
                headers[i]: ("" if i >= len(row) or row[i] is None else str(row[i]))
                for i in range(len(headers))
                if headers[i]
            }
            records.append(item)
        wb.close()
        return records
    raise ConfigUsageError(f"不支持的评测文件格式 '{path.suffix}'（仅支持 .json / .jsonl / .xlsx）")


def _emit_json(payload: Any) -> None:
    click.echo(json.dumps(payload, ensure_ascii=False, indent=2))


def _handle_cli_errors(fn: Any) -> Any:
    """Decorator mapping domain exceptions to clear exit codes (1/2/3)."""
    import functools

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except click.exceptions.Exit:
            raise
        except click.ClickException:
            raise
        except QAFailureError as exc:
            click.echo(f"QA FAILURE: {exc}", err=True)
            sys.exit(EXIT_QA_FAILURE)
        except (ConfigUsageError, ProviderConfigError) as exc:
            click.echo(f"ERROR (config/usage): {exc}", err=True)
            sys.exit(EXIT_USAGE_CONFIG_ERROR)
        except (ProviderError, OSError, RuntimeError) as exc:
            click.echo(f"ERROR (runtime): {exc}", err=True)
            sys.exit(EXIT_RUNTIME_ERROR)
        except Exception as exc:
            click.echo(f"ERROR (unexpected {type(exc).__name__}): {exc}", err=True)
            sys.exit(EXIT_RUNTIME_ERROR)

    return wrapper


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(version=__version__, prog_name="vetcase")
@click.option(
    "--config-dir",
    type=click.Path(path_type=Path, exists=True, file_okay=False),
    default=None,
    help="Optional directory containing default.yaml and extraction_rules.yaml.",
)
@click.option(
    "--log-level",
    type=click.Choice(["DEBUG", "INFO", "WARNING", "ERROR"], case_sensitive=False),
    default=None,
    help="Override logging level.",
)
@click.pass_context
def main(ctx: click.Context, config_dir: Path | None, log_level: str | None) -> None:
    """vetcase — 中文兽医临床病例报告与扫描书籍结构化抽取工具链。"""
    setup_logging(level=log_level)
    try:
        cfg = load_config(config_dir=config_dir)
    except Exception as exc:
        click.echo(f"ERROR (config): Failed to load configuration: {exc}", err=True)
        ctx.exit(EXIT_USAGE_CONFIG_ERROR)
        return
    ctx.ensure_object(dict)
    ctx.obj["cfg"] = cfg


@main.command()
@click.option("--json-output", "as_json", is_flag=True, help="Print diagnostics as JSON.")
@click.pass_context
@_handle_cli_errors
def doctor(ctx: click.Context, as_json: bool) -> None:
    """Check Python, venv, PDF/OCR/Excel/LLM dependencies, API key presence, hardware, and CJK fonts."""
    cfg: AppConfig = ctx.obj["cfg"]
    info = run_doctor(cfg)
    if as_json:
        _emit_json(info)
        return

    click.echo(f"vetcase v{info['tool_version']} | Python {info['python_version']} | venv={info['in_virtualenv']}")
    click.echo(f"Executable: {info['python_executable']}")
    click.echo(f"Config dir: {info['config_dir'] or 'built-in defaults'} (rule_version={info['rule_version']})")
    hw = info["hardware"]
    click.echo(
        f"Hardware: {hw['cpu_count']} CPUs | RAM {hw['available_ram_gb']}/{hw['total_ram_gb']} GB available | "
        f"recommended OCR workers={hw['recommended_ocr_workers']} (configured={hw['configured_ocr_workers']})"
    )
    click.echo("Packages:")
    for pkg_name, details in info["packages"].items():
        status_str = f"OK ({details['version']})" if details["available"] else f"MISSING ({details['error']})"
        click.echo(f"  - {details['label']} [{pkg_name}]: {status_str}")
    click.echo("API Keys (presence only, never printed):")
    for key_name, present in info["api_keys_present"].items():
        click.echo(f"  - {key_name}: {'SET' if present else 'NOT SET'}")
    click.echo("Chinese Fonts:")
    for font_path, exists in info["chinese_fonts"].items():
        click.echo(f"  - {font_path}: {'FOUND' if exists else 'missing'}")


@main.command()
@click.option("--project", "-p", "project_dir", default=None, type=click.Path(path_type=Path), help="Project workspace directory.")
@click.option("--output", "-o", "output_alias", default=None, type=click.Path(path_type=Path), help="Alias for --project workspace directory.")
@click.option("--input", "-i", "input_path", required=True, type=click.Path(path_type=Path), help="PDF file or directory (searched recursively).")
@click.option("--mode", type=click.Choice(["auto", "journal", "book"], case_sensitive=False), default="auto", show_default=True)
@click.option("--page-offset", type=int, default=0, show_default=True, help="PDF page offset (pdf_page_number - offset = book_page_number).")
@click.option("--target-article", default=None, help="Target article title/keyword for omnibus bound-volume PDFs.")
@click.option(
    "--ocr-policy",
    type=click.Choice(["auto", "force_image_pages", "force_all", "never"], case_sensitive=False),
    default="auto",
    show_default=True,
)
@click.option("--render-all/--no-render-all", default=None, help="Override config render_all_pages_for_review.")
@click.option("--retry-failed", is_flag=True, help="Retry previously failed documents.")
@click.pass_context
@_handle_cli_errors
def ingest(
    ctx: click.Context,
    project_dir: Path | None,
    output_alias: Path | None,
    input_path: Path,
    mode: str,
    page_offset: int,
    target_article: str | None,
    ocr_policy: str,
    render_all: bool | None,
    retry_failed: bool,
) -> None:
    """Discover, SHA-256 deduplicate, and extract native text/quality metrics from PDFs."""
    if project_dir is None and output_alias is None:
        raise click.UsageError("Must specify --project/-p or --output/-o.")
    if (
        project_dir is not None
        and output_alias is not None
        and project_dir.resolve() != output_alias.resolve()
    ):
        raise click.UsageError(
            "Conflicting --project and --output paths provided; specify only one or identical paths."
        )
    resolved_project = project_dir if project_dir is not None else output_alias
    assert resolved_project is not None

    cfg: AppConfig = ctx.obj["cfg"]
    res = ingest_project(
        project_dir=resolved_project,
        input_path=input_path,
        mode=SourceType(mode.lower()),
        page_offset=page_offset,
        target_article_query=target_article,
        ocr_policy=OCRPolicy(ocr_policy.lower()),
        render_all_pages=render_all,
        retry_failed=retry_failed,
        cfg=cfg,
    )
    _emit_json(res)


@main.command()
@click.option("--project", "-p", "project_dir", required=True, type=click.Path(path_type=Path))
@click.option("--doc-id", default=None, help="Optional single document_id to OCR.")
@click.option("--dpi", type=click.IntRange(72, 600), default=None, help="Override render DPI.")
@click.option("--workers", "-w", type=click.IntRange(1, 16), default=None, help="Requested OCR worker processes (capped safely by RAM).")
@click.option("--force", is_flag=True, help="Ignore per-page OCR cache and re-run.")
@click.option("--retry-failed", is_flag=True, help="Clear failed page caches and retry OCR.")
@click.pass_context
@_handle_cli_errors
def ocr(
    ctx: click.Context,
    project_dir: Path,
    doc_id: str | None,
    dpi: int | None,
    workers: int | None,
    force: bool,
    retry_failed: bool,
) -> None:
    """Run RapidOCR on pages flagged needs_ocr with caching and layout restoration."""
    cfg: AppConfig = ctx.obj["cfg"]
    res = ocr_project(
        project_dir=project_dir,
        doc_id=doc_id,
        dpi=dpi,
        workers=workers,
        force=force,
        retry_failed=retry_failed,
        cfg=cfg,
    )
    _emit_json(res)


@main.command()
@click.option("--project", "-p", "project_dir", required=True, type=click.Path(path_type=Path))
@click.option("--mode", type=click.Choice(["auto", "journal", "book"], case_sensitive=False), default="auto", show_default=True)
@click.option("--provider", type=click.Choice(["none", "gemini", "openai_compat"], case_sensitive=False), default="none", show_default=True, help="Optional LLM provider to consult on uncertain boundaries.")
@click.option("--model", default=None, help="Override LLM model for optional boundary check.")
@click.pass_context
@_handle_cli_errors
def index(
    ctx: click.Context,
    project_dir: Path,
    mode: str,
    provider: str,
    model: str | None,
) -> None:
    """Segment documents into cases, check book continuity, and generate case packets."""
    cfg: AppConfig = ctx.obj["cfg"]
    idx = index_project(
        project_dir=project_dir,
        mode=SourceType(mode.lower()),
        consult_provider=None if provider.lower() == "none" else provider.lower(),
        model=model,
        cfg=cfg,
    )
    _emit_json(
        {
            "total_documents": idx.total_documents,
            "total_cases": idx.total_cases,
            "chapter_continuity_issues": idx.chapter_continuity_issues,
            "duplicate_case_ids": idx.duplicate_case_ids,
        }
    )


@main.command()
@click.option("--project", "-p", "project_dir", required=True, type=click.Path(path_type=Path))
@click.option("--provider", type=click.Choice(["gemini", "openai_compat"], case_sensitive=False), default=None, help="LLM provider (defaults to config).")
@click.option("--model", default=None, help="Override LLM model name.")
@click.option("--workers", "-w", type=click.IntRange(1, 16), default=None, help="Concurrent LLM extraction workers.")
@click.option("--case-id", "case_ids", multiple=True, help="Extract only specified case_id(s).")
@click.option("--retry-failed", is_flag=True, help="Retry cases that previously failed.")
@click.option("--force", is_flag=True, help="Re-extract even already completed cases.")
@click.pass_context
@_handle_cli_errors
def extract(
    ctx: click.Context,
    project_dir: Path,
    provider: str | None,
    model: str | None,
    workers: int | None,
    case_ids: tuple[str, ...],
    retry_failed: bool,
    force: bool,
) -> None:
    """Extract structured cases via configured LLMProvider with normalization and provenance."""
    cfg: AppConfig = ctx.obj["cfg"]
    res = extract_project(
        project_dir=project_dir,
        provider_name=provider.lower() if provider else None,
        model=model,
        workers=workers,
        case_ids=list(case_ids) if case_ids else None,
        retry_failed=retry_failed,
        force=force,
        cfg=cfg,
    )
    _emit_json(res)


@main.command("prepare-agent-batch")
@click.option("--project", "-p", "project_dir", required=True, type=click.Path(path_type=Path))
@click.option("--output", "-o", "output_dir", type=click.Path(path_type=Path), default=None, help="Output directory for EXTRACTION_SPEC.md and batch manifests.")
@click.option("--batch-size", type=click.IntRange(1, 50), default=None, help="Cases per batch (recommended 6~10).")
@click.option("--include-review", is_flag=True, help="Include review_required cases in pending batches.")
@click.pass_context
@_handle_cli_errors
def prepare_agent_batch_cmd(
    ctx: click.Context,
    project_dir: Path,
    output_dir: Path | None,
    batch_size: int | None,
    include_review: bool,
) -> None:
    """Export EXTRACTION_SPEC.md and batch manifests for interactive AGY Agent extraction."""
    cfg: AppConfig = ctx.obj["cfg"]
    master_path = prepare_agent_batch_for_project(
        project_dir=project_dir,
        output_dir=output_dir,
        batch_size=batch_size,
        include_review=include_review,
        cfg=cfg,
    )
    _emit_json({"agent_batches_manifest": str(master_path)})


@main.command("import-cases")
@click.option("--project", "-p", "project_dir", required=True, type=click.Path(path_type=Path))
@click.option("--input", "-i", "json_path", required=True, type=click.Path(path_type=Path), help="Path to JSON file containing extracted case objects.")
@click.option("--method", default="agent_interactive", show_default=True, help="Provenance extraction_method label.")
@click.option("--model-name", default="agy-agent", show_default=True, help="Provenance model_name label.")
@click.pass_context
@_handle_cli_errors
def import_cases_cmd(
    ctx: click.Context,
    project_dir: Path,
    json_path: Path,
    method: str,
    model_name: str,
) -> None:
    """Validate, normalize, provenance-ground, and import agent-extracted JSON cases."""
    cfg: AppConfig = ctx.obj["cfg"]
    res = import_agent_cases(
        project_dir=project_dir,
        json_path=json_path,
        method=method,
        model_name=model_name,
        cfg=cfg,
    )
    _emit_json(res)


@main.command()
@click.option("--project", "-p", "project_dir", required=True, type=click.Path(path_type=Path))
@click.option("--strict", is_flag=True, help="Treat REVIEW_REQUIRED as QA failure (exit code 3) in addition to FAIL.")
@click.pass_context
@_handle_cli_errors
def qa(ctx: click.Context, project_dir: Path, strict: bool) -> None:
    """Run structural, semantic, OCR, and provenance QA checks on a project."""
    cfg: AppConfig = ctx.obj["cfg"]
    resolved = Path(project_dir)
    if not resolved.exists():
        raise ConfigUsageError(f"项目目录不存在: {resolved}")

    report = run_project_qa(resolved, cfg=cfg)
    _emit_json(
        {
            "overall_status": report.overall_status,
            "total_documents": report.total_documents,
            "total_cases": report.total_cases,
            "completed_cases": report.completed_cases,
            "review_required_cases": report.review_required_cases,
            "failed_cases": report.failed_cases,
            "pending_cases": report.pending_cases,
            "issue_counts_by_severity": report.issue_counts_by_severity,
            "issue_counts_by_rule": report.issue_counts_by_rule,
        }
    )
    if report.overall_status == "FAIL":
        raise QAFailureError(
            f"QA overall_status=FAIL (errors={report.issue_counts_by_severity.get('error', 0)}, failed_cases={report.failed_cases})"
        )
    if strict and report.overall_status == "REVIEW_REQUIRED":
        raise QAFailureError(
            f"QA strict mode failed with overall_status=REVIEW_REQUIRED (review_required_cases={report.review_required_cases})"
        )


@main.command()
@click.option("--project", "-p", "project_dir", required=True, type=click.Path(path_type=Path))
@click.option("--output", "-o", "output_dir", type=click.Path(path_type=Path), default=None, help="Output bundle directory (default: <project>/export).")
@click.option("--filename", "excel_filename", default=None, help="Custom Excel filename (supports Chinese names).")
@click.option("--format", "export_format", type=click.Choice(["auto", "journal", "book"], case_sensitive=False), default="auto", show_default=True)
@click.option("--layer", type=click.Choice(["normalized", "raw"], case_sensitive=False), default="normalized", show_default=True)
@click.pass_context
@_handle_cli_errors
def export(
    ctx: click.Context,
    project_dir: Path,
    output_dir: Path | None,
    excel_filename: str | None,
    export_format: str,
    layer: str,
) -> None:
    """Export Excel workbook (9-col journal or 7-col book), JSONL, provenance, QA, and metadata."""
    cfg: AppConfig = ctx.obj["cfg"]
    resolved = Path(project_dir)
    if not (resolved / "manifest.json").exists():
        raise ConfigUsageError(f"项目目录未初始化或缺少 manifest.json: {resolved}")

    outputs = export_project_bundle(
        project_dir=resolved,
        output_dir=output_dir,
        excel_filename=excel_filename,
        format_mode=export_format.lower(),  # type: ignore[arg-type]
        layer=layer.lower(),  # type: ignore[arg-type]
        cfg=cfg,
    )
    _emit_json({k: str(v) for k, v in outputs.items()})


@main.command()
@click.option("--project", "-p", "project_dir", required=True, type=click.Path(path_type=Path))
@click.pass_context
@_handle_cli_errors
def status(ctx: click.Context, project_dir: Path) -> None:
    """Display project progress across ingestion, OCR, indexing, extraction, QA, and exports."""
    res = get_project_status(project_dir)
    _emit_json(res)


@main.command()
@click.option("--project", "-p", "project_dir", default=None, type=click.Path(path_type=Path), help="Project workspace directory (defaults to --output if omitted).")
@click.option("--input", "-i", "input_path", required=True, type=click.Path(path_type=Path))
@click.option("--mode", type=click.Choice(["auto", "journal", "book"], case_sensitive=False), default="auto", show_default=True)
@click.option("--provider", type=click.Choice(["gemini", "openai_compat", "none"], case_sensitive=False), default="none", show_default=True)
@click.option("--model", default=None, help="Override LLM model name.")
@click.option("--format", "export_format", type=click.Choice(["auto", "journal", "book"], case_sensitive=False), default="auto", show_default=True)
@click.option("--layer", type=click.Choice(["normalized", "raw"], case_sensitive=False), default="normalized", show_default=True)
@click.option("--output", "-o", "output_dir", type=click.Path(path_type=Path), default=None, help="Export directory (or workspace root if --project is omitted).")
@click.option("--filename", "excel_filename", default=None, help="Custom Excel filename.")
@click.option("--page-offset", type=int, default=0, show_default=True)
@click.option("--target-article", default=None, help="Target article query for omnibus PDFs.")
@click.option(
    "--ocr-policy",
    type=click.Choice(["auto", "force_image_pages", "force_all", "never"], case_sensitive=False),
    default="auto",
    show_default=True,
)
@click.option("--ocr-workers", type=click.IntRange(1, 16), default=None)
@click.option("--extract-workers", type=click.IntRange(1, 16), default=None)
@click.option("--retry-failed", is_flag=True)
@click.pass_context
@_handle_cli_errors
def run(
    ctx: click.Context,
    project_dir: Path | None,
    input_path: Path,
    mode: str,
    provider: str,
    model: str | None,
    export_format: str,
    layer: str,
    output_dir: Path | None,
    excel_filename: str | None,
    page_offset: int,
    target_article: str | None,
    ocr_policy: str,
    ocr_workers: int | None,
    extract_workers: int | None,
    retry_failed: bool,
) -> None:
    """Run full pipeline (stops cleanly after indexing if --provider none)."""
    if project_dir is None and output_dir is None:
        raise click.UsageError("Must specify --project/-p or --output/-o.")
    if project_dir is None:
        assert output_dir is not None
        resolved_project = output_dir
        resolved_export_dir: Path | None = output_dir / "export"
    else:
        resolved_project = project_dir
        resolved_export_dir = output_dir

    cfg: AppConfig = ctx.obj["cfg"]
    res = run_full_pipeline(
        project_dir=resolved_project,
        input_path=input_path,
        mode=SourceType(mode.lower()),
        provider=provider.lower(),
        model=model,
        export_format=export_format.lower(),  # type: ignore[arg-type]
        layer=layer.lower(),  # type: ignore[arg-type]
        output_dir=resolved_export_dir,
        excel_filename=excel_filename,
        page_offset=page_offset,
        target_article_query=target_article,
        ocr_policy=OCRPolicy(ocr_policy.lower()),
        ocr_workers=ocr_workers,
        extract_workers=extract_workers,
        retry_failed=retry_failed,
        cfg=cfg,
    )
    _emit_json(res)
    if res.get("stopped_after") == "index":
        click.echo(res["next_steps"], err=True)


@main.command("eval")
@click.option("--pred", required=True, type=click.Path(path_type=Path), help="Predicted records (.json / .jsonl / .xlsx).")
@click.option("--gold", required=True, type=click.Path(path_type=Path), help="Gold reference records (.json / .jsonl / .xlsx).")
@click.option("--human-annotated", is_flag=True, help="Assert gold dataset is human-annotated clinical gold.")
@click.option("--refuse-unannotated", is_flag=True, help="Fail with error if --human-annotated is not set.")
@click.option("--output", "-o", "output_path", type=click.Path(path_type=Path), default=None, help="Optional JSON path to write evaluation report.")
@click.pass_context
@_handle_cli_errors
def eval_cmd(
    ctx: click.Context,
    pred: Path,
    gold: Path,
    human_annotated: bool,
    refuse_unannotated: bool,
    output_path: Path | None,
) -> None:
    """Evaluate predicted records against gold references (field EM / P / R / F1)."""
    pred_records = _load_records_for_eval(pred)
    gold_records = _load_records_for_eval(gold)
    try:
        report = evaluate_against_gold(
            pred_records=pred_records,
            gold_records=gold_records,
            dataset_is_human_annotated=human_annotated,
            refuse_unannotated=refuse_unannotated,
        )
    except ValueError as exc:
        raise ConfigUsageError(str(exc)) from exc

    payload = report.model_dump(mode="json")
    if output_path is not None:
        atomic_write_json(output_path, payload)
    _emit_json(payload)


if __name__ == "__main__":
    main()
