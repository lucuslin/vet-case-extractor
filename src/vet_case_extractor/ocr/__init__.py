"""RapidOCR engine and structured laboratory table extraction."""

from vet_case_extractor.ocr.engine import (
    configure_ocr_thread_env,
    create_ocr_engine,
    determine_safe_workers,
    run_ocr_for_project,
)
from vet_case_extractor.ocr.tables import extract_lab_rows

__all__ = [
    "configure_ocr_thread_env",
    "create_ocr_engine",
    "determine_safe_workers",
    "extract_lab_rows",
    "run_ocr_for_project",
]
