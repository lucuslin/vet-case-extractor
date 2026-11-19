"""Page text-layer quality assessment for deciding native extraction vs OCR escalation."""

from __future__ import annotations

import unicodedata

from vet_case_extractor.config import PDFConfig
from vet_case_extractor.models import OCRPolicy, PageQualityMetrics
from vet_case_extractor.utils.text import is_founder_pua_char

_EXPLICIT_WHITESPACE = frozenset({" ", "\t", "\n", "\r", "\f", "\v", "\u00a0", "\u3000"})


def _is_whitespace(ch: str) -> bool:
    return ch in _EXPLICIT_WHITESPACE or unicodedata.category(ch) == "Zs"


def _is_cjk_char(ch: str) -> bool:
    cp = ord(ch)
    return (
        0x4E00 <= cp <= 0x9FFF
        or 0x3400 <= cp <= 0x4DBF
        or 0xF900 <= cp <= 0xFAFF
        or 0x20000 <= cp <= 0x2CEAF
    )


def assess_page_quality(
    raw_text: str,
    image_count: int,
    full_page_image: bool,
    cfg: PDFConfig,
    ocr_policy: OCRPolicy = OCRPolicy.AUTO,
) -> PageQualityMetrics:
    """Assess extracted page text quality and decide whether OCR should be triggered.

    Distinguishes repairable CNKI/Founder legacy punctuation glyphs (`founder_pua_count`)
    from truly corrupted Private Use Area glyphs (`pua_count`), Unicode replacement chars
    (`replacement_char_count`), and control/unassigned codepoints (`garbled_ratio`).
    """
    char_count = 0
    cjk_count = 0
    pua_count = 0
    founder_pua_count = 0
    replacement_char_count = 0
    corrupt_other_count = 0

    for ch in raw_text:
        if _is_whitespace(ch):
            continue
        char_count += 1
        if is_founder_pua_char(ch):
            founder_pua_count += 1
            continue
        if ch == "\ufffd":
            replacement_char_count += 1
            continue
        cat = unicodedata.category(ch)
        if cat == "Co":
            pua_count += 1
        elif cat in ("Cc", "Cn", "Cs"):
            corrupt_other_count += 1
        elif _is_cjk_char(ch):
            cjk_count += 1

    cjk_ratio = (cjk_count / char_count) if char_count > 0 else 0.0
    garbled_count = pua_count + replacement_char_count + corrupt_other_count
    garbled_ratio = (garbled_count / char_count) if char_count > 0 else 0.0

    reasons: list[str] = []
    if char_count < cfg.min_chars_per_page:
        reasons.append(f"low_char_count({char_count}<{cfg.min_chars_per_page})")
    if garbled_ratio > cfg.max_garbled_ratio:
        reasons.append(f"high_garbled_ratio({garbled_ratio:.3f}>{cfg.max_garbled_ratio:.3f})")
    if full_page_image and cjk_ratio < cfg.min_cjk_ratio_with_image:
        reasons.append(
            f"full_page_image_low_cjk({cjk_ratio:.3f}<{cfg.min_cjk_ratio_with_image:.3f})"
        )

    if ocr_policy == OCRPolicy.NEVER:
        needs_ocr = False
        ocr_reason = ""
    elif ocr_policy == OCRPolicy.FORCE_ALL:
        needs_ocr = True
        ocr_reason = "; ".join([*reasons, "policy_force_all"])
    elif ocr_policy == OCRPolicy.FORCE_IMAGE_PAGES:
        if full_page_image or image_count > 0:
            reasons.append("policy_force_image_pages")
        needs_ocr = bool(reasons)
        ocr_reason = "; ".join(reasons)
    else:
        needs_ocr = bool(reasons)
        ocr_reason = "; ".join(reasons)

    return PageQualityMetrics(
        char_count=char_count,
        cjk_count=cjk_count,
        cjk_ratio=round(cjk_ratio, 6),
        pua_count=pua_count,
        founder_pua_count=founder_pua_count,
        replacement_char_count=replacement_char_count,
        garbled_ratio=round(garbled_ratio, 6),
        image_count=image_count,
        full_page_image=full_page_image,
        needs_ocr=needs_ocr,
        ocr_reason=ocr_reason,
    )
