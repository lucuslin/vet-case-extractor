"""Deterministic normalization of veterinary Rx administration routes and frequencies."""

from __future__ import annotations

import re

from vet_case_extractor.config import ExtractionRules
from vet_case_extractor.models import NormalizationChange

_DEFAULT_RX_ROUTES = {
    "静脉滴注": "iv",
    "静脉注射": "iv",
    "静滴": "iv",
    "静注": "iv",
    "肌肉注射": "im",
    "肌内注射": "im",
    "肌注": "im",
    "皮下注射": "sc",
    "皮下": "sc",
    "口服": "po",
    "内服": "po",
    "灌服": "po",
}

_DEFAULT_RX_FREQUENCIES = {
    "每日1次": "sid",
    "每天1次": "sid",
    "每日一次": "sid",
    "每天一次": "sid",
    "1次/d": "sid",
    "1次/日": "sid",
    "1次/天": "sid",
    "1次/24h": "sid",
    "每日2次": "bid",
    "每天2次": "bid",
    "每日两次": "bid",
    "每天两次": "bid",
    "2次/d": "bid",
    "2次/日": "bid",
    "2次/天": "bid",
    "1次/12h": "bid",
    "每日3次": "tid",
    "每天3次": "tid",
    "每日三次": "tid",
    "每天三次": "tid",
    "3次/d": "tid",
    "3次/日": "tid",
    "3次/天": "tid",
    "1次/8h": "tid",
    "每日4次": "qid",
    "每天4次": "qid",
    "每日四次": "qid",
    "每天四次": "qid",
    "4次/d": "qid",
    "4次/日": "qid",
    "4次/天": "qid",
    "1次/6h": "qid",
}

_FORBIDDEN_PLACEHOLDERS = {
    "无", "未提及", "未见", "不适用", "n/a", "na", "无记录", "未详", "不详", "暂无", "—", "-"
}

# Match standalone uppercase PO/IV/IM/SC/SID/BID/TID/QID not attached to Roman numeral stage/grade suffixes
_UPPER_RX_ABBR_RE = re.compile(
    r"(?<![A-Za-z])(PO|IV|IM|SC|SID|BID|TID|QID)(?![A-Za-z级型期度])"
)

# Guard bare "皮下" so anatomical terms like "皮下肿块" / "皮下气肿" are NOT rewritten to "sc肿块"
_BARE_SUBCUT_CONTEXT_RE = re.compile(
    r"(?:(?<=\d)|(?<=kg)|(?<=mg)|(?<=ml)|(?<=mL)|(?<=IU)|(?<=U)|(?<=[，,、\s]))"
    r"皮下"
    r"(?=(?:给药|输液|补液|推注|[，,、。\s;；]|$))"
)


def _replace_with_spacing(text: str, pattern: re.Pattern[str], token: str) -> str:
    def _sub(match: re.Match[str]) -> str:
        start, end = match.span()
        before_ch = text[start - 1] if start > 0 else ""
        after_ch = text[end] if end < len(text) else ""
        pad_left = " " if before_ch and (before_ch.isalnum() or "\u4e00" <= before_ch <= "\u9fff") else ""
        pad_right = " " if after_ch and (after_ch.isalnum() or "\u4e00" <= after_ch <= "\u9fff") else ""
        return f"{pad_left}{token}{pad_right}"

    return pattern.sub(_sub, text)


def normalize_rx(
    treatment_raw: str,
    rules: ExtractionRules,
) -> tuple[str, list[NormalizationChange]]:
    """Standardize Rx routes (`po/iv/im/sc`) and frequencies (`sid/bid/tid/qid`)."""
    raw = (treatment_raw or "").strip()
    if not raw:
        return "", []

    placeholders = {p.lower() for p in (rules.forbidden_placeholders or [])} | _FORBIDDEN_PLACEHOLDERS
    if raw.lower() in placeholders:
        return (
            "",
            [
                NormalizationChange(
                    field_name="medical_treatment",
                    rule_id="RX_PLACEHOLDER_CLEARED",
                    before=raw,
                    after="",
                    reason="Cleared forbidden placeholder in medical_treatment field.",
                )
            ],
        )

    routes = dict(_DEFAULT_RX_ROUTES)
    routes.update(rules.rx_routes or {})
    freqs = dict(_DEFAULT_RX_FREQUENCIES)
    freqs.update(rules.rx_frequencies or {})

    work = raw
    changes: list[NormalizationChange] = []

    # Apply route conversions longest-first
    for src in sorted(routes.keys(), key=len, reverse=True):
        target = routes[src]
        if src == "皮下":
            if "皮下" in work and _BARE_SUBCUT_CONTEXT_RE.search(work):
                updated = _replace_with_spacing(work, _BARE_SUBCUT_CONTEXT_RE, target)
                if updated != work:
                    changes.append(
                        NormalizationChange(
                            field_name="medical_treatment",
                            rule_id="RX_ROUTE_NORMALIZED",
                            before=src,
                            after=target,
                            reason="Standardized subcutaneous administration route to 'sc'.",
                        )
                    )
                    work = updated
            continue

        if src in work:
            pat = re.compile(re.escape(src))
            updated = _replace_with_spacing(work, pat, target)
            if updated != work:
                changes.append(
                    NormalizationChange(
                        field_name="medical_treatment",
                        rule_id="RX_ROUTE_NORMALIZED",
                        before=src,
                        after=target,
                        reason=f"Standardized route '{src}' to '{target}'.",
                    )
                )
                work = updated

    # Apply frequency conversions longest-first
    for src in sorted(freqs.keys(), key=len, reverse=True):
        target = freqs[src]
        if src in work or src.lower() in work.lower():
            pat = re.compile(re.escape(src), re.IGNORECASE)
            updated = _replace_with_spacing(work, pat, target)
            if updated != work:
                changes.append(
                    NormalizationChange(
                        field_name="medical_treatment",
                        rule_id="RX_FREQUENCY_NORMALIZED",
                        before=src,
                        after=target,
                        reason=f"Standardized frequency '{src}' to '{target}'.",
                    )
                )
                work = updated

    # Normalize uppercase PO/IV/IM/SC/SID/BID/TID/QID tokens
    lowered = _UPPER_RX_ABBR_RE.sub(lambda m: m.group(1).lower(), work)
    if lowered != work:
        changes.append(
            NormalizationChange(
                field_name="medical_treatment",
                rule_id="RX_CASE_NORMALIZED",
                before=work,
                after=lowered,
                reason="Normalized uppercase Rx abbreviations to lowercase.",
            )
        )
        work = lowered

    # Clean redundant whitespace around Chinese/ASCII punctuation
    work = re.sub(r"[ \t]+", " ", work)
    work = re.sub(r"\s+([，。；：、）)])", r"\1", work)
    work = re.sub(r"([（(])\s+", r"\1", work)
    work = "\n".join(line.strip() for line in work.splitlines() if line.strip())

    return work, changes
