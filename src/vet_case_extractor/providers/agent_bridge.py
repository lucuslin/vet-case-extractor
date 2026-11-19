"""Interactive Agent batch export and strict JSON import bridge (platform-agnostic, no silent API spoofing)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from vet_case_extractor.config import ExtractionRules
from vet_case_extractor.models import IndexSummary, LLMExtractedPayload, utc_now_iso
from vet_case_extractor.providers.base import ProviderSchemaError
from vet_case_extractor.utils.io import atomic_write_json, atomic_write_text

_PAYLOAD_FIELDS = set(LLMExtractedPayload.model_fields.keys())


def _render_extraction_spec(rules: ExtractionRules) -> str:
    """Render EXTRACTION_SPEC.md with complete field, Dx, Rx, and incremental-save rules."""
    species_preview = "、".join(rules.species_prefixes[:25]) + (
        f" 等共 {len(rules.species_prefixes)} 种" if rules.species_prefixes else ""
    )
    payload_schema = json.dumps(
        LLMExtractedPayload.model_json_schema(),
        ensure_ascii=False,
        indent=2,
    )
    return f"""# EXTRACTION_SPEC.md (Rule Version {rules.rule_version})

## 核心纪律（务必严格遵守）
1. **逐字取自原文，缺失留空（`""`）**：绝不编造、不改写、不翻译、不补写假设性治疗。
2. **禁止占位符**：缺失字段一律填 `""`，禁止填 `无`、`未提及`、`未见`、`不适用`、`N/A`、`—`、`-`。
3. **分批与增量落盘**：每批建议 6~10 例；每抽取 1 例立即覆盖保存目标 JSON，防止长会话中途截断。
4. **必须保留原文证据**：每个非空抽取字段请在 `evidence_quotes` 中提供对应的原文逐字片段列表，以便后续溯源校验。

## 字段口径
- `case_id`: 必须与批次清单中的 `case_id` 完全一致。
- `source`: 期刊仅填中文刊名（如 `中国兽医杂志`）；书籍留空 `""`。
- `publication_year` / `received_year`: 刊期发表年与 `收稿日期` 年份（4 位数字）；书籍留空 `""`。
- `title`: 保持索引中的标题（期刊为文章/病例标题，书籍为 `章节名 · 病例x 等级y`）。
- `medical_history`: 动物基本信息（signalment）+ 主诉（CC）+ 现病史/既往史 + **一般体格检查（PE：体温/脉搏/呼吸/CRT/BCS/可视黏膜/心肺听诊/腹部触诊/体表淋巴结）** + **Tier C 外院/他院/主人/既往怀疑或诊疗经过**。
- `medical_test`: CBC/生化/尿检/影像（X/US/CT/MRI/Echo）+ **神经学检查客观发现** + **专科检查（眼科/口腔等）** + **本次就诊诊断性采样/活检/存活动物术中探查及离体肉眼检查**。不含一般体格检查，不含推理/临床意义/神经学定位。
- `medical_treatment`: 治疗方案、手术过程、Rx 用药（含剂量/途径/频次）。不含随访/复查/好转/死亡等结果，不含书籍`解析`中的假设性建议。
- `diagnosis_raw`: 原文诊断结论（后续由规则引擎按 Dx 五则自动规范化）。
- `follow_up`: 治疗转归、复诊、康复、死亡/安乐死时间线 + **全部死后剖检/尸检/安乐死后剖检及其伴随组织学/实验室检验**。
- `notes`: 从原文剥离的纯推理/临床意义/神经学定位说明（如 `提示…`、`符合…`、`病变定位于…`）及 OCR 存疑说明。
- `evidence_quotes`: `{{"medical_history": [...], "medical_test": [...], ...}}` 原文逐字短句引用。
- `uncertainties`: 不确定点或可疑 OCR 数值说明列表。

## Dx 书写五则（系统将再次校验）
1. 物种前置不空格（支持 {species_preview}），整行只在最前面写一次物种。
2. 主病在前，次要/并发/继发诊断排后。
3. 多诊断用中文分号 `；` 间隔。
4. 括号三分：解剖部位/限定词保留；解读说明移入 `notes`；更规范同义病名替换外层俗称；非诊断症状（{", ".join(rules.non_diagnosis_symptoms[:8])}）丢弃。
5. 缩写先全称后括号（如 `犬颈椎C4~C5水合髓核脱出（HNPE）`）。

## 输出 JSON Schema（每个元素必须符合 LLMExtractedPayload）
```json
{payload_schema}
```

