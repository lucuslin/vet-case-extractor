"""Tests for structural QA, semantic QA, project QA reporting, and gold evaluation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import write_sample_project
from vet_case_extractor.config import AppConfig
from vet_case_extractor.models import (
    CaseRecord,
    EvidenceSpan,
    LabTableRow,
    QAReport,
    ReviewStatus,
    SourceType,
)
from vet_case_extractor.validation.evaluation import (
    HUMAN_GOLD_TAG,
    SYNTHETIC_WARNING_TAG,
    evaluate_against_gold,
)
from vet_case_extractor.validation.qa import run_project_qa
from vet_case_extractor.validation.semantic import validate_record_semantic
from vet_case_extractor.validation.structural import (
    check_project_json_integrity,
    validate_structural,
)


def test_clean_journal_project_passes_qa(
    tmp_path: Path,
    sample_journal_records: list[CaseRecord],
    app_config: AppConfig,
) -> None:
    proj = write_sample_project(tmp_path / "proj", sample_journal_records, SourceType.JOURNAL)
    report = run_project_qa(proj, app_config)

    assert report.overall_status == "PASS"
    assert report.total_documents == 2
    assert report.total_cases == 2
    assert report.completed_cases == 2
    assert report.failed_cases == 0
    assert (proj / "qa" / "qa_report.json").exists()
    assert (proj / "qa" / "qa_report.md").exists()
    assert (proj / "qa" / "review_queue.json").exists()

    parsed = QAReport.model_validate_json((proj / "qa" / "qa_report.json").read_text(encoding="utf-8"))
    assert parsed.overall_status == "PASS"


def test_structural_truncated_json_and_rescue_part(
    tmp_path: Path,
    sample_journal_records: list[CaseRecord],
    app_config: AppConfig,
) -> None:
    proj = write_sample_project(tmp_path / "proj_bad_json", sample_journal_records, SourceType.JOURNAL)
    # Truncate one case record JSON and add a rescue _part file
    bad_file = proj / "cases" / "doc_a_case_01" / "record.json"
    bad_file.write_text('{"schema_version": "1.0.0", "case_id": "doc_a_', encoding="utf-8")
    (proj / "rows").mkdir(parents=True, exist_ok=True)
    (proj / "rows" / "rows_01_part.json").write_text("[]", encoding="utf-8")

    issues, _, _, _ = check_project_json_integrity(proj)
    rule_ids = {i.rule_id for i in issues}
    assert "STR_CORRUPTED_JSON" in rule_ids
    assert "STR_RESCUE_PART_FILE" in rule_ids

    report = run_project_qa(proj, app_config)
    assert report.overall_status == "FAIL"


def test_structural_duplicates_counts_book_continuity_and_level_mismatch(
    tmp_path: Path,
    sample_book_records: list[CaseRecord],
    app_config: AppConfig,
) -> None:
    # Create a book project where record has wrong level vs index, plus chapter continuity gap
    gap_rec = sample_book_records[0].model_copy(
        update={
            "case_id": "book_1_ch01_case03",
            "case_order": 3,
            "case_number": 3,
            "case_level": 13,
            "title": "第一章 肝酶升高 · 病例3 等级13",
        }
    )
    records = [sample_book_records[0], gap_rec]
    proj = write_sample_project(tmp_path / "proj_book", records, SourceType.BOOK)

    # Corrupt index level for gap_rec to 3 so level mismatch triggers vs record=13
    index_path = proj / "index.json"
    index_data = json.loads(index_path.read_text(encoding="utf-8"))
    index_data["cases"][1]["case_level"] = 3
    index_path.write_text(json.dumps(index_data, ensure_ascii=False), encoding="utf-8")

    report = run_project_qa(proj, app_config)
    rules = {i.rule_id for i in report.issues}
    assert "STR_CHAPTER_CONTINUITY" in rules
    assert "STR_LEVEL_MISMATCH" in rules
    assert "STR_SUSPICIOUS_CASE_LEVEL" in rules
    assert report.overall_status == "FAIL"

    # Duplicate case_id & article-vs-case count mismatch
    dup_rec = sample_book_records[0].model_copy()
    issues_dup = validate_structural([sample_book_records[0], dup_rec])
    assert any(i.rule_id == "STR_DUPLICATE_CASE_ID" for i in issues_dup)


def test_semantic_treatment_leak_pe_reasoning_external_care(
    sample_journal_records: list[CaseRecord],
    app_config: AppConfig,
) -> None:
    rec = sample_journal_records[0].model_copy(
        update={
            "medical_history": "外院怀疑为胰腺炎并曾口服头孢。主诉食欲废绝。",
            "medical_test": "体温39.5℃，心肺听诊有杂音；神经学检查本体感受减弱，病变定位于T3-L3脊髓；外院曾行超声检查。",
            "medical_treatment": "静脉注射头孢曲松钠每日1次；2周后复查好转，1个月后死亡。",
        }
    )
    issues = validate_record_semantic(rec, app_config.rules)
    rule_ids = {i.rule_id for i in issues}

    assert "SEM_TREATMENT_FOLLOWUP_LEAK" in rule_ids
    assert "SEM_PE_IN_MEDICAL_TEST" in rule_ids
    assert "SEM_REASONING_IN_FACT_FIELD" in rule_ids
    assert "SEM_EXTERNAL_CARE_MISPLACED" in rule_ids

    # Ensure Tier C in medical_history was NOT flagged as reasoning
    hist_reasoning = [
        i for i in issues if i.rule_id == "SEM_REASONING_IN_FACT_FIELD" and i.field_name == "medical_history"
    ]
    assert not hist_reasoning

    # Ensure specialty/neuro test alone is valid and NOT flagged as PE
    neuro_clean = sample_journal_records[0].model_copy(
        update={
            "medical_test": "神经学检查：右后肢本体感受减弱，共济失调；眼科检查眼压28 mmHg。",
        }
    )
    neuro_issues = validate_record_semantic(neuro_clean, app_config.rules)
    assert not any(i.rule_id == "SEM_PE_IN_MEDICAL_TEST" for i in neuro_issues)


def test_semantic_postmortem_vs_living_exploration(
    sample_journal_records: list[CaseRecord],
    app_config: AppConfig,
) -> None:
    bad_rec = sample_journal_records[0].model_copy(
        update={"medical_test": "血常规HCT 22%。安乐死后剖检见脾脏血管肉瘤破裂。"}
    )
    bad_issues = validate_record_semantic(bad_rec, app_config.rules)
    assert any(i.rule_id == "SEM_POSTMORTEM_IN_MEDICAL_TEST" for i in bad_issues)

    living_rec = sample_journal_records[0].model_copy(
        update={
            "medical_test": "开腹探查见回肠闭锁；截肢后离体组织剖检见多发囊腔。",
        }
    )
    living_issues = validate_record_semantic(living_rec, app_config.rules)
    assert not any(i.rule_id == "SEM_POSTMORTEM_IN_MEDICAL_TEST" for i in living_issues)


@pytest.mark.parametrize(
    ("dx", "expected_rules"),
    [
        ("美洲驼肝胆管疾病", set()),
        ("驴尿道梗阻", set()),
        ("雪貂胰岛细胞瘤", set()),
        ("犬颈椎C4~C5水合髓核脱出（HNPE）", set()),
        ("犬骨肉瘤（肱骨近端）；肺转移", set()),
        ("甲状腺机能亢进", {"SEM_DX_SPECIES_PREFIX"}),
        ("犬 骨肉瘤", {"SEM_DX_SPACE_AFTER_SPECIES"}),
        ("犬骨肉瘤，肺转移", {"SEM_DX_SEPARATOR"}),
        ("犬骨肉瘤；犬肺转移", {"SEM_DX_DUPLICATE_SPECIES"}),
        ("马产后败血症；厌食", {"SEM_DX_NON_DIAGNOSIS_SYMPTOM"}),
        (
            "犬创伤后肝细胞损伤（ALT/AST一过性升高，非原发性肝胆疾病）",
            {"SEM_DX_SUSPICIOUS_PARENTHESES"},
        ),
        ("犬HNPE（颈椎水合髓核脱出）", {"SEM_DX_ABBREVIATION_ORDER"}),
    ],
)
def test_semantic_dx_five_rules(
    dx: str,
    expected_rules: set[str],
    sample_journal_records: list[CaseRecord],
    app_config: AppConfig,
) -> None:
    rec = sample_journal_records[0].model_copy(update={"diagnosis_normalized": dx})
    issues = validate_record_semantic(rec, app_config.rules)
    dx_rules = {i.rule_id for i in issues if i.rule_id.startswith("SEM_DX_")}
    assert dx_rules == expected_rules


def test_semantic_placeholders_anomalies_ungrounded_and_crosstalk(
    sample_journal_records: list[CaseRecord],
    sample_lab_row: LabTableRow,
    app_config: AppConfig,
) -> None:
    # Exact placeholder match triggers, non-exact "无明显异常" does NOT trigger
    for ph in ("无", "未提及", "N/A", "—"):
        rec_ph = sample_journal_records[0].model_copy(update={"follow_up": ph})
        issues_ph = validate_record_semantic(rec_ph, app_config.rules)
        assert any(i.rule_id == "SEM_FORBIDDEN_PLACEHOLDER" for i in issues_ph)

    rec_ok = sample_journal_records[0].model_copy(update={"medical_test": "心肺听诊未见异常，无明显异常"})
    issues_ok = validate_record_semantic(rec_ok, app_config.rules)
    assert not any(i.rule_id == "SEM_FORBIDDEN_PLACEHOLDER" for i in issues_ok)

    # LabTableRow anomaly + UNGROUNDED + crosstalk
    rec_complex = sample_journal_records[0].model_copy(
        update={
            "title": "犬输尿管囊肿诊治病例",
            "medical_history": "患猫，6岁，红细胞计数显著升高，红细胞压积72%。",
            "medical_test": "骨髓检查示红系增生，GCT 85 U/L。",
            "diagnosis_normalized": "猫真性红细胞增多症",
            "lab_rows": [sample_lab_row],
            "review_status": ReviewStatus.UNGROUNDED,
            "evidence_spans": [
                EvidenceSpan(
                    document_id="doc_a",
                    file_sha256="a" * 64,
                    case_id="doc_a_case_01",
                    field_name="medical_treatment",
                    page_index=None,
                    quote="放血治疗",
                    match_score=0.0,
                    extraction_method="test",
                    review_status=ReviewStatus.UNGROUNDED,
                )
            ],
        }
    )
    issues = validate_record_semantic(rec_complex, app_config.rules)
    rule_ids = {i.rule_id for i in issues}
    assert "OCR_LAB_ANOMALY" in rule_ids
    assert "OCR_KNOWN_TYPO_CANDIDATE" in rule_ids
    assert "PROV_UNGROUNDED_FIELD" in rule_ids
    assert "SEM_TITLE_CONTENT_CROSSTALK" in rule_ids


def test_evaluate_against_gold_synthetic_tag_and_metrics(
    sample_journal_records: list[CaseRecord],
) -> None:
    gold = sample_journal_records
    pred = [
        sample_journal_records[0],
        sample_journal_records[1].model_copy(update={"diagnosis_normalized": "犬骨肉瘤"}),
    ]

    report_syn = evaluate_against_gold(pred, gold, dataset_is_human_annotated=False)
    assert report_syn.metric_tag == SYNTHETIC_WARNING_TAG
    assert report_syn.clinical_accuracy_claim_allowed is False
    assert SYNTHETIC_WARNING_TAG in report_syn.disclaimer
    assert report_syn.per_field["title"].exact_match_rate == 1.0
    assert report_syn.per_field["Dx"].exact_match_rate == 0.5
    assert 0.0 < report_syn.per_field["Dx"].f1 < 1.0

    with pytest.raises(ValueError, match=SYNTHETIC_WARNING_TAG):
        evaluate_against_gold(pred, gold, dataset_is_human_annotated=False, refuse_unannotated=True)

    report_human = evaluate_against_gold(gold, gold, dataset_is_human_annotated=True)
    assert report_human.metric_tag == HUMAN_GOLD_TAG
    assert report_human.clinical_accuracy_claim_allowed is True
    assert report_human.macro_exact_match == 1.0
    assert report_human.macro_f1 == 1.0
