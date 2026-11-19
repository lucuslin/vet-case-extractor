"""End-to-end offline demo using clearly labeled SYNTHETIC Chinese veterinary PDFs.

Exercises:
1. Synthetic PDF fixture generation (native, scanned dual-col, mixed, multi-case, omnibus, scanned book, corrupted).
2. `vetcase` pipeline ingest -> RapidOCR -> index (stopping cleanly when provider=none).
3. Interactive Agent JSON import (`import_agent_cases`) -> normalization -> provenance grounding.
4. Structural/semantic/OCR QA (`run_project_qa`) and Excel/JSONL/provenance bundle export.
5. Regression consistency check tagged `UNANNOTATED_SYNTHETIC_NOT_CLINICAL_ACCURACY`.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from vet_case_extractor.config import load_config
from vet_case_extractor.export.bundle import export_project_bundle
from vet_case_extractor.models import OCRPolicy, SourceType
from vet_case_extractor.pipeline import (
    import_agent_cases,
    index_project,
    ingest_project,
    ocr_project,
    run_full_pipeline,
)
from vet_case_extractor.utils.io import atomic_write_json
from vet_case_extractor.validation.evaluation import evaluate_against_gold
from vet_case_extractor.validation.qa import run_project_qa


def _build_synthetic_agent_payloads(index_cases: list[Any]) -> list[dict[str, Any]]:
    """Construct faithful synthetic agent import items derived from indexed CaseSegments."""
    payloads: list[dict[str, Any]] = []
    for seg in index_cases:
        if seg.source_type == SourceType.BOOK:
            if seg.chapter_number == 1 and seg.case_number == 1:
                payloads.append(
                    {
                        "case_id": seg.case_id,
                        "title": seg.title,
                        "medical_history": "患犬，3岁，雄性。主诉精神沉郁2天，体格检查体温38.5℃。",
                        "medical_test": "生化检查 ALT 420 U/L（参考区间10~100 U/L）。",
                        "medical_treatment": "保肝治疗",
                        "diagnosis_raw": "犬急性肝细胞损伤",
                        "follow_up": "1周后复查好转。",
                        "evidence_quotes": {
                            "medical_history": ["精神沉郁"],
                            "medical_test": ["ALT"],
                            "diagnosis_raw": ["肝细胞损伤"],
                        },
                    }
                )
            elif seg.chapter_number == 1 and seg.case_number == 2:
                payloads.append(
                    {
                        "case_id": seg.case_id,
                        "title": seg.title,
                        "medical_history": "患猫，5岁，雌性。主诉厌食3天。体格检查可视黏膜黄染。",
                        "medical_test": "尿液分析见胆红素阳性。",
                        "medical_treatment": "",
                        "diagnosis_raw": "猫肝脂肪沉积",
                        "follow_up": "",
                        "evidence_quotes": {
                            "medical_history": ["厌食"],
                            "medical_test": ["胆红素"],
                            "diagnosis_raw": ["脂肪沉积"],
                        },
                    }
                )
            elif seg.chapter_number == 2 and seg.case_number == 1:
                payloads.append(
                    {
                        "case_id": seg.case_id,
                        "title": seg.title,
                        "medical_history": "患犬，7岁，雌性。主诉多饮多尿1个月。体格检查脱水约5%。",
                        "medical_test": "生化检查 CREA 310 μmol/L，尿比重1.012。",
                        "medical_treatment": "",
                        "diagnosis_raw": "犬慢性肾脏病",
                        "follow_up": "",
                        "evidence_quotes": {
                            "medical_history": ["多饮多尿"],
                            "medical_test": ["310"],
                            "diagnosis_raw": ["慢性肾脏病"],
                        },
                    }
                )
            else:
                payloads.append(
                    {
                        "case_id": seg.case_id,
                        "title": seg.title,
                        "medical_history": "患猫，10岁，雄性。主诉消瘦、呕吐。",
                        "medical_test": "血常规检查 HCT 22%。",
                        "medical_treatment": "皮下补液每日1次",
                        "diagnosis_raw": "猫慢性肾衰竭",
                        "follow_up": "2周后食欲改善。",
                        "evidence_quotes": {
                            "medical_history": ["消瘦"],
                            "medical_test": ["22%"],
                            "diagnosis_raw": ["慢性肾衰竭"],
                        },
                    }
                )
        else:
            title = seg.title
            if "猫血支原体" in title:
                payloads.append(
                    {
                        "case_id": seg.case_id,
                        "source": "中国兽医杂志",
                        "publication_year": "2024",
                        "received_year": "2023",
                        "title": title,
                        "medical_history": "患猫，雄性，3岁，体重4.2 kg。主诉精神沉郁、食欲废绝3天。一般体格检查：体温39.8℃，脉搏140次/min，呼吸频率32次/min，可视黏膜苍白，心肺听诊未见明显杂音。",
                        "medical_test": "血常规检查：RBC 2.8×10¹²/L，HCT 16.5%。PCR检测检出猫血支原体阳性。腹部B超检查见脾脏轻度肿大。",
                        "medical_treatment": "口服多西环素10 mg/kg，每日2次，连用21天；静脉滴注复方氯化钠注射液。",
                        "diagnosis_raw": "猫 血支原体感染",
                        "follow_up": "治疗2周后复查食欲恢复，精神好转。",
                        "evidence_quotes": {
                            "medical_history": ["体温39.8℃"],
                            "medical_test": ["猫血支原体阳性"],
                            "diagnosis_raw": ["猫血支原体感染"],
                        },
                    }
                )
            else:
                payloads.append(
                    {
                        "case_id": seg.case_id,
                        "source": seg.source_journal or "中国兽医杂志",
                        "publication_year": seg.publication_year or "2024",
                        "received_year": seg.received_year,
                        "title": title,
                        "medical_history": seg.raw_text[:120],
                        "medical_test": "",
                        "medical_treatment": "",
                        "diagnosis_raw": "猫角膜穿孔" if "角膜" in title else "犬创伤后肝细胞损伤",
                        "follow_up": "",
                    }
                )
    return payloads


def run_demo(work_dir: Path) -> dict[str, Any]:
    """Execute end-to-end synthetic demo for journal + scanned book workspaces."""
    script_dir = Path(__file__).resolve().parent
    if str(script_dir) not in sys.path:
        sys.path.insert(0, str(script_dir))
    from generate_synthetic_fixtures import generate_all_fixtures

    cfg = load_config()
    pdf_dir = work_dir / "synthetic_pdfs"
    fixtures = generate_all_fixtures(pdf_dir)

    # 1. Native single journal + omnibus target + agent import + QA + export
    journal_proj = work_dir / "journal_project"
    ingest_project(
        project_dir=journal_proj,
        input_path=fixtures["native_single"],
        mode=SourceType.JOURNAL,
        ocr_policy=OCRPolicy.AUTO,
        cfg=cfg,
    )
    ingest_project(
        project_dir=journal_proj,
        input_path=fixtures["omnibus"],
        mode=SourceType.JOURNAL,
        target_article_query="猫角膜穿孔",
        ocr_policy=OCRPolicy.AUTO,
        cfg=cfg,
    )
    ocr_project(project_dir=journal_proj, workers=1, cfg=cfg)
    idx_res_j = index_project(project_dir=journal_proj, mode=SourceType.JOURNAL, cfg=cfg)
    stage1 = {
        "stopped_after": "index",
        "total_cases": idx_res_j.total_cases,
    }
    from vet_case_extractor.ingestion.project import ProjectWorkspace

    ws_j = ProjectWorkspace(journal_proj)
    idx_j = ws_j.load_index()
    assert idx_j is not None
    payloads_j = _build_synthetic_agent_payloads(idx_j.cases)
    import_file_j = journal_proj / "synthetic_import.json"
    atomic_write_json(import_file_j, payloads_j)
    import_res_j = import_agent_cases(journal_proj, import_file_j, cfg=cfg)
    qa_j = run_project_qa(journal_proj, cfg=cfg)
    bundle_j = export_project_bundle(journal_proj, format_mode="journal", cfg=cfg)

    # 2. Scanned book with RapidOCR + page_offset=2
    book_proj = work_dir / "book_project"
    stage_b = run_full_pipeline(
        project_dir=book_proj,
        input_path=fixtures["scanned_book"],
        mode=SourceType.BOOK,
        provider="none",
        page_offset=2,
        ocr_workers=1,
        cfg=cfg,
    )
    ws_b = ProjectWorkspace(book_proj)
    idx_b = ws_b.load_index()
    assert idx_b is not None
    payloads_b = _build_synthetic_agent_payloads(idx_b.cases)
    import_file_b = book_proj / "synthetic_book_import.json"
    atomic_write_json(import_file_b, payloads_b)
    import_res_b = import_agent_cases(book_proj, import_file_b, cfg=cfg)
    qa_b = run_project_qa(book_proj, cfg=cfg)
    bundle_b = export_project_bundle(book_proj, format_mode="book", cfg=cfg)

    # 3. Synthetic self-consistency regression check (explicitly tagged non-clinical)
    records_j = ws_j.load_all_case_records()
    eval_rep = evaluate_against_gold(
        records_j,
        records_j,
        dataset_is_human_annotated=False,
    )

    return {
        "warning": eval_rep.metric_tag,
        "journal": {
            "stopped_after": stage1["stopped_after"],
            "import": import_res_j,
            "qa_status": qa_j.overall_status,
            "excel": str(bundle_j["excel"]),
        },
        "book": {
            "stopped_after": stage_b["stopped_after"],
            "import": import_res_b,
            "qa_status": qa_b.overall_status,
            "excel": str(bundle_b["excel"]),
        },
        "synthetic_regression_tag": eval_rep.metric_tag,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run end-to-end synthetic demo for vetcase.")
    parser.add_argument(
        "--work-dir",
        type=Path,
        default=Path("tmp/e2e_demo"),
        help="Working directory for synthetic demo outputs (default: tmp/e2e_demo).",
    )
    args = parser.parse_args()
    summary = run_demo(args.work_dir)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