输出文件格式应为 JSON 数组 `[{{...}}, {{...}}]`，或 `{{"rows": [{{...}}]}}`。
"""


def export_agent_batch(
    index: IndexSummary,
    completed_case_ids: set[str],
    output_dir: Path,
    rules: ExtractionRules,
    batch_size: int = 8,
) -> Path:
    """Export `EXTRACTION_SPEC.md`, per-batch manifests, and master batch manifest for interactive agent mode."""
    if batch_size < 1:
        raise ValueError(f"batch_size must be >= 1, got {batch_size}")

    output_dir.mkdir(parents=True, exist_ok=True)
    spec_path = output_dir / "EXTRACTION_SPEC.md"
    atomic_write_text(spec_path, _render_extraction_spec(rules), encoding="utf-8")

    pending = [seg for seg in index.cases if seg.case_id not in completed_case_ids]
    batches_meta: list[dict[str, Any]] = []

    for idx in range(0, len(pending), batch_size):
        chunk = pending[idx : idx + batch_size]
        batch_num = (idx // batch_size) + 1
        batch_id = f"batch_{batch_num:02d}"
        batch_file = output_dir / f"{batch_id}.json"
        suggested_output_file = output_dir / f"rows_{batch_num:02d}.json"

        batch_items = [
            {
                "case_id": seg.case_id,
                "document_id": seg.document_id,
                "source_type": seg.source_type.value,
                "source_filename": seg.source_filename,
                "source_journal": seg.source_journal,
                "publication_year": seg.publication_year,
                "received_year": seg.received_year,
                "title": seg.title,
                "page_range": seg.page_range,
                "book_page_range": seg.book_page_range,
                "packet_path": seg.packet_path,
                "image_paths": seg.image_paths,
                "boundary_uncertain": seg.boundary_uncertain,
                "boundary_notes": seg.boundary_notes,
            }
            for seg in chunk
        ]
        atomic_write_json(
            batch_file,
            {
                "batch_id": batch_id,
                "spec_path": str(spec_path),
                "suggested_output_path": str(suggested_output_file),
                "case_count": len(batch_items),
                "cases": batch_items,
            },
        )
        batches_meta.append(
            {
                "batch_id": batch_id,
                "manifest_path": str(batch_file),
                "suggested_output_path": str(suggested_output_file),
                "case_ids": [seg.case_id for seg in chunk],
                "case_count": len(chunk),
            }
        )

    master_manifest_path = output_dir / "agent_batches.json"
    atomic_write_json(
        master_manifest_path,
        {
            "generated_at": utc_now_iso(),
            "rule_version": rules.rule_version,
            "spec_path": str(spec_path),
            "batch_size": batch_size,
            "total_indexed_cases": len(index.cases),
            "already_completed_cases": len(completed_case_ids),
            "total_pending_cases": len(pending),
            "total_batches": len(batches_meta),
            "batches": batches_meta,
        },
    )
    return master_manifest_path


def _validate_item(item: Any, idx: int, source_path: Path) -> LLMExtractedPayload:
    if not isinstance(item, dict):
        raise ProviderSchemaError(
            f"Import item #{idx} in {source_path} must be a JSON object, got {type(item).__name__}."
        )
    if not (set(item.keys()) & _PAYLOAD_FIELDS):
        raise ProviderSchemaError(
            f"Import item #{idx} in {source_path} contains no recognized LLMExtractedPayload fields."
        )
    try:
        return LLMExtractedPayload.model_validate(item)
    except ValidationError as exc:
        raise ProviderSchemaError(
            f"Import item #{idx} in {source_path} failed schema validation: {exc}"
        ) from exc


def parse_agent_import_file(json_path: Path) -> list[LLMExtractedPayload]:
    """Strictly parse and validate raw or batched agent-extracted JSON items."""
    path = Path(json_path)
    if not path.exists():
        raise FileNotFoundError(f"Agent import JSON file not found: {path}")

    raw_text = path.read_text(encoding="utf-8")
    if not raw_text.strip():
        raise ProviderSchemaError(f"Agent import JSON file is empty: {path}")

    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise ProviderSchemaError(
            f"Corrupted or truncated JSON in {path} at line {exc.lineno} col {exc.colno}: {exc.msg}"
        ) from exc

    if isinstance(data, list):
        if not data:
            return []
        return [_validate_item(item, i, path) for i, item in enumerate(data)]

    if isinstance(data, dict):
        for container_key in ("rows", "cases", "items"):
            if container_key in data:
                items = data[container_key]
                if not isinstance(items, list):
                    raise ProviderSchemaError(
                        f"Key '{container_key}' in {path} must be a JSON array, got {type(items).__name__}."
                    )
                return [_validate_item(item, i, path) for i, item in enumerate(items)]
        return [_validate_item(data, 0, path)]

    raise ProviderSchemaError(
        f"Expected JSON array or object in {path}, got {type(data).__name__}."
    )
