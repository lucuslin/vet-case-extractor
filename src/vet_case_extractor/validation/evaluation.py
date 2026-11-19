"""Gold-standard evaluation metrics with strict synthetic vs human-annotated disclosure."""

from __future__ import annotations

from collections import Counter
from typing import Any

from pydantic import BaseModel, Field

from vet_case_extractor.models import SCHEMA_VERSION, CaseRecord, utc_now_iso

SYNTHETIC_WARNING_TAG = "UNANNOTATED_SYNTHETIC_NOT_CLINICAL_ACCURACY"
HUMAN_GOLD_TAG = "HUMAN_ANNOTATED_GOLD"

EVALUATED_FIELDS = (
    "source",
    "year",
    "title",
    "medical_history",
    "medical_test",
    "medical_treatment",
    "Dx",
    "follow-up",
    "备注",
)


class FieldEvaluation(BaseModel):
    """Per-field evaluation metrics."""

    field_name: str
    total_examples: int = 0
    exact_matches: int = 0
    exact_match_rate: float = 0.0
    tp: int = 0
    fp: int = 0
    fn: int = 0
    precision: float = 0.0
    recall: float = 0.0
    f1: float = 0.0
    char_f1: float = 0.0


class EvaluationReport(BaseModel):
    """Dataset evaluation report comparing predictions against gold references."""

    schema_version: str = SCHEMA_VERSION
    generated_at: str = Field(default_factory=utc_now_iso)
    dataset_is_human_annotated: bool = False
    clinical_accuracy_claim_allowed: bool = False
    metric_tag: str = SYNTHETIC_WARNING_TAG
    disclaimer: str = ""
    total_gold_records: int = 0
    total_pred_records: int = 0
    matched_record_ids: int = 0
    macro_exact_match: float = 0.0
    macro_precision: float = 0.0
    macro_recall: float = 0.0
    macro_f1: float = 0.0
    macro_char_f1: float = 0.0
    per_field: dict[str, FieldEvaluation] = Field(default_factory=dict)


def _record_to_field_map(item: CaseRecord | dict[str, Any]) -> tuple[str, dict[str, str]]:
    if isinstance(item, CaseRecord):
        key = item.case_id or item.title
        return key, {
            "source": item.source_title.strip(),
            "year": item.formatted_year().strip(),
            "title": item.title.strip(),
            "medical_history": item.medical_history.strip(),
            "medical_test": item.medical_test.strip(),
            "medical_treatment": item.medical_treatment.strip(),
            "Dx": item.diagnosis_normalized.strip(),
            "follow-up": item.follow_up.strip(),
            "备注": item.notes.strip(),
        }

    key = str(item.get("case_id") or item.get("title") or "")
    pub = str(item.get("publication_year") or "").strip()
    rec = str(item.get("received_year") or "").strip()
    year = str(item.get("year") or "").strip()
    if not year:
        year = f"{pub}（{rec}）" if pub and rec and pub != rec else (pub or rec)
    return key, {
        "source": str(item.get("source") or item.get("source_title") or "").strip(),
        "year": year,
        "title": str(item.get("title") or "").strip(),
        "medical_history": str(item.get("medical_history") or "").strip(),
        "medical_test": str(item.get("medical_test") or "").strip(),
        "medical_treatment": str(item.get("medical_treatment") or "").strip(),
        "Dx": str(item.get("Dx") or item.get("diagnosis_normalized") or "").strip(),
        "follow-up": str(item.get("follow-up") or item.get("follow_up") or "").strip(),
        "备注": str(item.get("备注") or item.get("notes") or "").strip(),
    }


def _char_f1(pred: str, gold: str) -> float:
    if not pred and not gold:
        return 1.0
    if not pred or not gold:
        return 0.0
    pred_chars = Counter(pred.replace(" ", ""))
    gold_chars = Counter(gold.replace(" ", ""))
    overlap = sum((pred_chars & gold_chars).values())
    if overlap == 0:
        return 0.0
    p = overlap / sum(pred_chars.values())
    r = overlap / sum(gold_chars.values())
    return 2 * p * r / (p + r)


