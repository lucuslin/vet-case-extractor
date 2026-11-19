"""Configuration loader combining YAML defaults, rule definitions, and environment variables."""

from __future__ import annotations

import contextlib
import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field


class PDFConfig(BaseModel):
    min_chars_per_page: int = 50
    min_cjk_ratio_with_image: float = 0.20
    max_garbled_ratio: float = 0.08
    use_pdfplumber_fallback: bool = True
    render_all_pages_for_review: bool = True
    journal_render_dpi: int = 300
    book_render_dpi: int = 250


class OCRConfig(BaseModel):
    engine: str = "rapidocr"
    use_angle_cls: bool = False
    rec_batch_num: int = 16
    workers: int = 3
    ort_intra_op_threads: int = 2
    ort_inter_op_threads: int = 1
    min_ram_gb_for_3_workers: float = 8.0
    min_ram_gb_for_2_workers: float = 4.0


class LayoutConfig(BaseModel):
    header_margin_ratio: float = 0.07
    footer_margin_ratio: float = 0.93
    full_width_threshold_ratio: float = 0.58
    gutter_min_gap_ratio: float = 0.02


class SegmentationConfig(BaseModel):
    default_page_offset: int = 0
    batch_size: int = 8


class LLMConfig(BaseModel):
    provider: str = "gemini"
    gemini_model: str = "gemini-2.5-flash"
    openai_model: str = "gpt-4o-mini"
    temperature: float = 0.0
    timeout_seconds: float = 90.0
    max_retries: int = 3
    concurrency: int = 2
    chunk_max_chars: int = 18000
    chunk_overlap_chars: int = 800


class ExportConfig(BaseModel):
    default_layer: str = "normalized"
    freeze_header: bool = True
    wrap_text: bool = True


class ExtractionRules(BaseModel):
    rule_version: str = "1.0.0"
    species_prefixes: list[str] = Field(default_factory=list)
    rx_routes: dict[str, str] = Field(default_factory=dict)
    rx_frequencies: dict[str, str] = Field(default_factory=dict)
    reasoning_markers: list[str] = Field(default_factory=list)
    tier_c_retention_markers: list[str] = Field(default_factory=list)
    treatment_followup_leak_keywords: list[str] = Field(default_factory=list)
    postmortem_markers: list[str] = Field(default_factory=list)
    living_exploration_exclusions: list[str] = Field(default_factory=list)
    general_pe_markers: list[str] = Field(default_factory=list)
    specialty_or_neuro_markers: list[str] = Field(default_factory=list)
    forbidden_placeholders: list[str] = Field(default_factory=list)
    non_diagnosis_symptoms: list[str] = Field(default_factory=list)
    book_lab_table_headings: list[str] = Field(default_factory=list)
    book_summary_headings: list[str] = Field(default_factory=list)
    ocr_known_typo_candidates: dict[str, str] = Field(default_factory=dict)


class AppConfig(BaseModel):
    rule_version: str = "1.0.0"
    pdf: PDFConfig = Field(default_factory=PDFConfig)
    ocr: OCRConfig = Field(default_factory=OCRConfig)
    layout: LayoutConfig = Field(default_factory=LayoutConfig)
    segmentation: SegmentationConfig = Field(default_factory=SegmentationConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    export: ExportConfig = Field(default_factory=ExportConfig)
    rules: ExtractionRules = Field(default_factory=ExtractionRules)


def find_config_dir(explicit_dir: str | Path | None = None) -> Path | None:
    """Locate the config directory from explicit arg, env var, cwd, or package parents."""
    candidates: list[Path] = []
    if explicit_dir is not None:
        candidates.append(Path(explicit_dir))
    env_dir = os.environ.get("VETCASE_CONFIG_DIR")
    if env_dir:
        candidates.append(Path(env_dir))
    candidates.append(Path.cwd() / "config")
    for parent in Path(__file__).resolve().parents:
        candidates.append(parent / "config")

    for candidate in candidates:
        if candidate.is_dir() and (candidate / "default.yaml").exists():
            return candidate
    return None


def _read_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    return data if isinstance(data, dict) else {}


def load_config(
    config_dir: str | Path | None = None,
    dotenv_path: str | Path | None = None,
) -> AppConfig:
    """Load default YAML configuration, domain rules YAML, and `.env` overrides."""
    if dotenv_path:
        load_dotenv(dotenv_path=Path(dotenv_path), override=False)
    else:
        load_dotenv(override=False)

    resolved_dir = find_config_dir(config_dir)
    raw_default: dict[str, Any] = {}
    raw_rules: dict[str, Any] = {}

    if resolved_dir is not None:
        default_yaml = resolved_dir / "default.yaml"
        rules_yaml = resolved_dir / "extraction_rules.yaml"
        if default_yaml.exists():
            raw_default = _read_yaml(default_yaml)
        if rules_yaml.exists():
            raw_rules = _read_yaml(rules_yaml)

    merged = dict(raw_default)
    if raw_rules:
        merged["rules"] = raw_rules

    cfg = AppConfig.model_validate(merged)

    # Apply supported environment variable overrides
    if os.environ.get("VETCASE_GEMINI_MODEL"):
        cfg.llm.gemini_model = os.environ["VETCASE_GEMINI_MODEL"].strip()
    if os.environ.get("VETCASE_OPENAI_MODEL"):
        cfg.llm.openai_model = os.environ["VETCASE_OPENAI_MODEL"].strip()
    if os.environ.get("VETCASE_OCR_WORKERS"):
        with contextlib.suppress(ValueError):
            cfg.ocr.workers = max(1, int(os.environ["VETCASE_OCR_WORKERS"]))

    return cfg
