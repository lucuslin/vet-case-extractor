"""Project-wide QA orchestration, Markdown reporting, and human review queue generation."""

from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from vet_case_extractor.config import AppConfig, load_config
from vet_case_extractor.models import (
    SCHEMA_VERSION,
    CaseRecord,
    CaseSegment,
    IndexSummary,
    ProjectManifest,
    QAIssue,
    QAReport,
    ReviewStatus,
    Severity,
    SourceType,
    TaskStatus,
    utc_now_iso,
)
from vet_case_extractor.utils.io import atomic_write_json, atomic_write_text
from vet_case_extractor.validation.semantic import validate_semantic
from vet_case_extractor.validation.structural import (
    check_project_json_integrity,
    validate_structural,
)

_REVIEW_TRIGGER_RULES = frozenset(
    {
        "OCR_LAB_ANOMALY",
        "OCR_SUSPECT_RECORD",
        "OCR_KNOWN_TYPO_CANDIDATE",
        "OCR_PAGES_FAILED",
        "PROV_UNGROUNDED_FIELD",
        "PROV_UNGROUNDED_RECORD",
        "PROV_MISSING_EVIDENCE",
        "SEM_TREATMENT_FOLLOWUP_LEAK",
        "SEM_PE_IN_MEDICAL_TEST",
        "SEM_REASONING_IN_FACT_FIELD",
        "SEM_EXTERNAL_CARE_MISPLACED",
        "SEM_TIER_C_OVERSTRIPPED",
        "STR_BOUNDARY_UNCERTAIN",
        "STR_MISSING_EXTRACTED_CASE",
    }
)


def _check_missing_provenance(records: list[CaseRecord]) -> list[QAIssue]:
    issues: list[QAIssue] = []
    idx = 0
    for rec in records:
        if rec.status not in (TaskStatus.COMPLETED, TaskStatus.REVIEW_REQUIRED):
            continue
        has_substance = any(
            getattr(rec, f).strip()
            for f in (
                "medical_history",
                "medical_test",
                "medical_treatment",
                "diagnosis_normalized",
                "follow_up",
            )
        )
        if has_substance and rec.extraction_method.strip() and not rec.evidence_spans:
            idx += 1
            issues.append(
                QAIssue(
                    issue_id=f"PROV-{idx:04d}",
                    category="provenance",
                    rule_id="PROV_MISSING_EVIDENCE",
                    severity=Severity.WARNING,
                    case_id=rec.case_id,
                    document_id=rec.document_id,
                    message=f"病例 `{rec.case_id}` 已抽取内容但缺乏 evidence_spans 原文溯源指针。",
                    suggestion="运行证据溯源对齐或对照页图人工复核。",
                )
            )
    return issues


