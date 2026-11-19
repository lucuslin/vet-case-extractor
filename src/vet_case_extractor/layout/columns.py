"""Dual-column and full-width layout restoration for extracted page text blocks."""

from __future__ import annotations

from vet_case_extractor.config import LayoutConfig
from vet_case_extractor.models import BBox, TextBlock


def _y_mid(bbox: BBox) -> float:
    return 0.5 * (bbox.y0 + bbox.y1)


def _quantile(sorted_vals: list[float], q: float) -> float:
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    idx = q * (len(sorted_vals) - 1)
    lo = int(idx)
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = idx - lo
    return sorted_vals[lo] * (1.0 - frac) + sorted_vals[hi] * frac


def _is_header_block(block: TextBlock, page_height: float, header_y: float) -> bool:
    return block.bbox.y1 <= header_y or (
        _y_mid(block.bbox) <= header_y and block.bbox.height <= 0.08 * page_height
    )


def _is_footer_zone(block: TextBlock, page_height: float, footer_y: float) -> bool:
    return block.bbox.y0 >= footer_y or (
        _y_mid(block.bbox) >= footer_y and block.bbox.height <= 0.08 * page_height
    )


def _split_header_footer_body(
    blocks: list[TextBlock],
    page_width: float,
    page_height: float,
    cfg: LayoutConfig,
) -> tuple[list[TextBlock], list[TextBlock], list[TextBlock]]:
    header_y = page_height * cfg.header_margin_ratio
    footer_y = page_height * cfg.footer_margin_ratio

    headers: list[TextBlock] = []
    non_headers: list[TextBlock] = []
    for blk in blocks:
        if _is_header_block(blk, page_height, header_y):
            headers.append(blk)
        else:
            non_headers.append(blk)

    footer_cands: list[TextBlock] = []
    above_footer: list[TextBlock] = []
    for blk in non_headers:
        if _is_footer_zone(blk, page_height, footer_y):
            footer_cands.append(blk)
        else:
            above_footer.append(blk)

    if not footer_cands:
        return headers, above_footer, []

    # Guard against dual-column body text extending into the bottom 7% margin
    # (common in Chinese journals where left column holds footnotes and right column holds prose).
    wide_thresh = page_width * cfg.full_width_threshold_ratio
    left_cands = [
        b for b in footer_cands if b.bbox.width < wide_thresh and b.bbox.x_mid < 0.48 * page_width
    ]
    right_cands = [
        b for b in footer_cands if b.bbox.width < wide_thresh and b.bbox.x_mid > 0.52 * page_width
    ]
    if left_cands and right_cands and above_footer:
        gap_thresh = 0.03 * page_height
        left_above = [b for b in above_footer if b.bbox.x_mid < 0.48 * page_width]
        right_above = [b for b in above_footer if b.bbox.x_mid > 0.52 * page_width]
        left_continues = bool(
            left_above
            and min(b.bbox.y0 for b in left_cands) - max(b.bbox.y1 for b in left_above) <= gap_thresh
        )
        right_continues = bool(
            right_above
            and min(b.bbox.y0 for b in right_cands) - max(b.bbox.y1 for b in right_above)
            <= gap_thresh
        )
        if left_continues and right_continues:
            # Keep continuing column lines in body; only spanning/isolated blocks at very bottom stay footer
            footers = [b for b in footer_cands if b.bbox.width >= wide_thresh]
            body = above_footer + [b for b in footer_cands if b.bbox.width < wide_thresh]
            return headers, body, footers

    return headers, above_footer, footer_cands


def _find_gutter_x(
    body_blocks: list[TextBlock],
    page_width: float,
    cfg: LayoutConfig,
) -> float | None:
    wide_thresh = page_width * cfg.full_width_threshold_ratio
    narrow = [b for b in body_blocks if b.bbox.width < wide_thresh]
    if len(narrow) < 2:
        return None

    min_x = 0.35 * page_width
    max_x = 0.65 * page_width
    tol = 0.01 * page_width
    min_gap = cfg.gutter_min_gap_ratio * page_width

    edges = sorted(
        {0.5 * page_width}
        | {b.bbox.x0 for b in narrow if min_x <= b.bbox.x0 <= max_x}
        | {b.bbox.x1 for b in narrow if min_x <= b.bbox.x1 <= max_x}
    )
    candidates = [0.5 * page_width]
    for i in range(len(edges) - 1):
        mid = 0.5 * (edges[i] + edges[i + 1])
        if min_x <= mid <= max_x:
            candidates.append(mid)

    best_score: tuple[int, int, float, float] | None = None
    best_gutter: float | None = None

    for gx in candidates:
        left = [b for b in narrow if b.bbox.x1 <= gx + tol]
        right = [b for b in narrow if b.bbox.x0 >= gx - tol]
        crossing = [b for b in narrow if b.bbox.x0 < gx - tol and b.bbox.x1 > gx + tol]

        if not left or not right:
            continue
        if len(left) + len(right) <= len(crossing):
            continue

        left_x1s = sorted(b.bbox.x1 for b in left)
        right_x0s = sorted(b.bbox.x0 for b in right)
        left_edge = _quantile(left_x1s, 0.9) if len(left_x1s) >= 5 else left_x1s[-1]
        right_edge = _quantile(right_x0s, 0.1) if len(right_x0s) >= 5 else right_x0s[0]
        gap = right_edge - left_edge
        if gap < min_gap:
            continue

        # Ensure left cluster is genuinely on the left and right cluster on the right
        if max(b.bbox.x_mid for b in left) >= gx or min(b.bbox.x_mid for b in right) <= gx:
            continue

        score = (
            len(left) + len(right) - 2 * len(crossing),
            min(len(left), len(right)),
            gap,
            -abs(gx - 0.5 * page_width),
        )
        if best_score is None or score > best_score:
            best_score = score
            best_gutter = gx

    return best_gutter


