"""Unified Pydantic v2 schemas for vet-case-extractor."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field

SCHEMA_VERSION = "1.0.0"


def utc_now_iso() -> str:
    """Return current UTC timestamp in ISO-8601 format."""
    return datetime.now(timezone.utc).isoformat()


class SourceType(str, Enum):
    AUTO = "auto"
    JOURNAL = "journal"
    BOOK = "book"


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    REVIEW_REQUIRED = "review_required"
    FAILED = "failed"


class PageExtractionEngine(str, Enum):
    PYMUPDF = "pymupdf"
    PDFPLUMBER = "pdfplumber"
    RAPIDOCR = "rapidocr"
    FAILED = "failed"


class ReviewStatus(str, Enum):
    OK = "ok"
    REVIEW_REQUIRED = "review_required"
    UNGROUNDED = "ungrounded"
    OCR_SUSPECT = "ocr_suspect"
    FAILED = "failed"


class Severity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class OCRPolicy(str, Enum):
    AUTO = "auto"
    FORCE_IMAGE_PAGES = "force_image_pages"
    FORCE_ALL = "force_all"
    NEVER = "never"


class BBox(BaseModel):
    """Axis-aligned bounding box in page pixel or point coordinates."""

    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def width(self) -> float:
        return max(0.0, self.x1 - self.x0)

    @property
    def height(self) -> float:
        return max(0.0, self.y1 - self.y0)

    @property
    def x_mid(self) -> float:
        return (self.x0 + self.x1) / 2.0


class TextBlock(BaseModel):
    """Single extracted text block with coordinates and layout role."""

    block_id: str
    text: str
    bbox: BBox
    column: Literal["left", "right", "full", "header", "footer"] = "full"
    confidence: float | None = None
    source: Literal["native", "ocr"] = "native"


class LabTableRow(BaseModel):
    """Structured laboratory test row preserving raw OCR/native strings without auto-mutation."""

    item_raw: str
    value_raw: str = ""
    unit_raw: str = ""
    ref_range_raw: str = ""
    flag_raw: str = ""
    page_index: int
    bbox: BBox | None = None
    table_title: str = ""
    anomalies: list[str] = Field(default_factory=list)


class PageQualityMetrics(BaseModel):
    """Text-layer quality metrics used to decide whether OCR is required."""

    char_count: int = 0
    cjk_count: int = 0
    cjk_ratio: float = 0.0
    pua_count: int = 0
    founder_pua_count: int = 0
    replacement_char_count: int = 0
    garbled_ratio: float = 0.0
    image_count: int = 0
    full_page_image: bool = False
    needs_ocr: bool = False
    ocr_reason: str = ""


class PageArtifact(BaseModel):
    """Extracted text, layout, quality, and image metadata for one PDF page."""

    document_id: str
    page_index: int
    pdf_page_number: int
    book_page_number: int | None = None
    width: float = 0.0
    height: float = 0.0
    engine: PageExtractionEngine = PageExtractionEngine.PYMUPDF
    quality: PageQualityMetrics = Field(default_factory=PageQualityMetrics)
    raw_text: str = ""
    cleaned_text: str = ""
    layout_text: str = ""
    blocks: list[TextBlock] = Field(default_factory=list)
    lab_rows: list[LabTableRow] = Field(default_factory=list)
    image_path: str | None = None
    ocr_cached: bool = False
    error: str | None = None


class DocumentRecord(BaseModel):
    """Ingestion and processing state for a single source PDF."""

    document_id: str
    filename: str
    file_path: str
    file_sha256: str
    source_type: SourceType = SourceType.AUTO
    page_count: int = 0
    page_offset: int = 0
    target_article_query: str | None = None
    status: TaskStatus = TaskStatus.PENDING
    pages_needing_ocr: list[int] = Field(default_factory=list)
    pages_failed: list[int] = Field(default_factory=list)
    error: str | None = None
    ingested_at: str = Field(default_factory=utc_now_iso)
    updated_at: str = Field(default_factory=utc_now_iso)


class CaseSegment(BaseModel):
    """Segmented case boundary linking document pages to a concrete extraction unit."""

    case_id: str
    document_id: str
    source_filename: str
    source_type: SourceType
    case_order: int = 1
    source_journal: str = ""
    publication_year: str = ""
    received_year: str = ""
    article_title: str = ""
    chapter_number: int | None = None
    chapter_title: str = ""
    case_number: int | None = None
    case_level: int | None = None
    case_level_raw: str = ""
    title: str = ""
    page_indices: list[int] = Field(default_factory=list)
    page_range: str = ""
    book_page_range: str = ""
    image_paths: list[str] = Field(default_factory=list)
    raw_text: str = ""
    packet_path: str = ""
    lab_rows: list[LabTableRow] = Field(default_factory=list)
    boundary_uncertain: bool = False
    boundary_notes: list[str] = Field(default_factory=list)


class IndexSummary(BaseModel):
    """Project-level index produced by the segmentation stage."""

    schema_version: str = SCHEMA_VERSION
    generated_at: str = Field(default_factory=utc_now_iso)
    total_documents: int = 0
    total_cases: int = 0
    cases: list[CaseSegment] = Field(default_factory=list)
    chapter_continuity_issues: list[str] = Field(default_factory=list)
    duplicate_case_ids: list[str] = Field(default_factory=list)


class EvidenceSpan(BaseModel):
    """Source grounding pointer linking an extracted field to page text and bounding box."""

    document_id: str
    file_sha256: str
    case_id: str
    field_name: str
    page_index: int | None = None
    quote: str = ""
    bbox: BBox | None = None
    match_score: float = 0.0
    extraction_method: str = ""
    model_name: str = ""
    rule_version: str = ""
    review_status: ReviewStatus = ReviewStatus.OK
    notes: str = ""


class NormalizationChange(BaseModel):
    """Audit log entry recording a deterministic normalization change from raw to normalized."""

    field_name: str
    rule_id: str
    before: str
    after: str
    reason: str


class LLMExtractedPayload(BaseModel):
    """Strict schema returned by LLM providers or interactive Agent extraction."""

    case_id: str = ""
    source: str = ""
    publication_year: str = ""
    received_year: str = ""
    title: str = ""
    medical_history: str = ""
    medical_test: str = ""
    medical_treatment: str = ""
    diagnosis_raw: str = ""
    follow_up: str = ""
    notes: str = ""
    evidence_quotes: dict[str, list[str]] = Field(default_factory=dict)
    uncertainties: list[str] = Field(default_factory=list)


class CaseRecord(BaseModel):
    """Unified internal case record holding both raw and normalized layers plus provenance."""

    schema_version: str = SCHEMA_VERSION
    document_id: str
    file_sha256: str
    case_id: str
    case_order: int = 1
    source_type: SourceType
    source_title: str = ""
    publication_year: str = ""
    received_year: str = ""
    page_range: str = ""
    book_page_range: str = ""
    chapter_number: int | None = None
    chapter_title: str = ""
    case_number: int | None = None
    case_level: int | None = None
    title: str = ""

    # Raw verbatim layer
    medical_history_raw: str = ""
    medical_test_raw: str = ""
    medical_treatment_raw: str = ""
    diagnosis_raw: str = ""
    follow_up_raw: str = ""
    notes_raw: str = ""

    # Normalized layer
    medical_history: str = ""
    medical_test: str = ""
    medical_treatment: str = ""
    diagnosis_normalized: str = ""
    follow_up: str = ""
    notes: str = ""

    # Provenance & workflow metadata
    evidence_spans: list[EvidenceSpan] = Field(default_factory=list)
    normalization_changes: list[NormalizationChange] = Field(default_factory=list)
    lab_rows: list[LabTableRow] = Field(default_factory=list)
    status: TaskStatus = TaskStatus.PENDING
    review_status: ReviewStatus = ReviewStatus.OK
    review_reasons: list[str] = Field(default_factory=list)
    extraction_method: str = ""
    model_name: str = ""
    rule_version: str = ""
    token_usage: dict[str, int] = Field(default_factory=dict)
    error: str | None = None
    updated_at: str = Field(default_factory=utc_now_iso)

    def formatted_year(self) -> str:
        """Format publication/received year following legacy rule: `2008（2007）`."""
        pub = self.publication_year.strip()
        rec = self.received_year.strip()
        if pub and rec and pub != rec:
            return f"{pub}（{rec}）"
        return pub or rec


class QAIssue(BaseModel):
    """Single structural, semantic, OCR, or provenance issue detected by QA."""

    issue_id: str
    case_id: str | None = None
    document_id: str | None = None
    page_indices: list[int] = Field(default_factory=list)
    category: Literal["structural", "semantic", "ocr", "provenance"]
    rule_id: str
    severity: Severity
    field_name: str | None = None
    message: str
    suggestion: str = ""
    auto_fixable: bool = False


class QAReport(BaseModel):
    """Project-wide quality assurance report."""

    schema_version: str = SCHEMA_VERSION
    generated_at: str = Field(default_factory=utc_now_iso)
    project_dir: str
    total_documents: int = 0
    total_cases: int = 0
    completed_cases: int = 0
    review_required_cases: int = 0
    failed_cases: int = 0
    pending_cases: int = 0
    empty_field_counts: dict[str, int] = Field(default_factory=dict)
    issue_counts_by_severity: dict[str, int] = Field(default_factory=dict)
    issue_counts_by_rule: dict[str, int] = Field(default_factory=dict)
    issues: list[QAIssue] = Field(default_factory=list)
    overall_status: Literal["PASS", "PASS_WITH_WARNINGS", "REVIEW_REQUIRED", "FAIL"] = "PASS"


class ProjectManifest(BaseModel):
    """Top-level persistent state manifest for a vet-case-extractor project workspace."""

    schema_version: str = SCHEMA_VERSION
    project_dir: str
    source_type: SourceType = SourceType.AUTO
    created_at: str = Field(default_factory=utc_now_iso)
    updated_at: str = Field(default_factory=utc_now_iso)
    documents: dict[str, DocumentRecord] = Field(default_factory=dict)
    case_statuses: dict[str, TaskStatus] = Field(default_factory=dict)