def build_qa_report(
    project_dir: Path,
    records: list[CaseRecord],
    manifest: ProjectManifest | None = None,
    index: IndexSummary | None = None,
    cfg: AppConfig | None = None,
    json_errors: list[QAIssue] | None = None,
) -> QAReport:
    """Compute a complete QAReport from project artifacts without touching disk."""
    resolved_cfg = cfg or load_config()
    issues: list[QAIssue] = []
    issues.extend(
        validate_structural(
            records=records,
            manifest=manifest,
            index=index,
            json_errors=json_errors,
        )
    )
    issues.extend(validate_semantic(records=records, rules=resolved_cfg.rules))
    issues.extend(_check_missing_provenance(records))

    completed_cases = 0
    review_required_cases = 0
    failed_cases = 0
    pending_cases = 0

    seen_case_ids: set[str] = set()
    for rec in records:
        seen_case_ids.add(rec.case_id)
        if rec.status == TaskStatus.FAILED or rec.review_status == ReviewStatus.FAILED:
            failed_cases += 1
        elif rec.status == TaskStatus.PENDING:
            pending_cases += 1
        elif rec.status == TaskStatus.REVIEW_REQUIRED or rec.review_status != ReviewStatus.OK:
            review_required_cases += 1
        else:
            completed_cases += 1

    if index is not None:
        for seg in index.cases:
            if seg.case_id not in seen_case_ids:
                seen_case_ids.add(seg.case_id)
                pending_cases += 1

    total_cases = completed_cases + review_required_cases + failed_cases + pending_cases
    total_documents = (
        len(manifest.documents)
        if manifest is not None
        else len({rec.document_id for rec in records if rec.document_id})
    )

    empty_field_counts = {
        "medical_history": sum(1 for r in records if not r.medical_history.strip()),
        "medical_test": sum(1 for r in records if not r.medical_test.strip()),
        "medical_treatment": sum(1 for r in records if not r.medical_treatment.strip()),
        "Dx": sum(1 for r in records if not r.diagnosis_normalized.strip()),
        "follow-up": sum(1 for r in records if not r.follow_up.strip()),
        "notes": sum(1 for r in records if not r.notes.strip()),
    }

    sev_counts = Counter(issue.severity.value for issue in issues)
    rule_counts = Counter(issue.rule_id for issue in issues)

    has_error = any(issue.severity == Severity.ERROR for issue in issues) or failed_cases > 0
    has_review_trigger = (
        review_required_cases > 0
        or pending_cases > 0
        or any(
            issue.severity == Severity.WARNING and issue.rule_id in _REVIEW_TRIGGER_RULES
            for issue in issues
        )
    )
    has_warning = any(issue.severity == Severity.WARNING for issue in issues)

    if has_error or total_documents == 0:
        overall_status = "FAIL"
    elif has_review_trigger:
        overall_status = "REVIEW_REQUIRED"
    elif has_warning:
        overall_status = "PASS_WITH_WARNINGS"
    else:
        overall_status = "PASS"

    return QAReport(
        project_dir=str(project_dir),
        total_documents=total_documents,
        total_cases=total_cases,
        completed_cases=completed_cases,
        review_required_cases=review_required_cases,
        failed_cases=failed_cases,
        pending_cases=pending_cases,
        empty_field_counts=empty_field_counts,
        issue_counts_by_severity=dict(sorted(sev_counts.items())),
        issue_counts_by_rule=dict(sorted(rule_counts.items())),
        issues=issues,
        overall_status=overall_status,
    )


def build_review_queue(
    report: QAReport,
    records: list[CaseRecord],
    index: IndexSummary | None = None,
) -> dict[str, Any]:
    """Build structured human review queue payload linking cases to page numbers and images."""
    seg_by_id: dict[str, CaseSegment] = (
        {seg.case_id: seg for seg in index.cases} if index is not None else {}
    )
    issues_by_case: dict[str, list[QAIssue]] = defaultdict(list)
    for issue in report.issues:
        if issue.case_id:
            issues_by_case[issue.case_id].append(issue)

    items: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    for rec in records:
        case_issues = issues_by_case.get(rec.case_id, [])
        lab_anomalies = [
            {
                "item_raw": row.item_raw,
                "value_raw": row.value_raw,
                "unit_raw": row.unit_raw,
                "ref_range_raw": row.ref_range_raw,
                "page_index": row.page_index,
                "anomalies": list(row.anomalies),
            }
            for row in rec.lab_rows
            if row.anomalies
        ]
        needs_review = (
            rec.status in (TaskStatus.REVIEW_REQUIRED, TaskStatus.FAILED, TaskStatus.PENDING)
            or rec.review_status != ReviewStatus.OK
            or bool(lab_anomalies)
            or any(i.severity in (Severity.ERROR, Severity.WARNING) for i in case_issues)
        )
        if not needs_review:
            continue

        seen_ids.add(rec.case_id)
        seg = seg_by_id.get(rec.case_id)
        items.append(
            {
                "case_id": rec.case_id,
                "document_id": rec.document_id,
                "title": rec.title,
                "source_type": rec.source_type.value,
                "page_range": rec.page_range or (seg.page_range if seg else ""),
                "book_page_range": rec.book_page_range or (seg.book_page_range if seg else ""),
                "page_indices": list(seg.page_indices) if seg else [],
                "image_paths": list(seg.image_paths) if seg else [],
                "status": rec.status.value,
                "review_status": rec.review_status.value,
                "review_reasons": list(rec.review_reasons),
                "lab_anomalies": lab_anomalies,
                "issues": [issue.model_dump() for issue in case_issues],
            }
        )

    if index is not None:
        for seg in index.cases:
            if seg.case_id in seen_ids:
                continue
            case_issues = issues_by_case.get(seg.case_id, [])
            if seg.boundary_uncertain or case_issues:
                items.append(
                    {
                        "case_id": seg.case_id,
                        "document_id": seg.document_id,
                        "title": seg.title,
                        "source_type": seg.source_type.value,
                        "page_range": seg.page_range,
                        "book_page_range": seg.book_page_range,
                        "page_indices": list(seg.page_indices),
                        "image_paths": list(seg.image_paths),
                        "status": TaskStatus.PENDING.value,
                        "review_status": ReviewStatus.REVIEW_REQUIRED.value
                        if seg.boundary_uncertain
                        else ReviewStatus.OK.value,
                        "review_reasons": list(seg.boundary_notes),
                        "lab_anomalies": [],
                        "issues": [issue.model_dump() for issue in case_issues],
                    }
                )

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": utc_now_iso(),
        "project_dir": report.project_dir,
        "overall_status": report.overall_status,
        "total_items": len(items),
        "items": items,
    }


