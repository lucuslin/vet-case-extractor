"""Structural QA checks for project files, schemas, metadata, counts, and book continuity."""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path

from pydantic import ValidationError

from vet_case_extractor.models import (
    SCHEMA_VERSION,
    CaseRecord,
    CaseSegment,
    IndexSummary,
    ProjectManifest,
    QAIssue,
    Severity,
    SourceType,
    TaskStatus,
)

_LEVEL_IN_TITLE_RE = re.compile(r"等级\s*(\d+)")


class _IssueCounter:
    def __init__(self, prefix: str = "STR") -> None:
        self.prefix = prefix
        self.count = 0

    def next_id(self) -> str:
        self.count += 1
        return f"{self.prefix}-{self.count:04d}"


def _try_parse_json(path: Path) -> tuple[object | None, str | None]:
    """Read and parse a JSON file, returning `(data, error_message)`."""
    try:
        raw = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        return None, f"UTF-8 解码失败: {exc}"
    except OSError as exc:
        return None, f"读取失败: {exc}"
    if not raw.strip():
        return None, "JSON 文件为空或被截断"
    try:
        return json.loads(raw), None
    except json.JSONDecodeError as exc:
        return None, f"JSON 解析/截断错误 (line {exc.lineno}, col {exc.colno}): {exc.msg}"


