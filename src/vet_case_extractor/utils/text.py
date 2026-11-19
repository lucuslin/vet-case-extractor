"""Text cleanup, CNKI/Founder font PUA repair, and matching normalization utilities."""

from __future__ import annotations

import re
import unicodedata

# Chinese PDFs produced with legacy Founder/CNKI GBK symbol fonts frequently map fullwidth
# ASCII/punctuation to Yi syllables U+A3A1..U+A3FE (mirroring U+FF01..U+FF5E, i.e. 0xA3xx -> 0xFFxx)
# and bullets to U+1001B0 (observed in Chinese Journal of Veterinary Medicine).
_FOUNDER_SPECIAL_MAP = {
    "\U001001b0": ".",
    "\u2014": "—",
    "\u00a0": " ",
    "\u3000": " ",
}

_ILLEGAL_XML_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def is_founder_pua_char(ch: str) -> bool:
    """Return True if char is a known CNKI/Founder legacy encoded punctuation glyph."""
    cp = ord(ch)
    return (0xA3A1 <= cp <= 0xA3FE) or cp == 0x1001B0 or cp == 0xE773


def normalize_founder_pua(text: str) -> str:
    """Repair known CNKI/Founder Yi/PUA codepoints (`0xA3xx` -> fullwidth ASCII, `0x1001B0` -> `.`).

    Notice: this only repairs deterministic legacy punctuation mappings, never altering medical numbers.
    """
    if not text:
        return ""
    out: list[str] = []
    for ch in text:
        if ch in _FOUNDER_SPECIAL_MAP:
            out.append(_FOUNDER_SPECIAL_MAP[ch])
            continue
        cp = ord(ch)
        if 0xA3A1 <= cp <= 0xA3FE:
            # Map U+A3A1..U+A3FE -> ASCII U+0021..U+007E or Chinese punctuation equivalents
            ascii_cp = cp - 0xA380
            mapped = chr(ascii_cp)
            if mapped == ",":
                out.append("，")
            elif mapped == ";":
                out.append("；")
            elif mapped == ":":
                out.append("：")
            elif mapped == ".":
                out.append("。")
            elif mapped == "(":
                out.append("（")
            elif mapped == ")":
                out.append("）")
            else:
                out.append(mapped)
        elif cp == 0xE773:
            out.append(" ")
        else:
            out.append(ch)
    return "".join(out)


def clean_ocr_noise(text: str, repair_founder: bool = True) -> str:
    """Join soft line wraps cleanly and remove redundant spaces between CJK characters."""
    if not text:
        return ""
    cleaned = normalize_founder_pua(text) if repair_founder else text
    # Convert fullwidth digits and ASCII letters (common in CNKI PDFs) to halfwidth, keeping Chinese punctuation
    chars: list[str] = []
    for ch in cleaned:
        cp = ord(ch)
        if 0xFF10 <= cp <= 0xFF19 or 0xFF21 <= cp <= 0xFF3A or 0xFF41 <= cp <= 0xFF5A:
            chars.append(unicodedata.normalize("NFKC", ch))
        else:
            chars.append(ch)
    cleaned = "".join(chars)
    cleaned = re.sub(r"[ \t\r\f\v]+", " ", cleaned)
    # Remove spaces directly between two CJK characters
    cleaned = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", cleaned)
    # Remove soft hyphenation artifacts from PDF line wraps like `mela-\nnoma` or `Feline￣`
    cleaned = cleaned.replace("￣\n", "-").replace("￣", "-")
    cleaned = re.sub(r"-\s*\n\s*", "", cleaned)
    lines = [line.strip() for line in cleaned.splitlines()]
    return "\n".join(line for line in lines if line)


def strip_illegal_xml_chars(text: str) -> str:
    """Remove XML 1.0 illegal control characters so openpyxl never fails."""
    if not text:
        return ""
    return _ILLEGAL_XML_RE.sub("", text)


def normalize_for_match(text: str) -> str:
    """Normalize whitespace and punctuation for fuzzy evidence substring matching."""
    if not text:
        return ""
    s = unicodedata.normalize("NFKC", normalize_founder_pua(text))
    s = re.sub(r"\s+", "", s)
    s = (
        s.replace("，", ",")
        .replace("。", ".")
        .replace("；", ";")
        .replace("：", ":")
        .replace("（", "(")
        .replace("）", ")")
    )
    return s.lower()
