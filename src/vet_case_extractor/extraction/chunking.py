"""Text chunking with configurable overlap and boundary-aware splitting."""

from __future__ import annotations


def chunk_case_text(text: str, max_chars: int, overlap_chars: int) -> list[str]:
    """Split long case text into overlapping chunks preferring page/paragraph/sentence boundaries."""
    if max_chars <= 0:
        raise ValueError(f"max_chars must be positive, got {max_chars}")
    if overlap_chars < 0:
        raise ValueError(f"overlap_chars must be non-negative, got {overlap_chars}")

    if not text or not text.strip():
        return []
    if len(text) <= max_chars:
        return [text]

    effective_overlap = min(overlap_chars, max(0, max_chars - 1))
    chunks: list[str] = []
    start = 0
    text_len = len(text)

    while start < text_len:
        target_end = min(text_len, start + max_chars)
        end = target_end

        if target_end < text_len:
            search_floor = start + max(1, (target_end - start) // 2)
            window = text[search_floor:target_end]
            split_offset = -1
            for marker in ("\n--- PDF第", "\n\n", "\n", "。", "；"):
                pos = window.rfind(marker)
                if pos > 0:
                    offset = pos if marker.startswith("\n---") else pos + len(marker)
                    candidate_end = search_floor + offset
                    if candidate_end > start:
                        split_offset = candidate_end
                        break
            if split_offset > start:
                end = split_offset

        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)

        if end >= text_len:
            break

        next_start = max(start + 1, end - effective_overlap)
        start = next_start

    return chunks
