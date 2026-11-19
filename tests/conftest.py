"""Shared pytest fixtures for vet_case_extractor tests."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from vet_case_extractor.config import AppConfig, load_config
from vet_case_extractor.models import (
    CaseRecord,
    CaseSegment,
    DocumentRecord,
    EvidenceSpan,
    IndexSummary,
    LabTableRow,
    ProjectManifest,
    ReviewStatus,
    SourceType,
    TaskStatus,
)
from vet_case_extractor.utils.io import atomic_write_json

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from generate_synthetic_fixtures import generate_all_fixtures  # noqa: E402


@pytest.fixture(scope="session")
def app_config() -> AppConfig:
    """Return loaded default AppConfig with extraction_rules.yaml."""
    return load_config()


@pytest.fixture(scope="session")
def synthetic_pdf_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Generate the 7 synthetic test PDFs once per pytest session."""
    out_dir = tmp_path_factory.mktemp("synthetic_pdfs")
    generate_all_fixtures(out_dir)
    return out_dir


@pytest.fixture()
def sample_journal_records() -> list[CaseRecord]:
    """Return clean normalized + raw journal CaseRecords for QA and export tests."""
    return [
        CaseRecord(
            document_id="doc_a",
            file_sha256="a" * 64,
            case_id="doc_a_case_01",
            case_order=1,
            source_type=SourceType.JOURNAL,
            source_title="中国兽医杂志",
            publication_year="2024",
            received_year="2023",
            page_range="1-2",
            title="猫血支原体感染病例报告",
            medical_history_raw="患猫，3岁，雄性。体温39.8℃，可视黏膜苍白。",
            medical_test_raw="RBC 2.8×10¹²/L，PCR检出猫血支原体阳性。",
            medical_treatment_raw="多西环素10 mg/kg，口服，每日2次。",
            diagnosis_raw="猫 血支原体感染",
            follow_up_raw="2周后复查食欲恢复。",
            notes_raw="",
            medical_history="患猫，3岁，雄性。体温39.8℃，可视黏膜苍白。",
            medical_test="RBC 2.8×10¹²/L，PCR检出猫血支原体阳性。",
            medical_treatment="多西环素10 mg/kg，po，bid。",
            diagnosis_normalized="猫血支原体感染",
            follow_up="2周后复查食欲恢复。",
            notes="",
            evidence_spans=[
                EvidenceSpan(
                    document_id="doc_a",
                    file_sha256="a" * 64,
                    case_id="doc_a_case_01",
                    field_name="diagnosis_normalized",
                    page_index=0,
                    quote="猫血支原体感染",
                    match_score=1.0,
                    extraction_method="test",
                    review_status=ReviewStatus.OK,
                )
            ],
            status=TaskStatus.COMPLETED,
            review_status=ReviewStatus.OK,
            extraction_method="test",
        ),
        CaseRecord(
            document_id="doc_b",
            file_sha256="b" * 64,
            case_id="doc_b_case_01",
            case_order=1,
            source_type=SourceType.JOURNAL,
            source_title="畜牧与兽医",
            publication_year="2023",
            received_year="2023",
            page_range="1",
            title="犬骨肉瘤病例报告",
            medical_history_raw="患犬，8岁，右前肢跛行。体格检查体温38.5℃。",
            medical_test_raw="X线片示肱骨近端溶骨性病变。",
            medical_treatment_raw="截肢手术。",
            diagnosis_raw="犬骨肉瘤（肱骨近端）；肺转移",
            follow_up_raw="",
            notes_raw="",
            medical_history="患犬，8岁，右前肢跛行。体格检查体温38.5℃。",
            medical_test="X线片示肱骨近端溶骨性病变。",
            medical_treatment="截肢手术。",
            diagnosis_normalized="犬骨肉瘤（肱骨近端）；肺转移",
            follow_up="",
            notes="",
            evidence_spans=[
                EvidenceSpan(
                    document_id="doc_b",
                    file_sha256="b" * 64,
                    case_id="doc_b_case_01",
                    field_name="medical_test",
                    page_index=0,
                    quote="X线片示肱骨近端溶骨性病变",
                    match_score=1.0,
                    extraction_method="test",
                    review_status=ReviewStatus.OK,
                )
            ],
            status=TaskStatus.COMPLETED,
            review_status=ReviewStatus.OK,
            extraction_method="test",
        ),
    ]


