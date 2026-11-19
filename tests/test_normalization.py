"""Tests for Dx 5 rules, Rx standardization, Tier C retention, reasoning separation, and field normalization."""

from __future__ import annotations

from vet_case_extractor.config import load_config
from vet_case_extractor.models import (
    CaseSegment,
    LabTableRow,
    LLMExtractedPayload,
    ReviewStatus,
    SourceType,
    TaskStatus,
)
from vet_case_extractor.normalization.dx import normalize_dx
from vet_case_extractor.normalization.fields import normalize_case_fields
from vet_case_extractor.normalization.rx import normalize_rx


def test_normalize_dx_all_five_rules_and_four_paren_categories() -> None:
    rules = load_config().rules

    # Rule 1 + Category 1: anatomical qualifier retained, no space after species
    dx1, notes1, _ = normalize_dx("犬 骨肉瘤（肱骨近端）", rules)
    assert dx1 == "犬骨肉瘤（肱骨近端）"
    assert notes1 == ""

    # Category 2: explanatory / lab notes moved to moved_notes
    dx2, notes2, _ = normalize_dx(
        "犬 创伤后肝细胞损伤（ALT/AST 一过性升高；幼龄大型犬 ALP 轻度升高为骨骼生长期正常；非原发性肝胆疾病）",
        rules,
    )
    assert dx2 == "犬创伤后肝细胞损伤"
    assert "ALT/AST 一过性升高" in notes2

    # Category 3: canonical name replaces colloquial outer term
    dx3, notes3, _ = normalize_dx("犬 皮质类固醇性肝病（医源性肾上腺皮质机能亢进）", rules)
    assert dx3 == "犬医源性肾上腺皮质机能亢进"
    assert notes3 == ""

    # Category 4: secondary diagnosis split with Chinese semicolon and non-diagnosis symptoms dropped
    dx4, _, _ = normalize_dx("马 产后败血症/子宫炎（继发肌肉损伤、厌食、应激）", rules)
    assert dx4 == "马产后败血症/子宫炎；继发肌肉损伤"

    # Rule 5: abbreviation after full name (and swap reversed order)
    dx5a, _, _ = normalize_dx("犬 颈椎C4~C5水合髓核脱出 (HNPE)", rules)
    assert dx5a == "犬颈椎C4~C5水合髓核脱出（HNPE）"
    dx5b, _, _ = normalize_dx("犬 HNPE（颈椎C4~C5水合髓核脱出）", rules)
    assert dx5b == "犬颈椎C4~C5水合髓核脱出（HNPE）"

    # Rule 1 + Rule 3: repeated species stripped on subsequent clauses, comma converted to `；`
    dx6, _, _ = normalize_dx("犬 血支原体感染，犬 免疫介导性溶血性贫血，脱水", rules)
    assert dx6 == "犬血支原体感染；免疫介导性溶血性贫血"


def test_normalize_dx_extended_species_prefixes() -> None:
    rules = load_config().rules
    assert len(rules.species_prefixes) >= 46
    for raw, expected in [
        ("美洲驼 肝胆管疾病", "美洲驼肝胆管疾病"),
        ("驴 尿道梗阻", "驴尿道梗阻"),
        ("大熊猫 急性胃肠炎", "大熊猫急性胃肠炎"),
        ("雪貂 胰岛素瘤", "雪貂胰岛素瘤"),
        ("蜥蜴 代谢性骨病", "蜥蜴代谢性骨病"),
        ("豚鼠 维生素C缺乏症", "豚鼠维生素C缺乏症"),
    ]:
        norm, _, _ = normalize_dx(raw, rules)
        assert norm == expected


def test_normalize_rx_routes_frequencies_and_guards() -> None:
    rules = load_config().rules

    rx1, changes1 = normalize_rx(
        "阿莫西林克拉维酸钾口服，每日2次；美洛昔康皮下注射，1次/d；生理盐水静脉滴注，每天3次；头孢曲松肌肉注射，4次/天。",
        rules,
    )
    assert "po" in rx1
    assert "bid" in rx1
    assert "sc" in rx1
    assert "sid" in rx1
    assert "iv" in rx1
    assert "tid" in rx1
    assert "im" in rx1
    assert "qid" in rx1
    assert len(changes1) >= 6

    # Guard: anatomical "皮下肿块" and Roman numeral "IV级" must NOT be corrupted
    rx2, _ = normalize_rx("切除皮下肿块，评估肿瘤IV级，术后口服泼尼松 PO BID。", rules)
    assert "皮下肿块" in rx2
    assert "IV级" in rx2
    assert "po bid" in rx2