def check_project_json_integrity(
    project_dir: Path,
) -> tuple[list[QAIssue], ProjectManifest | None, IndexSummary | None, list[CaseRecord]]:
    """Inspect project JSON artifacts for truncation, corruption, and schema compliance."""
    issues: list[QAIssue] = []
    counter = _IssueCounter("STRJSON")
    manifest: ProjectManifest | None = None
    index: IndexSummary | None = None
    records: list[CaseRecord] = []

    manifest_path = project_dir / "manifest.json"
    if not manifest_path.exists():
        issues.append(
            QAIssue(
                issue_id=counter.next_id(),
                category="structural",
                rule_id="STR_MISSING_MANIFEST",
                severity=Severity.ERROR,
                message=f"缺少项目清单文件: {manifest_path}",
                suggestion="请先运行 `vetcase ingest` 初始化项目工作区。",
            )
        )
    else:
        data, err = _try_parse_json(manifest_path)
        if err:
            issues.append(
                QAIssue(
                    issue_id=counter.next_id(),
                    category="structural",
                    rule_id="STR_CORRUPTED_JSON",
                    severity=Severity.ERROR,
                    message=f"manifest.json 损坏或截断: {err}",
                    suggestion="检查磁盘/写入状态，必要时从备份恢复或重新 ingest。",
                )
            )
        else:
            try:
                manifest = ProjectManifest.model_validate(data)
            except ValidationError as exc:
                issues.append(
                    QAIssue(
                        issue_id=counter.next_id(),
                        category="structural",
                        rule_id="STR_SCHEMA_INVALID",
                        severity=Severity.ERROR,
                        message=f"manifest.json Schema 校验失败: {exc.errors()[0].get('msg', str(exc))}",
                        suggestion="核对 manifest.json 字段与当前 Schema 版本是否兼容。",
                    )
                )

    index_path = project_dir / "index.json"
    if index_path.exists():
        data, err = _try_parse_json(index_path)
        if err:
            issues.append(
                QAIssue(
                    issue_id=counter.next_id(),
                    category="structural",
                    rule_id="STR_CORRUPTED_JSON",
                    severity=Severity.ERROR,
                    message=f"index.json 损坏或截断: {err}",
                    suggestion="重新运行 `vetcase index` 重建病例索引。",
                )
            )
        else:
            try:
                index = IndexSummary.model_validate(data)
            except ValidationError as exc:
                issues.append(
                    QAIssue(
                        issue_id=counter.next_id(),
                        category="structural",
                        rule_id="STR_SCHEMA_INVALID",
                        severity=Severity.ERROR,
                        message=f"index.json Schema 校验失败: {exc.errors()[0].get('msg', str(exc))}",
                        suggestion="重新运行 `vetcase index` 重建病例索引。",
                    )
                )
    elif manifest is not None and manifest.documents:
        issues.append(
            QAIssue(
                issue_id=counter.next_id(),
                category="structural",
                rule_id="STR_MISSING_INDEX",
                severity=Severity.WARNING,
                message="项目尚未生成 index.json 索引文件。",
                suggestion="运行 `vetcase index` 建立病例与文档映射。",
            )
        )

    for folder_name, cat in (("pages_raw", "structural"), ("pages_ocr", "ocr")):
        folder = project_dir / folder_name
        if folder.is_dir():
            for json_file in sorted(folder.rglob("*.json")):
                data, err = _try_parse_json(json_file)
                if err:
                    issues.append(
                        QAIssue(
                            issue_id=counter.next_id(),
                            category=cat,
                            rule_id="STR_CORRUPTED_JSON",
                            severity=Severity.ERROR,
                            message=f"页面产物 JSON 损坏 ({json_file.relative_to(project_dir)}): {err}",
                            suggestion="删除损坏缓存文件后重跑对应页面提取或 OCR。",
                        )
                    )

    cases_dir = project_dir / "cases"
    seen_case_files: set[Path] = set()
    if cases_dir.is_dir():
        for json_file in sorted(cases_dir.rglob("*.json")):
            if json_file in seen_case_files:
                continue
            seen_case_files.add(json_file)
            data, err = _try_parse_json(json_file)
            if err:
                issues.append(
                    QAIssue(
                        issue_id=counter.next_id(),
                        category="structural",
                        rule_id="STR_CORRUPTED_JSON",
                        severity=Severity.ERROR,
                        message=f"病例 JSON 损坏或截断 ({json_file.relative_to(project_dir)}): {err}",
                        suggestion="使用 `--retry-failed` 补抽该病例，或核对截断产物。",
                    )
                )
                continue
            if json_file.name == "record.json" or (
                isinstance(data, dict) and "case_id" in data and "document_id" in data
            ):
                try:
                    records.append(CaseRecord.model_validate(data))
                except ValidationError as exc:
                    issues.append(
                        QAIssue(
                            issue_id=counter.next_id(),
                            category="structural",
                            rule_id="STR_SCHEMA_INVALID",
                            severity=Severity.ERROR,
                            case_id=data.get("case_id") if isinstance(data, dict) else None,
                            message=f"病例记录 Schema 校验失败 ({json_file.relative_to(project_dir)}): {exc.errors()[0].get('msg', str(exc))}",
                            suggestion="检查病例 JSON 必填字段与类型后重新抽取或导入。",
                        )
                    )

    rows_dir = project_dir / "rows"
    if rows_dir.is_dir():
        for json_file in sorted(rows_dir.rglob("*.json")):
            if "_part" in json_file.stem:
                issues.append(
                    QAIssue(
                        issue_id=counter.next_id(),
                        category="structural",
                        rule_id="STR_RESCUE_PART_FILE",
                        severity=Severity.WARNING,
                        message=f"检测到截断抢救残留文件: {json_file.relative_to(project_dir)}",
                        suggestion="确认该抢救文件未与正式批次重复计数后清理或归档。",
                    )
                )
            data, err = _try_parse_json(json_file)
            if err:
                issues.append(
                    QAIssue(
                        issue_id=counter.next_id(),
                        category="structural",
                        rule_id="STR_CORRUPTED_JSON",
                        severity=Severity.ERROR,
                        message=f"批次 JSON 损坏或截断 ({json_file.relative_to(project_dir)}): {err}",
                        suggestion="按截断抢救 SOP 截取完整对象并补抽剩余病例。",
                    )
                )

    return issues, manifest, index, records


