"""Cross-platform CJK font discovery for synthetic PDF generation and `vetcase doctor`."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Literal

FontStatus = Literal["available", "missing", "not_applicable", "unchecked"]


def _detect_platform() -> Literal["windows", "macos", "linux", "other"]:
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    if sys.platform.startswith("linux"):
        return "linux"
    return "other"


def _platform_candidates() -> list[tuple[str, Path]]:
    windir = Path(os.environ.get("WINDIR", r"C:\Windows"))
    win_fonts = windir / "Fonts"
    candidates: list[tuple[str, Path]] = [
        ("windows", win_fonts / "msyh.ttc"),
        ("windows", win_fonts / "simsun.ttc"),
        ("windows", win_fonts / "simhei.ttf"),
        ("windows", win_fonts / "simfang.ttf"),
        ("linux", Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc")),
        ("linux", Path("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc")),
        ("linux", Path("/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc")),
        ("linux", Path("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc")),
        ("linux", Path("/usr/share/fonts/truetype/wqy/wqy-microhei.ttc")),
        ("linux", Path("/usr/share/fonts/truetype/arphic/uming.ttc")),
        ("linux", Path("/mnt/c/Windows/Fonts/msyh.ttc")),
        ("linux", Path("/mnt/c/Windows/Fonts/simsun.ttc")),
        ("macos", Path("/System/Library/Fonts/PingFang.ttc")),
        ("macos", Path("/System/Library/Fonts/STHeiti Light.ttc")),
        ("macos", Path("/System/Library/Fonts/STHeiti Medium.ttc")),
        ("macos", Path("/System/Library/Fonts/Supplemental/Songti.ttc")),
        ("macos", Path("/Library/Fonts/Arial Unicode.ttf")),
    ]
    return candidates


def probe_cjk_fonts(
    explicit_font: str | Path | None = None,
    *,
    check_filesystem: bool = True,
    target_platform: str | None = None,
) -> dict[str, Any]:
    """Probe CJK fonts across Windows, Linux/WSL, macOS, or user-configured path.

    Each candidate is assigned one of four statuses:
    - `available`: checked on active platform (or custom path) and found
    - `missing`: checked on active platform (or custom path) and not found
    - `not_applicable`: candidate belongs to a different OS platform
    - `unchecked`: filesystem check was skipped (`check_filesystem=False`)
    """
    current_plat = target_platform or _detect_platform()
    env_font = os.environ.get("VETCASE_CJK_FONT", "").strip()
    custom_spec = explicit_font if explicit_font is not None else (env_font or None)

    details: list[dict[str, str]] = []
    selected_font: str | None = None
    legacy_map: dict[str, bool] = {}

    if custom_spec is not None:
        custom_path = Path(custom_spec)
        if not check_filesystem:
            c_status: FontStatus = "unchecked"
        else:
            c_status = "available" if custom_path.is_file() else "missing"
            if c_status == "available":
                selected_font = str(custom_path)
        details.append(
            {
                "path": str(custom_path),
                "platform": "custom",
                "status": c_status,
            }
        )
        legacy_map[str(custom_path)] = c_status == "available"

    for cand_plat, cand_path in _platform_candidates():
        path_str = cand_path.as_posix() if cand_plat in ("linux", "macos") else str(cand_path)
        if not check_filesystem:
            status: FontStatus = "unchecked"
        elif cand_plat != current_plat and current_plat != "other":
            status = "not_applicable"
        else:
            try:
                exists = cand_path.is_file()
            except OSError:
                exists = False
            status = "available" if exists else "missing"
            if exists and selected_font is None:
                selected_font = path_str
            legacy_map[path_str] = exists

        details.append(
            {
                "path": path_str,
                "platform": cand_plat,
                "status": status,
            }
        )

    if not check_filesystem:
        overall_status: FontStatus = "unchecked"
    elif selected_font is not None:
        overall_status = "available"
    else:
        overall_status = "missing"

    return {
        "current_platform": current_plat,
        "status": overall_status,
        "selected_font": selected_font,
        "candidates": details,
        "legacy_map": legacy_map,
        "note": (
            "System CJK fonts are only required when generating synthetic PDF test fixtures; "
            "PDF reading, RapidOCR, segmentation, extraction, QA, and Excel export do not depend on local system fonts."
        ),
    }


def find_cjk_font(explicit_font: str | Path | None = None) -> Path:
    """Return the first available CJK TrueType/OpenType font or raise FileNotFoundError."""
    if explicit_font is not None:
        p = Path(explicit_font)
        if p.is_file():
            return p
        raise FileNotFoundError(f"Explicit CJK font not found: {p}")

    env_font = os.environ.get("VETCASE_CJK_FONT", "").strip()
    if env_font:
        p = Path(env_font)
        if p.is_file():
            return p
        raise FileNotFoundError(f"VETCASE_CJK_FONT path not found: {p}")

    probe = probe_cjk_fonts()
    if probe["selected_font"]:
        return Path(probe["selected_font"])

    # Fallback: check all candidates across platforms in case of cross-mounted filesystems
    for _, cand_path in _platform_candidates():
        try:
            if cand_path.is_file():
                return cand_path
        except OSError:
            continue

    checked = [d["path"] for d in probe["candidates"] if d["status"] != "not_applicable"]
    raise FileNotFoundError(
        "No CJK font found for synthetic PDF generation. Set VETCASE_CJK_FONT or install a CJK font "
        f"(e.g. fonts-noto-cjk / fonts-wqy-zenhei on Linux). Checked: {', '.join(checked)}"
    )
