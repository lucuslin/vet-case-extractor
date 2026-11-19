"""Tests for provenance grounding and ungrounded/no-fabrication enforcement."""

from __future__ import annotations

from vet_case_extractor.models import (
    BBox,
    CaseRecord,
    PageArtifact,
    ReviewStatus,
    SourceType,
    TaskStatus,
    TextBlock,
)
from vet_case_extractor.provenance.tracker import ground_case_provenance


def _sample_pages() -> list[PageArtifact]:
    return [
        PageArtifact(
            document_id="doc_1",
            page_index=0,
            pdf_page_number=1,
            raw_text="异国短毛猫，11岁，雄性。眼科检查左眼眼压17mmHg。",
            cleaned_text="异国短毛猫，11岁，雄性。眼科检查左眼眼压17mmHg。",
            blocks=[
                TextBlock(
                    block_id="p0_b0",
                    text="异国短毛猫，11岁，雄性。",
                    bbox=BBox(x0=50, y0=100, x1=300, y1=120),
                ),
                TextBlock(
                    block_id="p0_b1",
                    text="眼科检查左眼眼压17mmHg。",
                    bbox=BBox(x0=50, y0=130, x1=320, y1=150),
                ),
            ],
        ),
        PageArtifact(
            document_id="doc_1",
            page_index=1,
            pdf_page_number=2,
            raw_text="行右眼球摘除术。病理诊断为弥散性虹膜黑色素瘤。术后6个月回访良好。",
            cleaned_text="行右眼球摘除术。病理诊断为弥散性虹膜黑色素瘤。术后6个月回访良好。",
            blocks=[
                TextBlock(
                    block_id="p1_b0",
                    text="行右眼球摘除术。",
                    bbox=BBox(x0=40, y0=80, x1=200, y1=100),
                ),
                TextBlock(
                    block_id="p1_b1",
                    text="病理诊断为弥散性虹膜黑色素瘤。",
                    bbox=BBox(x0=40, y0=110, x1=310, y1=130),
                ),
                TextBlock(
                    block_id="p1_b2",
                    text="术后6个月回访良好。",
                    bbox=BBox(x0=40, y0=140, x1=260, y1=160),
                ),
            ],
        ),
    ]


def test_provenance_grounds_exact_and_normalized_spans_with_bboxes() -> None:
    pages = _sample_pages()
    record = CaseRecord(
        document_id="doc_1",
        file_sha256="deadbeef" * 8,
        case_id="doc_1__case_001",
        source_type=SourceType.JOURNAL,
        title="猫弥散性虹膜黑色素瘤的病例报告",
        medical_history_raw="异国短毛猫，11岁，雄性。",
        medical_history="异国短毛猫，11岁，雄性。",
        medical_test_raw="眼科检查左眼眼压17mmHg。",
        medical_test="眼科检查左眼眼压17mmHg。",
        medical_treatment_raw="行右眼球摘除术。",
        medical_treatment="行右眼球摘除术。",
        diagnosis_raw="猫弥散性虹膜黑色素瘤",
        diagnosis_normalized="猫弥散性虹膜黑色素瘤",
        follow_up_raw="术后6个月回访良好。",
        follow_up="术后6个月回访良好。",
        status=TaskStatus.COMPLETED,
    )

    grounded = ground_case_provenance(
        record=record,
        pages=pages,
        extraction_method="gemini",
        model_name="gemini-2.5-flash",
        rule_version="1.0.0",
    )

    assert grounded.review_status == ReviewStatus.OK
    assert grounded.status == TaskStatus.COMPLETED
    assert len(grounded.evidence_spans) == 5

    by_field = {sp.field_name: sp for sp in grounded.evidence_spans}
    assert by_field["medical_history"].page_index == 0
    assert by_field["medical_history"].bbox == BBox(x0=50, y0=100, x1=300, y1=120)
    assert by_field["medical_history"].match_score == 1.0
    assert by_field["medical_history"].file_sha256 == "deadbeef" * 8

    assert by_field["medical_test"].page_index == 0
    assert by_field["medical_treatment"].page_index == 1
    assert by_field["diagnosis"].page_index == 1
    assert by_field["follow_up"].page_index == 1


def test_provenance_flags_ungrounded_without_fabricating_bbox_or_page() -> None:
    pages = _sample_pages()
    record = CaseRecord(
        document_id="doc_1",
        file_sha256="deadbeef" * 8,
        case_id="doc_1__case_001",
        source_type=SourceType.JOURNAL,
        title="猫弥散性虹膜黑色素瘤的病例报告",
        medical_history_raw="异国短毛猫，11岁，雄性。",
        medical_history="异国短毛猫，11岁，雄性。",
        medical_treatment_raw="每日皮下注射胰岛素5IU（完全编造的治疗）",
        medical_treatment="每日皮下注射胰岛素5IU（完全编造的治疗）",
        diagnosis_raw="猫弥散性虹膜黑色素瘤",
        diagnosis_normalized="猫弥散性虹膜黑色素瘤",
        status=TaskStatus.COMPLETED,
    )

    grounded = ground_case_provenance(
        record=record,
        pages=pages,
        extraction_method="gemini",
        model_name="gemini-2.5-flash",
        rule_version="1.0.0",
        evidence_quotes={
            "medical_treatment": ["每日皮下注射胰岛素5IU（完全编造的治疗）"],
        },
    )

    assert grounded.review_status == ReviewStatus.UNGROUNDED
    assert grounded.status == TaskStatus.REVIEW_REQUIRED
    assert any("medical_treatment" in r for r in grounded.review_reasons)

    by_field = {sp.field_name: sp for sp in grounded.evidence_spans}
    tx_span = by_field["medical_treatment"]
    assert tx_span.review_status == ReviewStatus.UNGROUNDED
    assert tx_span.page_index is None
    assert tx_span.bbox is None
    assert tx_span.match_score == 0.0


def test_provenance_empty_fields_stay_ok_and_multi_block_span_computes_union_bbox() -> None:
    pages = [
        PageArtifact(
            document_id="doc_2",
            page_index=0,
            pdf_page_number=1,
            raw_text="病例1，英国短毛猫，4岁，\n体重4.0kg，精神沉郁。",
            cleaned_text="病例1，英国短毛猫，4岁，\n体重4.0kg，精神沉郁。",
            blocks=[
                TextBlock(
                    block_id="b1",
                    text="病例1，英国短毛猫，4岁，",
                    bbox=BBox(x0=10, y0=10, x1=200, y1=30),
                ),
                TextBlock(
                    block_id="b2",
                    text="体重4.0kg，精神沉郁。",
                    bbox=BBox(x0=10, y0=35, x1=210, y1=55),
                ),
            ],
        )
    ]
    record = CaseRecord(
        document_id="doc_2",
        file_sha256="1234" * 16,
        case_id="doc_2__case_001",
        source_type=SourceType.BOOK,
        medical_history_raw="英国短毛猫，4岁，体重4.0kg，精神沉郁。",
        medical_history="英国短毛猫，4岁，体重4.0kg，精神沉郁。",
        medical_treatment="",  # legitimately empty
        follow_up="",          # legitimately empty
        status=TaskStatus.COMPLETED,
    )

    grounded = ground_case_provenance(
        record=record,
        pages=pages,
        extraction_method="agent_interactive",
        model_name="test-agent",
        rule_version="1.0.0",
    )
    assert grounded.review_status == ReviewStatus.OK
    assert len(grounded.evidence_spans) == 1
    sp = grounded.evidence_spans[0]
    assert sp.page_index == 0
    assert sp.bbox == BBox(x0=10, y0=10, x1=210, y1=55)
