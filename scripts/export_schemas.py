"""Export versioned JSON Schemas from vet_case_extractor Pydantic models."""

from __future__ import annotations

import json
from pathlib import Path

from vet_case_extractor.models import (
    CaseRecord,
    EvidenceSpan,
    IndexSummary,
    ProjectManifest,
    QAReport,
)


def main() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    schemas_dir = repo_root / "schemas"
    schemas_dir.mkdir(parents=True, exist_ok=True)

    targets = {
        "case_record.schema.json": CaseRecord.model_json_schema(),
        "provenance.schema.json": {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "title": "ProvenanceBundle",
            "type": "array",
            "items": EvidenceSpan.model_json_schema(),
        },
        "qa_report.schema.json": QAReport.model_json_schema(),
        "index_summary.schema.json": IndexSummary.model_json_schema(),
        "project_manifest.schema.json": ProjectManifest.model_json_schema(),
    }

    for filename, payload in targets.items():
        target_path = schemas_dir / filename
        target_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"Wrote {target_path}")


if __name__ == "__main__":
    main()
