"""Validation, quality assurance, and gold-standard evaluation utilities."""

from vet_case_extractor.validation.evaluation import (
    EvaluationReport,
    FieldEvaluation,
    evaluate_against_gold,
)
from vet_case_extractor.validation.qa import render_qa_markdown, run_project_qa
from vet_case_extractor.validation.semantic import (
    validate_record_semantic,
    validate_semantic,
)
from vet_case_extractor.validation.structural import (
    check_project_json_integrity,
    validate_structural,
)

__all__ = [
    "EvaluationReport",
    "FieldEvaluation",
    "check_project_json_integrity",
    "evaluate_against_gold",
    "render_qa_markdown",
    "run_project_qa",
    "validate_record_semantic",
    "validate_semantic",
    "validate_structural",
]
