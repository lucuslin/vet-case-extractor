"""Project workspace state management, PDF discovery, and atomic artifact I/O."""

from __future__ import annotations

from pathlib import Path

from vet_case_extractor.models import (
    CaseRecord,
    DocumentRecord,
    IndexSummary,
    PageArtifact,
    ProjectManifest,
    SourceType,
    TaskStatus,
    utc_now_iso,
)
from vet_case_extractor.utils.io import (
    atomic_write_json,
    compute_sha256,
    read_model,
    safe_filename,
)


class ProjectWorkspace:
    """Manages persistent directory layout, manifest, page artifacts, index, and case records."""

    def __init__(self, project_dir: Path | str) -> None:
        self.project_dir = Path(project_dir).resolve()
        self.manifest_path = self.project_dir / "manifest.json"
        self.index_path = self.project_dir / "index.json"
        self.pages_raw_dir = self.project_dir / "pages_raw"
        self.pages_ocr_dir = self.project_dir / "pages_ocr"
        self.pages_img_dir = self.project_dir / "pages_img"
        self.cases_dir = self.project_dir / "cases"
        self.qa_dir = self.project_dir / "qa"
        self.export_dir = self.project_dir / "export"
        self.logs_dir = self.project_dir / "logs"

    def init_project(self, source_type: SourceType = SourceType.AUTO) -> ProjectManifest:
        """Create workspace directories and initialize or load the project manifest."""
        for directory in (
            self.project_dir,
            self.pages_raw_dir,
            self.pages_ocr_dir,
            self.pages_img_dir,
            self.cases_dir,
            self.qa_dir,
            self.export_dir,
            self.logs_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)

        if self.manifest_path.exists():
            manifest = self.load_manifest()
            if source_type != SourceType.AUTO and manifest.source_type == SourceType.AUTO:
                manifest.source_type = source_type
                self.save_manifest(manifest)
            return manifest

        manifest = ProjectManifest(
            project_dir=str(self.project_dir),
            source_type=source_type,
        )
        self.save_manifest(manifest)
        return manifest

    def load_manifest(self) -> ProjectManifest:
        """Load and validate the workspace project manifest."""
        if not self.manifest_path.exists():
            raise FileNotFoundError(f"Project manifest not found: {self.manifest_path}")
        return read_model(self.manifest_path, ProjectManifest)

    def save_manifest(self, manifest: ProjectManifest) -> Path:
        """Atomically persist the project manifest with updated timestamp."""
        manifest.updated_at = utc_now_iso()
        return atomic_write_json(self.manifest_path, manifest)

    @staticmethod
    def discover_pdfs(input_path: Path | str) -> list[Path]:
        """Recursively discover PDF files without any 100-item truncation limit."""
        target = Path(input_path)
        if not target.exists():
            raise FileNotFoundError(f"Input path does not exist: {target}")

        if target.is_file():
            if target.suffix.lower() != ".pdf":
                raise ValueError(f"Input file is not a PDF: {target}")
            return [target.resolve()]

        if not target.is_dir():
            raise ValueError(f"Input path is neither a file nor directory: {target}")

        seen: set[Path] = set()
        pdfs: list[Path] = []
        for pattern in ("*.pdf", "*.PDF"):
            for candidate in target.rglob(pattern):
                if candidate.is_file():
                    resolved = candidate.resolve()
                    if resolved not in seen:
                        seen.add(resolved)
                        pdfs.append(resolved)

        pdfs.sort(key=lambda p: str(p).lower())
        if not pdfs:
            raise ValueError(f"No PDF files found under directory: {target}")
        return pdfs

    def register_pdfs(
        self,
        input_path: Path | str,
        source_type: SourceType = SourceType.AUTO,
        page_offset: int = 0,
        target_article_query: str | None = None,
    ) -> tuple[ProjectManifest, list[str], list[str]]:
        """Register discovered PDFs into the manifest, skipping SHA-256 duplicates."""
        manifest = (
            self.load_manifest()
            if self.manifest_path.exists()
            else self.init_project(source_type=source_type)
        )

        pdf_paths = self.discover_pdfs(input_path)
        sha_to_doc_id = {doc.file_sha256: doc_id for doc_id, doc in manifest.documents.items()}

        added_doc_ids: list[str] = []
        skipped_duplicate_doc_ids: list[str] = []
        effective_source_type = (
            source_type if source_type != SourceType.AUTO else manifest.source_type
        )

        for pdf_path in pdf_paths:
            sha = compute_sha256(pdf_path)
            if sha in sha_to_doc_id:
                skipped_duplicate_doc_ids.append(sha_to_doc_id[sha])
                continue

            base_id = f"{safe_filename(pdf_path.stem, max_length=48)}_{sha[:12]}"
            doc_id = base_id
            suffix_idx = 2
            while doc_id in manifest.documents:
                doc_id = f"{base_id}_{suffix_idx}"
                suffix_idx += 1

            record = DocumentRecord(
                document_id=doc_id,
                filename=pdf_path.name,
                file_path=str(pdf_path),
                file_sha256=sha,
                source_type=effective_source_type,
                page_offset=page_offset,
                target_article_query=target_article_query,
                status=TaskStatus.PENDING,
            )
            manifest.documents[doc_id] = record
            sha_to_doc_id[sha] = doc_id
            added_doc_ids.append(doc_id)

        self.save_manifest(manifest)
        return manifest, added_doc_ids, skipped_duplicate_doc_ids

    def page_raw_path(self, doc_id: str, page_idx: int) -> Path:
        """Return path for a page's persisted PageArtifact JSON."""
        if page_idx < 0:
            raise ValueError(f"page_idx must be non-negative, got {page_idx}")
        return self.pages_raw_dir / safe_filename(doc_id) / f"p{page_idx + 1:03d}.json"

    def page_ocr_path(self, doc_id: str, page_idx: int) -> Path:
        """Return path for a page's cached raw RapidOCR JSON."""
        if page_idx < 0:
            raise ValueError(f"page_idx must be non-negative, got {page_idx}")
        return self.pages_ocr_dir / safe_filename(doc_id) / f"p{page_idx + 1:03d}.json"

    def page_img_path(self, doc_id: str, page_idx: int) -> Path:
        """Return path for a page's rendered PNG image."""
        if page_idx < 0:
            raise ValueError(f"page_idx must be non-negative, got {page_idx}")
        return self.pages_img_dir / safe_filename(doc_id) / f"p{page_idx + 1:03d}.png"

    def case_dir(self, case_id: str) -> Path:
        """Return directory for a specific case's packet and record."""
        return self.cases_dir / safe_filename(case_id)

    def save_page_artifact(self, artifact: PageArtifact) -> Path:
        """Atomically save a PageArtifact JSON to pages_raw/<doc_id>/pNNN.json."""
        path = self.page_raw_path(artifact.document_id, artifact.page_index)
        return atomic_write_json(path, artifact)

    def load_page_artifacts(self, doc_id: str) -> list[PageArtifact]:
        """Load all PageArtifacts for a document sorted by 0-based page_index."""
        doc_dir = self.pages_raw_dir / safe_filename(doc_id)
        if not doc_dir.is_dir():
            return []
        artifacts = [read_model(path, PageArtifact) for path in sorted(doc_dir.glob("*.json"))]
        artifacts.sort(key=lambda item: item.page_index)
        return artifacts

    def save_index(self, index: IndexSummary) -> Path:
        """Atomically persist the project-level IndexSummary."""
        return atomic_write_json(self.index_path, index)

    def load_index(self) -> IndexSummary | None:
        """Load the project-level IndexSummary if it exists."""
        if not self.index_path.exists():
            return None
        return read_model(self.index_path, IndexSummary)

    def save_case_record(self, record: CaseRecord) -> Path:
        """Atomically persist a CaseRecord to cases/<case_id>/record.json."""
        record.updated_at = utc_now_iso()
        path = self.case_dir(record.case_id) / "record.json"
        return atomic_write_json(path, record)

    def load_case_record(self, case_id: str) -> CaseRecord | None:
        """Load a single CaseRecord by case_id if present."""
        path = self.case_dir(case_id) / "record.json"
        if not path.exists():
            return None
        return read_model(path, CaseRecord)

    def load_all_case_records(self) -> list[CaseRecord]:
        """Load all persisted CaseRecords sorted deterministically."""
        if not self.cases_dir.is_dir():
            return []
        records = [
            read_model(path, CaseRecord)
            for path in sorted(self.cases_dir.glob("*/record.json"))
        ]
        records.sort(key=lambda rec: (rec.document_id, rec.case_order, rec.case_id))
        return records