def _check_book_continuity(
    items: list[CaseRecord] | list[CaseSegment],
    source_label: str,
    counter: _IssueCounter,
) -> list[QAIssue]:
    """Check 1..max chapter continuity and duplicate case numbers for book records/segments."""
    issues: list[QAIssue] = []
    by_chapter: dict[int, list[tuple[int, str]]] = defaultdict(list)
    for item in items:
        if item.source_type != SourceType.BOOK:
            continue
        if item.chapter_number is not None and item.case_number is not None:
            by_chapter[item.chapter_number].append((item.case_number, item.case_id))

    for chapter_num in sorted(by_chapter):
        pairs = by_chapter[chapter_num]
        nums = [n for n, _ in pairs]
        counts = Counter(nums)
        dups = sorted(n for n, c in counts.items() if c > 1)
        if dups:
            dup_case_ids = [cid for n, cid in pairs if n in dups]
            issues.append(
                QAIssue(
                    issue_id=counter.next_id(),
                    category="structural",
                    rule_id="STR_BOOK_DUPLICATE_CASE_NUMBER",
                    severity=Severity.ERROR,
                    case_id=dup_case_ids[0] if dup_case_ids else None,
                    message=f"{source_label}第 {chapter_num} 章存在重复病例号: {dups}",
                    suggestion="核对原书目录与页图，合并或修正重复编号病例。",
                )
            )
        if nums:
            max_num = max(nums)
            missing = [n for n in range(1, max_num + 1) if n not in counts]
            if missing:
                issues.append(
                    QAIssue(
                        issue_id=counter.next_id(),
                        category="structural",
                        rule_id="STR_CHAPTER_CONTINUITY",
                        severity=Severity.ERROR,
                        message=f"{source_label}第 {chapter_num} 章病例号不连续，缺失编号: {missing} (最大编号 {max_num})",
                        suggestion="回查该章页图，通常为病例标题行 OCR 丢失或正则误判。",
                    )
                )
    return issues