def restore_page_layout(
    blocks: list[TextBlock],
    page_width: float,
    page_height: float,
    cfg: LayoutConfig,
) -> tuple[list[TextBlock], str]:
    """Restore reading order across headers, spanning blocks, dual columns, and footers.

    Returns ordered TextBlock copies with updated `.column` labels and a formatted
    `layout_text` string annotated with `[[通栏]]`, `[[左栏]]`, and `[[右栏]]` markers
    whenever dual-column blocks are present.
    """
    non_empty = [b for b in blocks if b.text and b.text.strip()]
    if not non_empty:
        return [], ""

    resolved_w = page_width if page_width > 0 else max(b.bbox.x1 for b in non_empty)
    resolved_h = page_height if page_height > 0 else max(b.bbox.y1 for b in non_empty)
    resolved_w = max(resolved_w, 1.0)
    resolved_h = max(resolved_h, 1.0)

    headers, body, footers = _split_header_footer_body(non_empty, resolved_w, resolved_h, cfg)

    ordered_headers = [
        b.model_copy(update={"column": "header"})
        for b in sorted(headers, key=lambda b: (b.bbox.y0, b.bbox.x0))
    ]
    ordered_footers = [
        b.model_copy(update={"column": "footer"})
        for b in sorted(footers, key=lambda b: (b.bbox.y0, b.bbox.x0))
    ]

    gutter_x = _find_gutter_x(body, resolved_w, cfg)
    ordered_body: list[TextBlock] = []
    has_dual_columns = False

    if gutter_x is not None:
        wide_thresh = resolved_w * cfg.full_width_threshold_ratio
        tol = 0.01 * resolved_w
        full_blocks: list[TextBlock] = []
        col_blocks: list[TextBlock] = []

        for blk in body:
            crosses_gutter = blk.bbox.x0 < gutter_x - tol and blk.bbox.x1 > gutter_x + tol
            if blk.bbox.width >= wide_thresh or crosses_gutter:
                full_blocks.append(blk.model_copy(update={"column": "full"}))
            elif blk.bbox.x_mid < gutter_x:
                col_blocks.append(blk.model_copy(update={"column": "left"}))
            else:
                col_blocks.append(blk.model_copy(update={"column": "right"}))

        has_left = any(b.column == "left" for b in col_blocks)
        has_right = any(b.column == "right" for b in col_blocks)

        if has_left and has_right:
            has_dual_columns = True
            full_blocks.sort(key=lambda b: (b.bbox.y0, b.bbox.x0))

            # Assign each left/right block to a vertical band delimited by full-width blocks
            bands: list[list[TextBlock]] = [[] for _ in range(len(full_blocks) + 1)]
            for blk in col_blocks:
                band_idx = len(full_blocks)
                blk_ymid = _y_mid(blk.bbox)
                for idx, full_blk in enumerate(full_blocks):
                    if blk_ymid < _y_mid(full_blk.bbox):
                        band_idx = idx
                        break
                bands[band_idx].append(blk)

            for idx in range(len(full_blocks) + 1):
                left_band = sorted(
                    (b for b in bands[idx] if b.column == "left"),
                    key=lambda b: (b.bbox.y0, b.bbox.x0),
                )
                right_band = sorted(
                    (b for b in bands[idx] if b.column == "right"),
                    key=lambda b: (b.bbox.y0, b.bbox.x0),
                )
                ordered_body.extend(left_band)
                ordered_body.extend(right_band)
                if idx < len(full_blocks):
                    ordered_body.append(full_blocks[idx])
        else:
            ordered_body = [
                b.model_copy(update={"column": "full"})
                for b in sorted(body, key=lambda b: (b.bbox.y0, b.bbox.x0))
            ]
    else:
        ordered_body = [
            b.model_copy(update={"column": "full"})
            for b in sorted(body, key=lambda b: (b.bbox.y0, b.bbox.x0))
        ]

    ordered = ordered_headers + ordered_body + ordered_footers

    if not has_dual_columns:
        layout_text = "\n".join(b.text.strip() for b in ordered if b.text.strip())
        return ordered, layout_text

    marker_map = {
        "header": "[[通栏]]",
        "full": "[[通栏]]",
        "footer": "[[通栏]]",
        "left": "[[左栏]]",
        "right": "[[右栏]]",
    }
    lines: list[str] = []
    prev_marker: str | None = None
    for blk in ordered:
        text = blk.text.strip()
        if not text:
            continue
        marker = marker_map.get(blk.column, "[[通栏]]")
        if marker != prev_marker:
            lines.append(marker)
            prev_marker = marker
        lines.append(text)

    return ordered, "\n".join(lines)
