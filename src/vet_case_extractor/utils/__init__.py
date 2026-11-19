"""Shared utility helpers for vet-case-extractor."""

from vet_case_extractor.utils.io import (
    atomic_write_json,
    atomic_write_text,
    compute_sha256,
    read_json,
    safe_filename,
)
from vet_case_extractor.utils.logging import setup_logging
from vet_case_extractor.utils.text import (
    clean_ocr_noise,
    normalize_for_match,
    normalize_founder_pua,
    strip_illegal_xml_chars,
)

__all__ = [
    "atomic_write_json",
    "atomic_write_text",
    "clean_ocr_noise",
    "compute_sha256",
    "normalize_for_match",
    "normalize_founder_pua",
    "read_json",
    "safe_filename",
    "setup_logging",
    "strip_illegal_xml_chars",
]