def validate_structural(
    records: list[CaseRecord],
    manifest: ProjectManifest | None = None,
    index: IndexSummary | None = None,
    json_errors: list[QAIssue] | None = None,
) -> list[QAIssue]:
    """Run structural QA checks across manifest, index, and extracted CaseRecords."""
    issues: list[QAIssue] = list(json_errors or [])
    counter = _IssueCounter("STR")

    # 1. Manifest checks
    if manifest is not None:
        sha_to_docs: dict[str, list[str]] = defaultdict(list)
        for doc_id, doc in manifest.documents.items():
            if doc_id != doc.document_id:
                issues.append(
                    QAIssue(
                        issue_id=counter.next_id(),
                        category="structural",
                        rule_id="STR_DOCUMENT_ID_KEY_MISMATCH",
                        severity=Severity.ERROR,
                        document_id=doc_id,
                        message=f"manifest 文档键 `{doc_id}` 与内部 document_id `{doc.document_id}` 不一致。",
                    )
                )
            sha_to_docs[doc.file_sha256].append(doc_id)
            if doc.status == TaskStatus.FAILED:
                issues.append(
                    QAIssue(
                        issue_id=counter.next_id(),
                        category="structural",
                        rule_id="STR_DOCUMENT_FAILED",
                        severity=Severity.ERROR,
                        document_id=doc.document_id,
                        message=f"文档 `{doc.filename}` 处理失败: {doc.error or '未知错误'}",
                        suggestion="排查 PDF 文件是否损坏后使用 `--retry-failed` 重试。",
                    )
                )
            if doc.pages_failed:
                issues.append(
                    QAIssue(
                        issue_id=counter.next_id(),
                        category="ocr",
                        rule_id="OCR_PAGES_FAILED",
                        severity=Severity.WARNING,
                        document_id=doc.document_id,
                        page_indices=list(doc.pages_failed),
                        message=f"文档 `{doc.filename}` 存在处理/OCR 失败页面: {doc.pages_failed}",
                        suggestion="检查对应页图并重跑 OCR。",
                    )
                )
        for sha, doc_ids in sha_to_docs.items():
            if sha and len(doc_ids) > 1:
                issues.append(
                    QAIssue(
                        issue_id=counter.next_id(),
                        category="structural",
                        rule_id="STR_DUPLICATE_DOCUMENT",
                        severity=Severity.ERROR,
                        document_id=doc_ids[0],
                        message=f"检测到重复 SHA-256 ({sha[:12]}...) 对应多个 document_id: {doc_ids}",
                        suggestion="去重重复文档，保留单一 document_id。",
                    )
                )

    # 2. Record-level schema, duplicate, and metadata checks
    seen_case_ids: dict[str, int] = {}
    seen_doc_orders: set[tuple[str, int]] = set()
    records_by_id: dict[str, CaseRecord] = {}

    for rec in records:
        if rec.schema_version != SCHEMA_VERSION:
            issues.append(
                QAIssue(
                    issue_id=counter.next_id(),
                    category="structural",
                    rule_id="STR_SCHEMA_VERSION_MISMATCH",
                    severity=Severity.WARNING,
                    case_id=rec.case_id,
                    document_id=rec.document_id,
                    message=f"Schema 版本不一致: 期望 {SCHEMA_VERSION}，实际 {rec.schema_version}",
                )
            )

        if not rec.document_id.strip() or not rec.case_id.strip() or not rec.file_sha256.strip():
            issues.append(
                QAIssue(
                    issue_id=counter.next_id(),
                    category="structural",
                    rule_id="STR_MISSING_REQUIRED_METADATA",
                    severity=Severity.ERROR,
                    case_id=rec.case_id or None,
                    document_id=rec.document_id or None,
                    message="病例记录缺少必填溯源标识 (document_id / case_id / file_sha256)。",
                )
            )

        if rec.case_id in seen_case_ids:
            issues.append(
                QAIssue(
                    issue_id=counter.next_id(),
                    category="structural",
                    rule_id="STR_DUPLICATE_CASE_ID",
                    severity=Severity.ERROR,
                    case_id=rec.case_id,
                    document_id=rec.document_id,
                    message=f"存在重复 case_id: `{rec.case_id}`",
                    suggestion="检查批次合并或重跑索引以保证 case_id 唯一。",
                )
            )
        else:
            seen_case_ids[rec.case_id] = 1
            records_by_id[rec.case_id] = rec

        order_key = (rec.document_id, rec.case_order)
        if order_key in seen_doc_orders:
            issues.append(
                QAIssue(
                    issue_id=counter.next_id(),
                    category="structural",
                    rule_id="STR_DUPLICATE_CASE_ORDER",
                    severity=Severity.WARNING,
                    case_id=rec.case_id,
                    document_id=rec.document_id,
                    message=f"文档 `{rec.document_id}` 内存在重复 case_order={rec.case_order}",
                )
            )
        seen_doc_orders.add(order_key)

        if rec.status == TaskStatus.FAILED:
            issues.append(
                QAIssue(
                    issue_id=counter.next_id(),
                    category="structural",
                    rule_id="STR_CASE_FAILED",
                    severity=Severity.ERROR,
                    case_id=rec.case_id,
                    document_id=rec.document_id,
                    message=f"病例抽取失败: {rec.error or '未记录错误原因'}",
                    suggestion="使用 `vetcase extract --retry-failed` 重新抽取。",
                )
            )
        elif rec.status == TaskStatus.PENDING:
            issues.append(
                QAIssue(
                    issue_id=counter.next_id(),
                    category="structural",
                    rule_id="STR_CASE_PENDING",
                    severity=Severity.WARNING,
                    case_id=rec.case_id,
                    document_id=rec.document_id,
                    message="病例尚处于 pending 待抽取状态。",
                    suggestion="运行 `vetcase extract` 或 `import-cases` 完成抽取。",
                )
            )

        if rec.status in (TaskStatus.COMPLETED, TaskStatus.REVIEW_REQUIRED):
            if not rec.title.strip():
                issues.append(
                    QAIssue(
                        issue_id=counter.next_id(),
                        category="structural",
                        rule_id="STR_MISSING_TITLE",
                        severity=Severity.ERROR,
                        case_id=rec.case_id,
                        document_id=rec.document_id,
                        field_name="title",
                        message="已完成病例缺少 title。",
                    )
                )
            if rec.source_type == SourceType.JOURNAL:
                missing_meta = []
                if not rec.source_title.strip():
                    missing_meta.append("source_title(刊名)")
                if not rec.formatted_year():
                    missing_meta.append("year(发表年)")
                if missing_meta:
                    issues.append(
                        QAIssue(
                            issue_id=counter.next_id(),
                            category="structural",
                            rule_id="STR_MISSING_JOURNAL_METADATA",
                            severity=Severity.WARNING,
                            case_id=rec.case_id,
                            document_id=rec.document_id,
                            field_name="source_title" if "source_title(刊名)" in missing_meta else "publication_year",
                            message=f"期刊病例缺少元数据: {', '.join(missing_meta)}",
                            suggestion="检查首页页眉/版权栏原文补全中文刊名或发表/收稿年份。",
                        )
                    )
            elif rec.source_type == SourceType.BOOK:
                missing_book = []
                if rec.chapter_number is None and not rec.chapter_title.strip():
                    missing_book.append("chapter_number/chapter_title")
                if rec.case_number is None:
                    missing_book.append("case_number")
                if rec.case_level is None:
                    missing_book.append("case_level")
                if missing_book:
                    issues.append(
                        QAIssue(
                            issue_id=counter.next_id(),
                            category="structural",
                            rule_id="STR_MISSING_BOOK_METADATA",
                            severity=Severity.WARNING,
                            case_id=rec.case_id,
                            document_id=rec.document_id,
                            message=f"书籍病例缺少章节/编号/等级元数据: {', '.join(missing_book)}",
                        )
                    )

    # 3. Index vs records coverage, article-vs-case count, and book continuity/level checks
    if index is not None:
        if index.total_cases != len(index.cases):
            issues.append(
                QAIssue(
                    issue_id=counter.next_id(),
                    category="structural",
                    rule_id="STR_INDEX_COUNT_MISMATCH",
                    severity=Severity.WARNING,
                    message=f"index.json total_cases={index.total_cases} 与 cases 列表长度 {len(index.cases)} 不一致。",
                )
            )

        for dup_id in index.duplicate_case_ids:
            issues.append(
                QAIssue(
                    issue_id=counter.next_id(),
                    category="structural",
                    rule_id="STR_DUPLICATE_CASE_ID",
                    severity=Severity.ERROR,
                    case_id=dup_id,
                    message=f"index.json 报告重复 case_id: `{dup_id}`",
                )
            )

        for cont_msg in index.chapter_continuity_issues:
            issues.append(
                QAIssue(
                    issue_id=counter.next_id(),
                    category="structural",
                    rule_id="STR_CHAPTER_CONTINUITY",
                    severity=Severity.ERROR,
                    message=f"index.json 章节连续性异常: {cont_msg}",
                    suggestion="回查书籍目录与页图确认漏页或 OCR 误判。",
                )
            )

        seg_by_id: dict[str, CaseSegment] = {seg.case_id: seg for seg in index.cases}
        expected_by_doc: Counter[str] = Counter(seg.document_id for seg in index.cases)
        actual_by_doc: Counter[str] = Counter(rec.document_id for rec in records)

        for doc_id in sorted(set(expected_by_doc) | set(actual_by_doc)):
            exp_n = expected_by_doc.get(doc_id, 0)
            act_n = actual_by_doc.get(doc_id, 0)
            if exp_n != act_n:
                issues.append(
                    QAIssue(
                        issue_id=counter.next_id(),
                        category="structural",
                        rule_id="STR_CASE_COUNT_MISMATCH",
                        severity=Severity.WARNING,
                        document_id=doc_id,
                        message=f"文档 `{doc_id}` 索引病例数 ({exp_n}) 与实抽病例数 ({act_n}) 不一致。",
                        suggestion="检查一文多病例拆行、漏抽病例或重复导入。",
                    )
                )

        for seg in index.cases:
            if seg.boundary_uncertain:
                issues.append(
                    QAIssue(
                        issue_id=counter.next_id(),
                        category="structural",
                        rule_id="STR_BOUNDARY_UNCERTAIN",
                        severity=Severity.WARNING,
                        case_id=seg.case_id,
                        document_id=seg.document_id,
                        page_indices=list(seg.page_indices),
                        message=f"病例边界存疑: {'; '.join(seg.boundary_notes) or '需核对合订本或章节边界'}",
                        suggestion="对照页图核对文章/病例起止页范围。",
                    )
                )
            if seg.case_id not in records_by_id:
                issues.append(
                    QAIssue(
                        issue_id=counter.next_id(),
                        category="structural",
                        rule_id="STR_MISSING_EXTRACTED_CASE",
                        severity=Severity.WARNING,
                        case_id=seg.case_id,
                        document_id=seg.document_id,
                        page_indices=list(seg.page_indices),
                        message=f"索引病例 `{seg.case_id}` ({seg.title}) 尚未完成抽取落盘。",
                        suggestion="运行 `vetcase extract` 或 `import-cases` 补齐该病例。",
                    )
                )

        for rec in records:
            seg = seg_by_id.get(rec.case_id)
            if seg is None:
                issues.append(
                    QAIssue(
                        issue_id=counter.next_id(),
                        category="structural",
                        rule_id="STR_UNINDEXED_CASE",
                        severity=Severity.WARNING,
                        case_id=rec.case_id,
                        document_id=rec.document_id,
                        message=f"已抽取病例 `{rec.case_id}` 未出现在 index.json 中。",
                    )
                )
                continue

            if seg.source_type == SourceType.BOOK or rec.source_type == SourceType.BOOK:
                if seg.case_level is not None and rec.case_level != seg.case_level:
                    issues.append(
                        QAIssue(
                            issue_id=counter.next_id(),
                            category="structural",
                            rule_id="STR_LEVEL_MISMATCH",
                            severity=Severity.ERROR,
                            case_id=rec.case_id,
                            document_id=rec.document_id,
                            field_name="case_level",
                            page_indices=list(seg.page_indices),
                            message=f"书籍等级与 index.json 不一致: 记录为 {rec.case_level}，索引为 {seg.case_level}",
                            suggestion="对照书籍页图标题行核对等级数字（常见 OCR 误识如 等级13 -> 等级3）。",
                        )
                    )
                m = _LEVEL_IN_TITLE_RE.search(rec.title)
                if m and seg.case_level is not None and int(m.group(1)) != seg.case_level:
                    issues.append(
                        QAIssue(
                            issue_id=counter.next_id(),
                            category="structural",
                            rule_id="STR_LEVEL_MISMATCH",
                            severity=Severity.ERROR,
                            case_id=rec.case_id,
                            document_id=rec.document_id,
                            field_name="title",
                            page_indices=list(seg.page_indices),
                            message=f"title 中等级（等级{m.group(1)}）与 index.json（等级{seg.case_level}）不一致。",
                            suggestion="核对原页图后统一 title 与 index 等级。",
                        )
                    )
                if rec.case_level is not None and not (1 <= rec.case_level <= 5):
                    issues.append(
                        QAIssue(
                            issue_id=counter.next_id(),
                            category="structural",
                            rule_id="STR_SUSPICIOUS_CASE_LEVEL",
                            severity=Severity.WARNING,
                            case_id=rec.case_id,
                            document_id=rec.document_id,
                            field_name="case_level",
                            page_indices=list(seg.page_indices),
                            message=f"书籍病例等级超出常规范围 (1~5): 等级{rec.case_level}",
                            suggestion="请回看对应页图确认是否存在 OCR 粘连（如 等级13 实为 等级3）。",
                        )
                    )

        # Avoid duplicate continuity reports if index already recorded chapter_continuity_issues
        if not index.chapter_continuity_issues:
            issues.extend(_check_book_continuity(index.cases, "索引", counter))
        issues.extend(_check_book_continuity(records, "实抽记录", counter))
    else:
        issues.extend(_check_book_continuity(records, "实抽记录", counter))

    return issues