def test_normalize_case_fields_tier_c_reasoning_mixed_postmortem_and_placeholders() -> None:
    rules = load_config().rules
    segment = CaseSegment(
        case_id="doc1__case_001",
        document_id="doc1",
        source_filename="test.pdf",
        source_type=SourceType.JOURNAL,
        source_journal="中国兽医杂志",
        publication_year="2023",
        received_year="2022",
        title="猫病例报告",
        lab_rows=[
            LabTableRow(
                item_raw="WBC",
                value_raw="13.5",
                page_index=0,
                anomalies=["unit_conflict"],
            )
        ],
    )
    payload = LLMExtractedPayload(
        case_id="doc1__case_001",
        medical_history=(
            "布偶猫，2岁，雄性。外院怀疑为口鼻瘘。主人怀疑被咬伤。"
            "既往曾诊断为胰腺炎并服药。"
        ),
        medical_test=(
            "体格检查：体温38.5℃，心率160次/min。"
            "提示再生性贫血。病变定位于T3-L3脊髓节段。"
            "X线检查见左肺野致密阴影，提示肺水肿。"
            "开腹探查见回肠闭锁。"
            "安乐死后剖检见脾脏血管肉瘤破裂。"
        ),
        medical_treatment="阿莫西林口服，每日2次。",
        diagnosis_raw="猫 皮质类固醇性肝病（医源性肾上腺皮质机能亢进）",
        follow_up="未提及",
        notes="",
    )

    rec = normalize_case_fields(
        payload=payload,
        segment=segment,
        rules=rules,
        method="gemini",
        model_name="gemini-2.5-flash",
        rule_version=rules.rule_version,
        file_sha256="f" * 64,
    )

    # Raw layer preserved verbatim
    assert "提示再生性贫血" in rec.medical_test_raw
    assert rec.follow_up_raw == "未提及"

    # Tier C retained intact in medical_history
    assert "外院怀疑为口鼻瘘" in rec.medical_history
    assert "主人怀疑被咬伤" in rec.medical_history
    assert "既往曾诊断为胰腺炎" in rec.medical_history

    # Explicit PE moved from medical_test to medical_history
    assert "体温38.5℃" in rec.medical_history
    assert "体温38.5℃" not in rec.medical_test

    # Standalone reasoning moved to notes with required prefix
    assert "提示再生性贫血" not in rec.medical_test
    assert "病变定位于T3-L3脊髓节段" not in rec.medical_test
    assert "【原文推理/解读剥离】提示再生性贫血" in rec.notes
    assert "【原文推理/解读剥离】病变定位于T3-L3脊髓节段" in rec.notes

    # Mixed fact+reasoning sentence preserved (not destructively deleted) and flagged review_required
    assert "X线检查见左肺野致密阴影，提示肺水肿" in rec.medical_test
    assert rec.review_status == ReviewStatus.REVIEW_REQUIRED
    assert rec.status == TaskStatus.REVIEW_REQUIRED
    assert any("mixed objective fact and reasoning" in r for r in rec.review_reasons)

    # Living exploration stays in medical_test; post-mortem moved to follow_up; placeholder cleared
    assert "开腹探查见回肠闭锁" in rec.medical_test
    assert "安乐死后剖检见脾脏血管肉瘤破裂" not in rec.medical_test
    assert "安乐死后剖检见脾脏血管肉瘤破裂" in rec.follow_up
    assert "未提及" not in rec.follow_up

    # Dx and Rx normalized
    assert rec.diagnosis_normalized == "猫医源性肾上腺皮质机能亢进"
    assert "po" in rec.medical_treatment and "bid" in rec.medical_treatment
    assert any("unit_conflict" in r for r in rec.review_reasons)


def test_normalize_case_fields_cleans_fragments_and_placeholders() -> None:
    rules = load_config().rules
    segment = CaseSegment(
        case_id="c2",
        document_id="d2",
        source_filename="b.pdf",
        source_type=SourceType.BOOK,
        title="第一章 · 病例1 等级1",
    )
    payload = LLMExtractedPayload(
        case_id="c2",
        medical_history="犬，3岁。",
        medical_test="X线整体。提示正常。同时超声也。",
        medical_treatment="无",
        diagnosis_raw="N/A",
        follow_up="—",
    )
    rec = normalize_case_fields(
        payload=payload,
        segment=segment,
        rules=rules,
        method="agent_interactive",
        model_name="agy",
        rule_version="1.0.0",
    )
    assert rec.medical_test == ""
    assert rec.medical_treatment == ""
    assert rec.diagnosis_normalized == ""
    assert rec.follow_up == ""
    assert "【原文推理/解读剥离】提示正常" in rec.notes