def render_qa_markdown(
    report: QAReport,
    records: list[CaseRecord] | None = None,
) -> str:
    """Render a human-readable Markdown QA report."""
    lines: list[str] = [
        "# 兽医临床病例抽取 QA 报告",
        "",
        f"- **项目目录**: `{report.project_dir}`",
        f"- **生成时间**: `{report.generated_at}`",
        f"- **Schema 版本**: `{report.schema_version}`",
        f"- **总体状态**: **{report.overall_status}**",
        "",
        "## 1. 统计摘要",
        "",
        "| 指标 | 数值 |",
        "|---|---:|",
        f"| 文档总数 | {report.total_documents} |",
        f"| 病例总数 | {report.total_cases} |",
        f"| 已完成病例 | {report.completed_cases} |",
        f"| 待人工复核病例 | {report.review_required_cases} |",
        f"| 失败病例 | {report.failed_cases} |",
        f"| 待抽取病例 | {report.pending_cases} |",
        "",
        "## 2. 字段空值统计",
        "",
        "| 字段 | 空值数 | 占比 |",
        "|---|---:|---:|",
    ]
    denom = len(records) if records else max(report.total_cases - report.pending_cases, 0)
    for fname, empty_n in report.empty_field_counts.items():
        pct = f"{(empty_n / denom * 100):.1f}%" if denom > 0 else "N/A"
        lines.append(f"| `{fname}` | {empty_n} | {pct} |")

    if records and any(r.source_type == SourceType.BOOK for r in records):
        lines.extend(
            [
                "",
                "> 提示：扫描图版教学书籍（以实验室检查解读为主）`medical_treatment` / `Dx` / `follow-up` 存在 10%~20% 留空通常属正常原文现象，严禁编造或补充假设治疗。",
            ]
        )

    lines.extend(
        [
            "",
            "## 3. 问题分布",
            "",
            f"- **按严重程度**: {report.issue_counts_by_severity or {'none': 0}}",
            f"- **按规则编号**: {report.issue_counts_by_rule or {'none': 0}}",
            "",
            "## 4. 检出问题与存疑清单",
            "",
        ]
    )

    if not report.issues:
        lines.append("未检出结构、语义、OCR 或溯源问题。")
    else:
        lines.append("| ID | 级别 | 类别 | 规则 | 病例ID | 字段 | 说明 | 建议 |")
        lines.append("|---|---|---|---|---|---|---|---|")
        for issue in report.issues:
            msg = issue.message.replace("|", "\\|").replace("\n", " ")
            sug = issue.suggestion.replace("|", "\\|").replace("\n", " ")
            lines.append(
                f"| {issue.issue_id} | {issue.severity.value} | {issue.category} | "
                f"`{issue.rule_id}` | `{issue.case_id or '-'}` | `{issue.field_name or '-'}` | "
                f"{msg} | {sug or '-'} |"
            )

    lines.append("")
    return "\n".join(lines)


def run_project_qa(project_dir: Path, cfg: AppConfig | None = None) -> QAReport:
    """Run full project QA and write `qa/qa_report.json`, `qa/qa_report.md`, and `qa/review_queue.json`."""
    resolved_dir = Path(project_dir)
    resolved_cfg = cfg or load_config()

    json_errors, manifest, index, records = check_project_json_integrity(resolved_dir)
    report = build_qa_report(
        project_dir=resolved_dir,
        records=records,
        manifest=manifest,
        index=index,
        cfg=resolved_cfg,
        json_errors=json_errors,
    )
    review_queue = build_review_queue(report=report, records=records, index=index)
    md_text = render_qa_markdown(report=report, records=records)

    qa_dir = resolved_dir / "qa"
    qa_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_json(qa_dir / "qa_report.json", report)
    atomic_write_text(qa_dir / "qa_report.md", md_text)
    atomic_write_json(qa_dir / "review_queue.json", review_queue)

    return report