def evaluate_against_gold(
    pred_records: list[CaseRecord] | list[dict[str, Any]],
    gold_records: list[CaseRecord] | list[dict[str, Any]],
    dataset_is_human_annotated: bool = False,
    *,
    refuse_unannotated: bool = False,
    fields: tuple[str, ...] = EVALUATED_FIELDS,
) -> EvaluationReport:
    """Compute field-level Exact Match, Precision, Recall, and F1 against gold records.

    Unless ``dataset_is_human_annotated=True``, the returned report explicitly carries the
    ``UNANNOTATED_SYNTHETIC_NOT_CLINICAL_ACCURACY`` tag and marks ``clinical_accuracy_claim_allowed=False``.
    Setting ``refuse_unannotated=True`` raises ``ValueError`` when ``dataset_is_human_annotated=False``.
    """
    if not dataset_is_human_annotated and refuse_unannotated:
        raise ValueError(
            f"{SYNTHETIC_WARNING_TAG}: Cannot report clinical accuracy without human-annotated gold data "
            "(pass dataset_is_human_annotated=True only for verified human annotations)."
        )

    pred_map: dict[str, dict[str, str]] = {}
    for idx, item in enumerate(pred_records):
        key, fmap = _record_to_field_map(item)
        pred_map[key or f"__idx_{idx}"] = fmap

    gold_map: dict[str, dict[str, str]] = {}
    for idx, item in enumerate(gold_records):
        key, fmap = _record_to_field_map(item)
        gold_map[key or f"__idx_{idx}"] = fmap

    all_keys = sorted(set(gold_map) | set(pred_map))
    matched_ids = len(set(gold_map) & set(pred_map))

    per_field: dict[str, FieldEvaluation] = {}
    for field in fields:
        exact_matches = 0
        tp = 0
        fp = 0
        fn = 0
        char_f1_scores: list[float] = []

        for key in all_keys:
            p_val = pred_map.get(key, {}).get(field, "")
            g_val = gold_map.get(key, {}).get(field, "")
            if p_val == g_val:
                exact_matches += 1
            if p_val and g_val:
                if p_val == g_val:
                    tp += 1
                else:
                    fp += 1
                    fn += 1
            elif p_val and not g_val:
                fp += 1
            elif g_val and not p_val:
                fn += 1
            char_f1_scores.append(_char_f1(p_val, g_val))

        n = len(all_keys)
        em_rate = exact_matches / n if n > 0 else 0.0
        if tp + fp == 0 and fn == 0:
            precision = 1.0 if n > 0 else 0.0
        else:
            precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0

        if tp + fn == 0 and fp == 0:
            recall = 1.0 if n > 0 else 0.0
        else:
            recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0

        f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) > 0 else 0.0
        avg_char_f1 = sum(char_f1_scores) / len(char_f1_scores) if char_f1_scores else 0.0

        per_field[field] = FieldEvaluation(
            field_name=field,
            total_examples=n,
            exact_matches=exact_matches,
            exact_match_rate=round(em_rate, 4),
            tp=tp,
            fp=fp,
            fn=fn,
            precision=round(precision, 4),
            recall=round(recall, 4),
            f1=round(f1, 4),
            char_f1=round(avg_char_f1, 4),
        )

    nf = len(per_field)
    macro_em = round(sum(fe.exact_match_rate for fe in per_field.values()) / nf, 4) if nf else 0.0
    macro_p = round(sum(fe.precision for fe in per_field.values()) / nf, 4) if nf else 0.0
    macro_r = round(sum(fe.recall for fe in per_field.values()) / nf, 4) if nf else 0.0
    macro_f1 = round(sum(fe.f1 for fe in per_field.values()) / nf, 4) if nf else 0.0
    macro_char_f1 = round(sum(fe.char_f1 for fe in per_field.values()) / nf, 4) if nf else 0.0

    if dataset_is_human_annotated:
        metric_tag = HUMAN_GOLD_TAG
        disclaimer = ""
    else:
        metric_tag = SYNTHETIC_WARNING_TAG
        disclaimer = (
            f"{SYNTHETIC_WARNING_TAG}: 输入评测集未标注为人工金标准（dataset_is_human_annotated=False），"
            "仅反映合成或回归一致性，不可用于宣称真实临床抽取准确率。"
        )

    return EvaluationReport(
        dataset_is_human_annotated=dataset_is_human_annotated,
        clinical_accuracy_claim_allowed=dataset_is_human_annotated,
        metric_tag=metric_tag,
        disclaimer=disclaimer,
        total_gold_records=len(gold_map),
        total_pred_records=len(pred_map),
        matched_record_ids=matched_ids,
        macro_exact_match=macro_em,
        macro_precision=macro_p,
        macro_recall=macro_r,
        macro_f1=macro_f1,
        macro_char_f1=macro_char_f1,
        per_field=per_field,
    )
