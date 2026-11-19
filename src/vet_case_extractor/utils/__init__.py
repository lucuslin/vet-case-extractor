"""Shared utility helpers for vet-case-extractor."""

from vet_case_extractor.utils.fonts import find_cjk_font, probe_cjk_fonts
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
    "find_cjk_font",
    "normalize_for_match",
    "normalize_founder_pua",
    "probe_cjk_fonts",
    "read_json",
    "safe_filename",
    "setup_logging",
    "strip_illegal_xml_chars",
]
