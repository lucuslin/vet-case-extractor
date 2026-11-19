"""Fault-injection, exception, and resume/recovery tests (10+ failure scenarios)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from click.testing import CliRunner

from vet_case_extractor.cli import (
    EXIT_OK,
    EXIT_QA_FAILURE,
    EXIT_RUNTIME_ERROR,
    EXIT_USAGE_CONFIG_ERROR,
    main,
)
from vet_case_extractor.config import AppConfig
from vet_case_extractor.ingestion.project import ProjectWorkspace
from vet_case_extractor.models import (
    LLMExtractedPayload,
    SourceType,
    TaskStatus,
)
from vet_case_extractor.pipeline import (
    ConfigUsageError,
    extract_project,
    index_project,
    ingest_project,
    ocr_project,
)
from vet_case_extractor.providers.base import (
    LLMProvider,
    LLMResponse,
    ProviderRateLimitError,
    ProviderTimeoutError,
)
from vet_case_extractor.utils.io import atomic_write_json


class _FlakyProvider(LLMProvider):
    """Provider that fails on selected case_ids on first attempt, then succeeds."""

    provider_name = "gemini"

    def __init__(self, fail_case_ids: set[str]) -> None:
        self.model_name = "gemini-2.5-flash"
        self.fail_case_ids = set(fail_case_ids)
        self.called_case_ids: list[str] = []

    def extract_structured(
        self,
        system_prompt: str,
        user_prompt: str,
        response_model: type[LLMExtractedPayload],
    ) -> LLMResponse[LLMExtractedPayload]:
        case_id = ""
        title = ""
        for line in user_prompt.splitlines():
            clean_line = line.lstrip("- ").strip()
            if clean_line.startswith("case_id:"):
                case_id = clean_line.split(":", 1)[1].strip()
            elif clean_line.startswith("title:"):
                title = clean_line.split(":", 1)[1].strip()
        self.called_case_ids.append(case_id)

        if case_id in self.fail_case_ids:
            self.fail_case_ids.remove(case_id)
            raise ProviderTimeoutError(f"Simulated timeout for {case_id}")

        return LLMResponse(
            parsed=response_model(
                case_id=case_id,
                source="黑龙江畜牧兽医",
                publication_year="2024",
                received_year="2024",
                title=title or case_id,
                medical_history="患犬，体温38.4℃。" if "001" in case_id else "患猫，体温37.2℃。",
                medical_test="BUN 32 mmol/L" if "001" in case_id else "左心房显著扩张",
                medical_treatment="静脉滴注生理盐水" if "001" in case_id else "",
                diagnosis_raw="犬急性肾损伤" if "001" in case_id else "猫动脉血栓栓塞",
                follow_up="",
            ),
            model_name=self.model_name,
            token_usage={"prompt_tokens": 50, "completion_tokens": 25, "total_tokens": 75},
            raw_text="{}",
        )


def test_01_empty_dir_and_missing_input_exit_code_2(tmp_path: Path) -> None:
    empty_dir = tmp_path / "empty_inputs"
    empty_dir.mkdir()
    runner = CliRunner()

    res_empty = runner.invoke(main, ["ingest", "-p", str(tmp_path / "p1"), "-i", str(empty_dir)])
    assert res_empty.exit_code == EXIT_USAGE_CONFIG_ERROR

    res_missing = runner.invoke(
        main, ["ingest", "-p", str(tmp_path / "p2"), "-i", str(tmp_path / "does_not_exist.pdf")]
    )
    assert res_missing.exit_code == EXIT_USAGE_CONFIG_ERROR


def test_02_duplicate_pdf_sha256_deduplication(
    tmp_path: Path,
    synthetic_pdf_dir: Path,
) -> None:
    src = synthetic_pdf_dir / "合成_猫血支原体感染病例报告.pdf"
    in_dir = tmp_path / "dup_inputs"
    in_dir.mkdir()
    shutil.copy2(src, in_dir / "01_orig.pdf")
    shutil.copy2(src, in_dir / "02_renamed_duplicate.pdf")

    proj = tmp_path / "dup_proj"
    res1 = ingest_project(proj, in_dir, mode=SourceType.JOURNAL)
    assert len(res1["added_doc_ids"]) == 1
    assert len(res1["skipped_duplicate_doc_ids"]) == 1

    # Re-ingesting the same file is also skipped
    res2 = ingest_project(proj, in_dir / "01_orig.pdf", mode=SourceType.JOURNAL)
    assert len(res2["added_doc_ids"]) == 0
    assert len(res2["skipped_duplicate_doc_ids"]) == 1


def test_03_corrupted_pdf_isolated_from_valid_batch(
    tmp_path: Path,
    synthetic_pdf_dir: Path,
) -> None:
    in_dir = tmp_path / "mixed_bad"
    in_dir.mkdir()
    shutil.copy2(synthetic_pdf_dir / "合成_猫血支原体感染病例报告.pdf", in_dir / "valid.pdf")
    shutil.copy2(synthetic_pdf_dir / "合成_损坏PDF文件.pdf", in_dir / "corrupted.pdf")

    proj = tmp_path / "corrupt_proj"
    res = ingest_project(proj, in_dir, mode=SourceType.JOURNAL)
    assert len(res["failed_doc_ids"]) == 1
    assert len(res["processed_doc_ids"]) == 1

    ws = ProjectWorkspace(proj)
    manifest = ws.load_manifest()
    failed_doc = manifest.documents[res["failed_doc_ids"][0]]
    assert failed_doc.status == TaskStatus.FAILED
    assert failed_doc.error

    # Indexing still succeeds for the valid document
    idx = index_project(proj, mode=SourceType.JOURNAL)
    assert idx.total_cases == 1


def test_04_missing_api_key_raises_config_error_exit_2(
    tmp_path: Path,
    synthetic_pdf_dir: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    proj = tmp_path / "nokey_proj"
    runner = CliRunner()
    assert (
        runner.invoke(
            main,
            [
                "run",
                "-p",
                str(proj),
                "-i",
                str(synthetic_pdf_dir / "合成_猫血支原体感染病例报告.pdf"),
                "--provider",
                "none",
            ],
        ).exit_code
        == EXIT_OK
    )

    res_gemini = runner.invoke(main, ["extract", "-p", str(proj), "--provider", "gemini"])
    assert res_gemini.exit_code == EXIT_USAGE_CONFIG_ERROR
    assert "GEMINI_API_KEY" in res_gemini.output

    res_openai = runner.invoke(main, ["extract", "-p", str(proj), "--provider", "openai_compat"])
    assert res_openai.exit_code == EXIT_USAGE_CONFIG_ERROR


def test_05_invalid_cli_arguments_exit_code_2(tmp_path: Path) -> None:
    runner = CliRunner()
    assert runner.invoke(main, ["run", "-p", str(tmp_path), "-i", str(tmp_path), "--mode", "invalid"]).exit_code == EXIT_USAGE_CONFIG_ERROR
    assert runner.invoke(main, ["export", "-p", str(tmp_path), "--layer", "badlayer"]).exit_code == EXIT_USAGE_CONFIG_ERROR
    assert runner.invoke(main, ["qa", "-p", str(tmp_path / "missing_proj")]).exit_code == EXIT_USAGE_CONFIG_ERROR


def test_06_per_case_llm_failure_isolation_resume_and_retry_failed(
    tmp_path: Path,
    synthetic_pdf_dir: Path,
    app_config: AppConfig,
    monkeypatch,
) -> None:
    proj = tmp_path / "resume_proj"
    ingest_project(proj, synthetic_pdf_dir / "合成_一文多病例报告.pdf", mode=SourceType.JOURNAL, cfg=app_config)
    idx = index_project(proj, mode=SourceType.JOURNAL, cfg=app_config)
    assert idx.total_cases == 2
    case1_id = idx.cases[0].case_id
    case2_id = idx.cases[1].case_id

    flaky = _FlakyProvider(fail_case_ids={case2_id})
    monkeypatch.setattr("vet_case_extractor.pipeline.get_provider", lambda name, cfg, model=None: flaky)

    # First run: case 1 succeeds, case 2 fails cleanly
    res1 = extract_project(proj, provider_name="gemini", cfg=app_config)
    assert res1["completed"] + res1["review_required"] == 1
    assert res1["failed"] == 1

    ws = ProjectWorkspace(proj)
    rec1 = ws.load_case_record(case1_id)
    rec2 = ws.load_case_record(case2_id)
    assert rec1 is not None and rec1.status != TaskStatus.FAILED
    assert rec2 is not None and rec2.status == TaskStatus.FAILED

    # Second run WITHOUT --retry-failed: skips completed case 1 AND skips failed case 2
    flaky.called_case_ids.clear()
    res2 = extract_project(proj, provider_name="gemini", retry_failed=False, cfg=app_config)
    assert res2["attempted"] == 0
    assert res2["skipped_completed"] == 1
    assert res2["skipped_failed"] == 1
    assert flaky.called_case_ids == []

    # Third run WITH --retry-failed: skips case 1, retries ONLY case 2, and succeeds
    res3 = extract_project(proj, provider_name="gemini", retry_failed=True, cfg=app_config)
    assert res3["attempted"] == 1
    assert res3["skipped_completed"] == 1
    assert res3["failed"] == 0
    assert flaky.called_case_ids == [case2_id]
    rec2_after = ws.load_case_record(case2_id)
    assert rec2_after is not None and rec2_after.status != TaskStatus.FAILED

    # Unknown case_id raises ConfigUsageError
    try:
        extract_project(proj, provider_name="gemini", case_ids=["nonexistent_case"], cfg=app_config)
        raise AssertionError("Expected ConfigUsageError for unknown case_id")
    except ConfigUsageError:
        pass


def test_07_truncated_import_json_fails_cleanly_exit_1(
    tmp_path: Path,
    synthetic_pdf_dir: Path,
) -> None:
    proj = tmp_path / "trunc_import_proj"
    runner = CliRunner()
    assert (
        runner.invoke(
            main,
            [
                "run",
                "-p",
                str(proj),
                "-i",
                str(synthetic_pdf_dir / "合成_猫血支原体感染病例报告.pdf"),
                "--provider",
                "none",
            ],
        ).exit_code
        == EXIT_OK
    )

    bad_json = proj / "truncated_import.json"
    bad_json.write_text('[{"case_id": "abc", "title": "broken', encoding="utf-8")
    res = runner.invoke(main, ["import-cases", "-p", str(proj), "-i", str(bad_json)])
    assert res.exit_code == EXIT_RUNTIME_ERROR


def test_08_corrupted_workspace_json_triggers_qa_exit_code_3(
    tmp_path: Path,
    synthetic_pdf_dir: Path,
) -> None:
    proj = tmp_path / "qa_corrupt_proj"
    runner = CliRunner()
    assert (
        runner.invoke(
            main,
            [
                "run",
                "-p",
                str(proj),
                "-i",
                str(synthetic_pdf_dir / "合成_猫血支原体感染病例报告.pdf"),
                "--provider",
                "none",
            ],
        ).exit_code
        == EXIT_OK
    )
    idx = json.loads((proj / "index.json").read_text(encoding="utf-8"))
    cid = idx["cases"][0]["case_id"]
    (proj / "cases" / cid / "record.json").write_text("{broken json", encoding="utf-8")

    qa_res = runner.invoke(main, ["qa", "-p", str(proj)])
    assert qa_res.exit_code == EXIT_QA_FAILURE


def test_09_ocr_cache_resume_and_corrupted_ocr_cache_recovery(
    tmp_path: Path,
    synthetic_pdf_dir: Path,
    app_config: AppConfig,
) -> None:
    proj = tmp_path / "ocr_cache_proj"
    ingest_res = ingest_project(
        proj,
        synthetic_pdf_dir / "合成_混合原生与扫描病例报告.pdf",
        mode=SourceType.JOURNAL,
        cfg=app_config,
    )
    doc_id = ingest_res["processed_doc_ids"][0]

    # First OCR run processes scanned page 2 (page_index=1)
    ocr1 = ocr_project(proj, workers=1, cfg=app_config)
    assert ocr1["pages_ocr_completed"] == 1
    assert ocr1["pages_skipped_cached"] == 0

    # Second OCR run uses cached JSON
    ocr2 = ocr_project(proj, workers=1, cfg=app_config)
    assert ocr2["pages_ocr_completed"] == 0
    assert ocr2["pages_skipped_cached"] == 1

    # Corrupt OCR cache JSON -> automatic re-OCR recovery without crash
    ws = ProjectWorkspace(proj)
    ws.page_ocr_path(doc_id, 1).write_text("{truncated", encoding="utf-8")
    ocr3 = ocr_project(proj, workers=1, cfg=app_config)
    assert ocr3["pages_ocr_completed"] == 1
    assert ocr3["pages_failed"] == 0


def test_10_ingest_retry_failed_recovers_and_qa_crosstalk_exit_3(
    tmp_path: Path,
    synthetic_pdf_dir: Path,
    app_config: AppConfig,
) -> None:
    proj = tmp_path / "recover_doc_proj"
    target_pdf = tmp_path / "recoverable.pdf"
    shutil.copy2(synthetic_pdf_dir / "合成_损坏PDF文件.pdf", target_pdf)

    res1 = ingest_project(proj, target_pdf, mode=SourceType.JOURNAL, cfg=app_config)
    assert len(res1["failed_doc_ids"]) == 1

    # Replace the unreadable file at the registered path and test retry_failed on proj
    shutil.copy2(synthetic_pdf_dir / "合成_猫血支原体感染病例报告.pdf", target_pdf)
    ws = ProjectWorkspace(proj)
    manifest = ws.load_manifest()
    failed_doc_id = res1["failed_doc_ids"][0]
    assert manifest.documents[failed_doc_id].status == TaskStatus.FAILED
    res_retry = ingest_project(proj, target_pdf, mode=SourceType.JOURNAL, retry_failed=True, cfg=app_config)
    assert failed_doc_id in res_retry["processed_doc_ids"]

    # Verify QA exit code 3 on semantic error (title-vs-content species/topic crosstalk via import-cases)
    proj2 = tmp_path / "crosstalk_qa_proj"
    cat_pdf = synthetic_pdf_dir / "合成_猫血支原体感染病例报告.pdf"
    ingest_project(proj2, cat_pdf, mode=SourceType.JOURNAL, cfg=app_config)
    index_project(proj2, mode=SourceType.JOURNAL, cfg=app_config)
    ws2 = ProjectWorkspace(proj2)
    idx = ws2.load_index()
    assert idx is not None
    cid = idx.cases[0].case_id
    bad_import = proj2 / "crosstalk_import.json"
    atomic_write_json(
        bad_import,
        [
            {
                "case_id": cid,
                "source": "中国兽医杂志",
                "publication_year": "2024",
                "title": "猫血支原体感染病例报告",
                "medical_history": "规模化猪场保育猪群发生水样腹泻，脱水明显。",
                "medical_test": "猪流行性腹泻病毒PCR检测阳性。",
                "medical_treatment": "补液与生物安全防控。",
                "diagnosis_raw": "猪流行性腹泻",
                "follow_up": "猪群腹泻控制。",
            }
        ],
    )
    runner = CliRunner()
    assert runner.invoke(main, ["import-cases", "-p", str(proj2), "-i", str(bad_import)]).exit_code == EXIT_OK
    qa_res = runner.invoke(main, ["qa", "-p", str(proj2)])
    assert qa_res.exit_code == EXIT_QA_FAILURE


def test_11_provider_rate_limit_exception_isolated(
    tmp_path: Path,
    synthetic_pdf_dir: Path,
    app_config: AppConfig,
    monkeypatch,
) -> None:
    class _RateLimitProvider(LLMProvider):
        provider_name = "gemini"

        def __init__(self) -> None:
            super().__init__(model_name="gemini-2.5-flash")

        def extract_structured(self, system_prompt, user_prompt, response_model):
            raise ProviderRateLimitError("429 Too Many Requests")

    monkeypatch.setattr(
        "vet_case_extractor.pipeline.get_provider",
        lambda name, cfg, model=None: _RateLimitProvider(),
    )
    proj = tmp_path / "ratelimit_proj"
    ingest_project(proj, synthetic_pdf_dir / "合成_猫血支原体感染病例报告.pdf", mode=SourceType.JOURNAL, cfg=app_config)
    index_project(proj, mode=SourceType.JOURNAL, cfg=app_config)
    res = extract_project(proj, provider_name="gemini", cfg=app_config)
    assert res["failed"] == 1
    assert res["completed"] == 0