@pytest.fixture()
def sample_book_records() -> list[CaseRecord]:
    """Return clean book CaseRecords across 2 chapters."""
    return [
        CaseRecord(
            document_id="book_1",
            file_sha256="c" * 64,
            case_id="book_1_ch01_case01",
            case_order=1,
            source_type=SourceType.BOOK,
            chapter_number=1,
            chapter_title="第一章 肝酶升高",
            case_number=1,
            case_level=1,
            page_range="3",
            book_page_range="1",
            title="第一章 肝酶升高 · 病例1 等级1",
            medical_history_raw="3岁雄性犬，精神沉郁，体温38.5℃。",
            medical_test_raw="ALT 420 U/L。",
            medical_treatment_raw="",
            diagnosis_raw="犬急性肝细胞损伤",
            follow_up_raw="",
            notes_raw="",
            medical_history="3岁雄性犬，精神沉郁，体温38.5℃。",
            medical_test="ALT 420 U/L。",
            medical_treatment="",
            diagnosis_normalized="犬急性肝细胞损伤",
            follow_up="",
            notes="",
            evidence_spans=[
                EvidenceSpan(
                    document_id="book_1",
                    file_sha256="c" * 64,
                    case_id="book_1_ch01_case01",
                    field_name="medical_test",
                    page_index=2,
                    quote="ALT 420 U/L",
                    match_score=1.0,
                    extraction_method="test",
                    review_status=ReviewStatus.OK,
                )
            ],
            status=TaskStatus.COMPLETED,
            review_status=ReviewStatus.OK,
            extraction_method="test",
        ),
        CaseRecord(
            document_id="book_1",
            file_sha256="c" * 64,
            case_id="book_1_ch02_case01",
            case_order=2,
            source_type=SourceType.BOOK,
            chapter_number=2,
            chapter_title="第二章 肾功能异常",
            case_number=1,
            case_level=2,
            page_range="5",
            book_page_range="3",
            title="第二章 肾功能异常 · 病例1 等级2",
            medical_history_raw="7岁雌性犬，多饮多尿。",
            medical_test_raw="CREA 310 μmol/L。",
            medical_treatment_raw="",
            diagnosis_raw="犬慢性肾脏病",
            follow_up_raw="",
            notes_raw="",
            medical_history="7岁雌性犬，多饮多尿。",
            medical_test="CREA 310 μmol/L。",
            medical_treatment="",
            diagnosis_normalized="犬慢性肾脏病",
            follow_up="",
            notes="",
            evidence_spans=[
                EvidenceSpan(
                    document_id="book_1",
                    file_sha256="c" * 64,
                    case_id="book_1_ch02_case01",
                    field_name="diagnosis_normalized",
                    page_index=4,
                    quote="犬慢性肾脏病",
                    match_score=1.0,
                    extraction_method="test",
                    review_status=ReviewStatus.OK,
                )
            ],
            status=TaskStatus.COMPLETED,
            review_status=ReviewStatus.OK,
            extraction_method="test",
        ),
    ]


def write_sample_project(
    project_dir: Path,
    records: list[CaseRecord],
    source_type: SourceType = SourceType.JOURNAL,
    filenames: dict[str, str] | None = None,
) -> Path:
    """Create minimal valid manifest.json, index.json, and cases/<id>/record.json files."""
    docs: dict[str, DocumentRecord] = {}
    segments: list[CaseSegment] = []
    case_statuses: dict[str, TaskStatus] = {}
    fn_map = filenames or {}

    for rec in records:
        if rec.document_id not in docs:
            fname = fn_map.get(rec.document_id, f"{rec.document_id}.pdf")
            docs[rec.document_id] = DocumentRecord(
                document_id=rec.document_id,
                filename=fname,
                file_path=str(project_dir / "inputs" / fname),
                file_sha256=rec.file_sha256,
                source_type=source_type,
                page_count=1,
                status=TaskStatus.COMPLETED,
            )
        segments.append(
            CaseSegment(
                case_id=rec.case_id,
                document_id=rec.document_id,
                source_filename=fn_map.get(rec.document_id, f"{rec.document_id}.pdf"),
                source_type=rec.source_type,
                case_order=rec.case_order,
                source_journal=rec.source_title,
                publication_year=rec.publication_year,
                received_year=rec.received_year,
                chapter_number=rec.chapter_number,
                chapter_title=rec.chapter_title,
                case_number=rec.case_number,
                case_level=rec.case_level,
                title=rec.title,
                page_indices=[0],
                page_range=rec.page_range or "1",
                book_page_range=rec.book_page_range,
                lab_rows=list(rec.lab_rows),
            )
        )
        case_statuses[rec.case_id] = rec.status
        atomic_write_json(project_dir / "cases" / rec.case_id / "record.json", rec)

    manifest = ProjectManifest(
        project_dir=str(project_dir),
        source_type=source_type,
        documents=docs,
        case_statuses=case_statuses,
    )
    index = IndexSummary(
        total_documents=len(docs),
        total_cases=len(segments),
        cases=segments,
    )
    atomic_write_json(project_dir / "manifest.json", manifest)
    atomic_write_json(project_dir / "index.json", index)
    return project_dir


@pytest.fixture()
def sample_lab_row() -> LabTableRow:
    return LabTableRow(
        item_raw="WBC",
        value_raw="12.5",
        unit_raw="×10¹²/L",
        ref_range_raw="6.0~17.0×10⁹/L",
        flag_raw="",
        page_index=0,
        anomalies=["量级指数冲突: 实测 ×10¹²/L 与参考区间 ×10⁹/L 不符"],
    )
