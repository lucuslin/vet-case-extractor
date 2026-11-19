"""Tests for ProjectWorkspace discovery, SHA-256 deduplication, and atomic artifact storage."""

from __future__ import annotations

from pathlib import Path

import pymupdf as fitz
import pytest

from vet_case_extractor.ingestion.project import ProjectWorkspace
from vet_case_extractor.models import (
    CaseRecord,
    CaseSegment,
    IndexSummary,
    PageArtifact,
    SourceType,
    TaskStatus,
)


def _write_minimal_pdf(path: Path, text: str = "Test") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((50, 72), text)
    doc.save(str(path))
    doc.close()
    return path


def test_workspace_init_and_manifest_roundtrip(tmp_path: Path) -> None:
    ws = ProjectWorkspace(tmp_path / "proj")
    manifest = ws.init_project(source_type=SourceType.JOURNAL)
    assert manifest.source_type == SourceType.JOURNAL
    assert ws.manifest_path.exists()
    assert ws.pages_raw_dir.is_dir()
    assert ws.pages_ocr_dir.is_dir()
    assert ws.pages_img_dir.is_dir()
    assert ws.cases_dir.is_dir()

    reloaded = ws.load_manifest()
    assert reloaded.project_dir == str(ws.project_dir)
    assert reloaded.source_type == SourceType.JOURNAL

    # Calling init_project again is idempotent and preserves existing source_type
    second = ws.init_project(source_type=SourceType.AUTO)
    assert second.source_type == SourceType.JOURNAL


def test_discover_pdfs_errors_and_no_100_item_limit(tmp_path: Path) -> None:
    missing = tmp_path / "does_not_exist"
    with pytest.raises(FileNotFoundError):
        ProjectWorkspace.discover_pdfs(missing)

    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    with pytest.raises(ValueError):
        ProjectWorkspace.discover_pdfs(empty_dir)

    non_pdf = tmp_path / "note.txt"
    non_pdf.write_text("hello", encoding="utf-8")
    with pytest.raises(ValueError):
        ProjectWorkspace.discover_pdfs(non_pdf)

    # Create 105 nested PDFs to verify no 100-item glob truncation
    batch_dir = tmp_path / "many_pdfs"
    for idx in range(105):
        sub = batch_dir / f"sub_{idx // 25}"
        sub.mkdir(parents=True, exist_ok=True)
        (sub / f"case_{idx:03d}.pdf").write_bytes(b"%PDF-1.4\n%%EOF\n")

    discovered = ProjectWorkspace.discover_pdfs(batch_dir)
    assert len(discovered) == 105


def test_register_pdfs_sha256_dedup_and_single_file(tmp_path: Path) -> None:
    pdf_dir = tmp_path / "inputs"
    pdf_a = _write_minimal_pdf(pdf_dir / "病例报告A.pdf", "Case A unique content")
    pdf_b = _write_minimal_pdf(pdf_dir / "nested" / "病例报告B.pdf", "Case B unique content")
    # Duplicate content under a different filename
    dup_b = pdf_dir / "copy_of_B.pdf"
    dup_b.write_bytes(pdf_b.read_bytes())

    ws = ProjectWorkspace(tmp_path / "workspace")
    manifest, added, skipped = ws.register_pdfs(
        pdf_dir,
        source_type=SourceType.JOURNAL,
        page_offset=0,
        target_article_query="猫病例",
    )
    assert len(added) == 2
    assert len(skipped) == 1
    assert set(manifest.documents.keys()) == set(added)
    for doc_id in added:
        rec = manifest.documents[doc_id]
        assert rec.source_type == SourceType.JOURNAL
        assert rec.target_article_query == "猫病例"
        assert rec.status == TaskStatus.PENDING

    # Re-registering the same file skips it as duplicate
    manifest2, added2, skipped2 = ws.register_pdfs(pdf_a, source_type=SourceType.JOURNAL)
    assert added2 == []
    assert len(skipped2) == 1
    assert len(manifest2.documents) == 2


def test_workspace_artifacts_index_and_case_records(tmp_path: Path) -> None:
    ws = ProjectWorkspace(tmp_path / "ws")
    ws.init_project(source_type=SourceType.BOOK)

    assert ws.load_index() is None
    assert ws.load_case_record("missing_case") is None
    assert ws.load_all_case_records() == []
    assert ws.load_page_artifacts("missing_doc") == []

    doc_id = "book_doc_1234"
    assert ws.page_raw_path(doc_id, 0).name == "p001.json"
    assert ws.page_ocr_path(doc_id, 2).name == "p003.json"
    assert ws.page_img_path(doc_id, 11).name == "p012.png"

    art1 = PageArtifact(document_id=doc_id, page_index=1, pdf_page_number=2, raw_text="page 2")
    art0 = PageArtifact(document_id=doc_id, page_index=0, pdf_page_number=1, raw_text="page 1")
    ws.save_page_artifact(art1)
    ws.save_page_artifact(art0)

    loaded_arts = ws.load_page_artifacts(doc_id)
    assert [a.page_index for a in loaded_arts] == [0, 1]

    seg = CaseSegment(
        case_id="ch1_c1",
        document_id=doc_id,
        source_filename="book.pdf",
        source_type=SourceType.BOOK,
        case_order=1,
        title="第一章 · 病例1 等级1",
    )
    index = IndexSummary(total_documents=1, total_cases=1, cases=[seg])
    ws.save_index(index)
    loaded_index = ws.load_index()
    assert loaded_index is not None
    assert loaded_index.total_cases == 1
    assert loaded_index.cases[0].case_id == "ch1_c1"

    case_rec = CaseRecord(
        document_id=doc_id,
        file_sha256="abc123",
        case_id="ch1_c1",
        case_order=1,
        source_type=SourceType.BOOK,
        title="第一章 · 病例1 等级1",
        diagnosis_raw="猫脂肪肝",
        diagnosis_normalized="猫脂肪肝",
        status=TaskStatus.COMPLETED,
    )
    ws.save_case_record(case_rec)
    loaded_case = ws.load_case_record("ch1_c1")
    assert loaded_case is not None
    assert loaded_case.diagnosis_normalized == "猫脂肪肝"
    assert len(ws.load_all_case_records()) == 1
