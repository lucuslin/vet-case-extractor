"""End-to-end tests for CLI subcommands, RapidOCR synthetic PDFs, agent bridge, and MCP server."""

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path

from click.testing import CliRunner
from openpyxl import load_workbook

from generate_synthetic_fixtures import generate_all_fixtures
from vet_case_extractor import mcp_server
from vet_case_extractor.cli import EXIT_OK, EXIT_QA_FAILURE, main
from vet_case_extractor.models import LLMExtractedPayload, QAReport
from vet_case_extractor.providers.base import LLMProvider, LLMResponse
from vet_case_extractor.utils.io import atomic_write_json


class _StubProvider(LLMProvider):
    provider_name = "openai_compat"

    def __init__(self, model_name: str = "stub-vet-model") -> None:
        self.model_name = model_name
        self.calls: list[str] = []

    def extract_structured(
        self,
        system_prompt: str,
        user_prompt: str,
        response_model: type[LLMExtractedPayload],
    ) -> LLMResponse[LLMExtractedPayload]:
        self.calls.append(user_prompt)
        case_id = ""
        title = ""
        for line in user_prompt.splitlines():
            clean_line = line.lstrip("- ").strip()
            if clean_line.startswith("case_id:"):
                case_id = clean_line.split(":", 1)[1].strip()
            elif clean_line.startswith("title:"):
                title = clean_line.split(":", 1)[1].strip()
        payload = response_model(
            case_id=case_id,
            source="中国兽医杂志",
            publication_year="2024",
            received_year="2023",
            title=title or "猫血支原体感染病例报告",
            medical_history="患猫，雄性，3岁，体温39.8℃。",
            medical_test="PCR检测检出猫血支原体阳性。",
            medical_treatment="口服多西环素10 mg/kg，每日2次。",
            diagnosis_raw="猫 血支原体感染",
            follow_up="治疗2周后复查食欲恢复。",
            evidence_quotes={
                "medical_history": ["体温39.8℃"],
                "medical_test": ["猫血支原体阳性"],
                "diagnosis_raw": ["猫血支原体感染"],
            },
        )
        return LLMResponse(
            parsed=payload,
            model_name=self.model_name,
            token_usage={"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
            raw_text="{}",
        )


def test_cli_help_and_subcommands() -> None:
    runner = CliRunner()
    res = runner.invoke(main, ["--help"])
    assert res.exit_code == EXIT_OK
    for subcmd in (
        "doctor",
        "ingest",
        "ocr",
        "index",
        "extract",
        "prepare-agent-batch",
        "import-cases",
        "qa",
        "export",
        "status",
        "run",
        "eval",
    ):
        assert subcmd in res.output
        sub_res = runner.invoke(main, [subcmd, "--help"])
        assert sub_res.exit_code == EXIT_OK


def test_cli_doctor_and_secret_masking(monkeypatch) -> None:
    secret = "SUPER_SECRET_TEST_KEY_987654"
    monkeypatch.setenv("GEMINI_API_KEY", secret)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    runner = CliRunner()
    res_txt = runner.invoke(main, ["doctor"])
    assert res_txt.exit_code == EXIT_OK
    assert "GEMINI_API_KEY: SET" in res_txt.output
    assert "OPENAI_API_KEY: NOT SET" in res_txt.output
    assert secret not in res_txt.output

    res_json = runner.invoke(main, ["doctor", "--json-output"])
    assert res_json.exit_code == EXIT_OK
    data = json.loads(res_json.output)
    assert data["api_keys_present"]["GEMINI_API_KEY"] is True
    assert data["api_keys_present"]["OPENAI_API_KEY"] is False
    assert data["hardware"]["recommended_ocr_workers"] >= 1
    assert data["packages"]["pymupdf"]["available"] is True
    assert data["packages"]["rapidocr-onnxruntime"]["available"] is True
    assert secret not in res_json.output


def test_cli_journal_e2e_with_real_rapidocr_and_agent_bridge(
    tmp_path: Path,
    synthetic_pdf_dir: Path,
) -> None:
    runner = CliRunner()
    fixtures = {p.name: p for p in synthetic_pdf_dir.glob("*.pdf")}

    # Prepare journal inputs (native single + mixed + scanned dual-col + multi-case)
    in_dir = tmp_path / "journal_inputs"
    in_dir.mkdir(parents=True)
    for fname in (
        "合成_猫血支原体感染病例报告.pdf",
        "合成_混合原生与扫描病例报告.pdf",
        "合成_纯扫描双栏犬肝酶异常病例.pdf",
        "合成_一文多病例报告.pdf",
    ):
        shutil.copy2(fixtures[fname], in_dir / fname)

    proj = tmp_path / "journal_proj"
    run_res = runner.invoke(
        main,
        [
            "run",
            "-p",
            str(proj),
            "-i",
            str(in_dir),
            "--mode",
            "journal",
            "--provider",
            "none",
            "--ocr-workers",
            "1",
        ],
    )
    assert run_res.exit_code == EXIT_OK, run_res.output
    assert "prepare-agent-batch" in run_res.output
    run_payload = json.loads(run_res.stdout)
    assert run_payload["stopped_after"] == "index"
    assert run_payload["ocr"]["pages_ocr_completed"] >= 2
    assert run_payload["index"]["total_documents"] == 4
    assert run_payload["index"]["total_cases"] == 5

    # Also test omnibus targeting in a separate workspace
    omni_proj = tmp_path / "omni_proj"
    omni_res = runner.invoke(
        main,
        [
            "run",
            "-p",
            str(omni_proj),
            "-i",
            str(fixtures["合成_猫角膜穿孔合订本病例.pdf"]),
            "--mode",
            "journal",
            "--provider",
            "none",
            "--target-article",
            "猫角膜穿孔",
        ],
    )
    assert omni_res.exit_code == EXIT_OK, omni_res.output
    omni_idx = json.loads((omni_proj / "index.json").read_text(encoding="utf-8"))
    assert len(omni_idx["cases"]) == 1
    assert "猫角膜穿孔" in omni_idx["cases"][0]["raw_text"]
    assert "猪流行性腹泻" not in omni_idx["cases"][0]["raw_text"]

    # Prepare agent batches on main journal project
    prep_res = runner.invoke(
        main,
        ["prepare-agent-batch", "-p", str(proj), "--batch-size", "2"],
    )
    assert prep_res.exit_code == EXIT_OK, prep_res.output
    prep_data = json.loads(prep_res.stdout)
    master_manifest = Path(prep_data["agent_batches_manifest"])
    assert master_manifest.exists()
    assert (master_manifest.parent / "EXTRACTION_SPEC.md").exists()
    assert (master_manifest.parent / "batch_01.json").exists()

    # Build valid agent import items matching all 5 indexed cases
    idx_data = json.loads((proj / "index.json").read_text(encoding="utf-8"))
    import_items = []
    for seg in idx_data["cases"]:
        cid = seg["case_id"]
        if "猫血支原体" in cid:
            import_items.append(
                {
                    "case_id": cid,
                    "source": "中国兽医杂志",
                    "publication_year": "2024",
                    "received_year": "2023",
                    "title": seg["title"],
                    "medical_history": "患猫，雄性，3岁，体温39.8℃，可视黏膜苍白。",
                    "medical_test": "RBC 2.8×10¹²/L，PCR检测检出猫血支原体阳性。",
                    "medical_treatment": "口服多西环素10 mg/kg，每日2次。",
                    "diagnosis_raw": "猫 血支原体感染",
                    "follow_up": "治疗2周后复查食欲恢复。",
                    "evidence_quotes": {
                        "medical_history": ["体温39.8℃"],
                        "medical_test": ["猫血支原体阳性"],
                        "diagnosis_raw": ["猫血支原体感染"],
                    },
                }
            )
        elif "混合" in cid:
            import_items.append(
                {
                    "case_id": cid,
                    "source": "畜牧与兽医",
                    "publication_year": "2023",
                    "received_year": "2022",
                    "title": seg["title"],
                    "medical_history": "患猫，雌性，3岁，体重3.5 kg。体温39.2℃。",
                    "medical_test": "腹部超声检查见肠管内强回声线状异物。",
                    "medical_treatment": "开腹探查术，术后静脉滴注氨苄西林每日2次。",
                    "diagnosis_raw": "猫肠道线性异物",
                    "follow_up": "出院后7天复查完全康复。",
                    "evidence_quotes": {
                        "medical_history": ["体温39.2℃"],
                        "medical_test": ["线状异物"],
                        "diagnosis_raw": ["猫肠道线性异物"],
                    },
                }
            )
        elif "纯扫描双栏" in cid:
            import_items.append(
                {
                    "case_id": cid,
                    "source": "中国兽医杂志",
                    "publication_year": "2024",
                    "received_year": "2023",
                    "title": seg["title"],
                    "medical_history": "患犬，雄性，6岁，外院怀疑胰腺炎。体温38.6℃。",
                    "medical_test": "肝酶异常（ALT/ALP轻度升高）；WBC 12.5×10^12/L，HGB 111.5 g/L，PCV 134%，GCT 85 U/L。",
                    "medical_treatment": "静脉注射头孢曲松钠每日1次，口服熊去氧胆酸每日2次。",
                    "diagnosis_raw": "犬创伤后肝细胞损伤",
                    "follow_up": "1个月后复查临床症状消失。",
                    "evidence_quotes": {
                        "medical_history": ["外院怀疑胰腺炎"],
                        "diagnosis_raw": ["犬创伤后肝细胞损伤"],
                    },
                }
            )
        elif seg["case_order"] == 1:
            import_items.append(
                {
                    "case_id": cid,
                    "source": "黑龙江畜牧兽医",
                    "publication_year": "2024",
                    "received_year": "2024",
                    "title": seg["title"],
                    "medical_history": "患犬，拉布拉多，2岁，体温38.4℃。",
                    "medical_test": "BUN 32 mmol/L，CREA 620 μmol/L。",
                    "medical_treatment": "静脉滴注生理盐水并实施血液透析。",
                    "diagnosis_raw": "犬急性肾损伤",
                    "follow_up": "2周后复查肾功能指标恢复正常。",
                    "evidence_quotes": {
                        "medical_history": ["体温38.4℃"],
                        "medical_test": ["620"],
                        "diagnosis_raw": ["犬急性肾损伤"],
                    },
                }
            )
        else:
            # Multi-case case 2 deliberately missing treatment and follow-up
            import_items.append(
                {
                    "case_id": cid,
                    "source": "黑龙江畜牧兽医",
                    "publication_year": "2024",
                    "received_year": "2024",
                    "title": seg["title"],
                    "medical_history": "患猫，英国短毛猫，5岁，体温37.2℃。",
                    "medical_test": "超声心动图检查见左心房显著扩张及自发显影血栓回声。",
                    "medical_treatment": "",
                    "diagnosis_raw": "猫动脉血栓栓塞",
                    "follow_up": "",
                    "evidence_quotes": {
                        "medical_history": ["体温37.2℃"],
                        "medical_test": ["左心房显著扩张"],
                        "diagnosis_raw": ["猫动脉血栓栓塞"],
                    },
                }
            )

    import_file = proj / "agent_rows.json"
    atomic_write_json(import_file, import_items)

    imp_res = runner.invoke(
        main,
        ["import-cases", "-p", str(proj), "-i", str(import_file), "--method", "agent_interactive"],
    )
    assert imp_res.exit_code == EXIT_OK, imp_res.output
    imp_data = json.loads(imp_res.stdout)
    assert imp_data["imported"] == 5

    # Status command
    st_res = runner.invoke(main, ["status", "-p", str(proj)])
    assert st_res.exit_code == EXIT_OK
    st_data = json.loads(st_res.stdout)
    assert st_data["extracted_records"] == 5

    # QA command (note: scanned_dual_col carries OCR lab anomalies -> REVIEW_REQUIRED, exit 0; --strict -> exit 3)
    qa_res = runner.invoke(main, ["qa", "-p", str(proj)])
    assert qa_res.exit_code == EXIT_OK, qa_res.output
    qa_strict = runner.invoke(main, ["qa", "-p", str(proj), "--strict"])
    assert qa_strict.exit_code == EXIT_QA_FAILURE

    # Export both normalized and raw layers
    exp_norm = runner.invoke(
        main,
        [
            "export",
            "-p",
            str(proj),
            "--format",
            "journal",
            "--layer",
            "normalized",
            "--filename",
            "中文导出_规范层.xlsx",
        ],
    )
    assert exp_norm.exit_code == EXIT_OK, exp_norm.output
    exp_paths = json.loads(exp_norm.stdout)
    wb = load_workbook(exp_paths["excel"])
    assert wb.sheetnames == ["病例数据", "说明"]
    ws = wb["病例数据"]
    assert ws.max_column == 9
    assert ws.max_row == 6
    wb.close()

    exp_raw = runner.invoke(
        main,
        [
            "export",
            "-p",
            str(proj),
            "-o",
            str(proj / "export_raw"),
            "--format",
            "journal",
            "--layer",
            "raw",
        ],
    )
    assert exp_raw.exit_code == EXIT_OK

    # Eval CLI command with synthetic tag verification
    eval_out = proj / "eval_report.json"
    eval_res = runner.invoke(
        main,
        ["eval", "--pred", exp_paths["cases_jsonl"], "--gold", exp_paths["cases_jsonl"], "-o", str(eval_out)],
    )
    assert eval_res.exit_code == EXIT_OK, eval_res.output
    eval_data = json.loads(eval_out.read_text(encoding="utf-8"))
    assert eval_data["metric_tag"] == "UNANNOTATED_SYNTHETIC_NOT_CLINICAL_ACCURACY"
    assert eval_data["clinical_accuracy_claim_allowed"] is False


def test_cli_book_e2e_with_real_rapidocr(
    tmp_path: Path,
    synthetic_pdf_dir: Path,
) -> None:
    runner = CliRunner()
    book_pdf = synthetic_pdf_dir / "合成_扫描兽医病例分析书籍.pdf"
    proj = tmp_path / "book_proj"

    run_res = runner.invoke(
        main,
        [
            "run",
            "-p",
            str(proj),
            "-i",
            str(book_pdf),
            "--mode",
            "book",
            "--provider",
            "none",
            "--page-offset",
            "2",
            "--ocr-workers",
            "1",
        ],
    )
    assert run_res.exit_code == EXIT_OK, run_res.output
    idx_data = json.loads((proj / "index.json").read_text(encoding="utf-8"))
    assert idx_data["total_cases"] == 4
    assert idx_data["cases"][0]["book_page_range"] == "1-2"
    assert idx_data["cases"][0]["chapter_number"] == 1
    assert idx_data["cases"][0]["case_level"] == 1

    # Import clean book records
    import_items = [
        {
            "case_id": seg["case_id"],
            "title": seg["title"],
            "medical_history": "患犬，3岁，体温38.5℃。" if idx == 0 else "患犬或猫，主诉见病史。",
            "medical_test": "ALT 420 U/L" if idx == 0 else "",
            "medical_treatment": "",
            "diagnosis_raw": "犬急性肝细胞损伤" if idx == 0 else "猫慢性肾脏病",
            "follow_up": "",
        }
        for idx, seg in enumerate(idx_data["cases"])
    ]
    imp_file = proj / "book_import.json"
    atomic_write_json(imp_file, import_items)

    assert (
        runner.invoke(main, ["import-cases", "-p", str(proj), "-i", str(imp_file)]).exit_code
        == EXIT_OK
    )
    assert runner.invoke(main, ["qa", "-p", str(proj)]).exit_code == EXIT_OK
    exp_res = runner.invoke(main, ["export", "-p", str(proj), "--format", "book"])
    assert exp_res.exit_code == EXIT_OK, exp_res.output
    wb = load_workbook(json.loads(exp_res.stdout)["excel"])
    assert wb["病例数据"].max_column == 7
    assert wb["病例数据"].max_row == 5
    wb.close()


def test_cli_extract_with_stub_provider_and_mcp_server(
    tmp_path: Path,
    monkeypatch,
) -> None:
    fixtures = generate_all_fixtures(tmp_path / "pdfs")
    stub = _StubProvider()
    monkeypatch.setattr("vet_case_extractor.pipeline.get_provider", lambda name, cfg, model=None: stub)

    proj = tmp_path / "api_proj"
    runner = CliRunner()
    assert (
        runner.invoke(
            main,
            ["run", "-p", str(proj), "-i", str(fixtures["native_single"]), "--provider", "openai_compat"],
        ).exit_code
        == EXIT_OK
    )
    assert len(stub.calls) == 1
    assert (proj / "export" / "病例抽取_总表.xlsx").exists()

    # Verify MCP server tool registration & calls
    server = mcp_server.create_mcp_server()
    tools = asyncio.run(server.list_tools())
    tool_names = {t.name for t in tools}
    assert tool_names == {"ingest_pdf", "extract_cases", "validate_cases", "export_cases", "get_status"}

    status_out = mcp_server.get_status(str(proj))
    assert status_out["extracted_records"] == 1
    qa_out = mcp_server.validate_cases(str(proj))
    assert QAReport.model_validate(qa_out).overall_status == "PASS"

    call_res = asyncio.run(server.call_tool("get_status", {"project_dir": str(proj)}))
    assert not call_res.is_error


def test_cli_output_alias_on_ingest_and_run(
    tmp_path: Path,
    synthetic_pdf_dir: Path,
    monkeypatch,
) -> None:
    runner = CliRunner()
    native_pdf = synthetic_pdf_dir / "合成_猫血支原体感染病例报告.pdf"

    # 1. `vetcase ingest --input <pdf> --output <dir>` works without --project
    ingest_out = tmp_path / "ingest_via_output"
    res_ing = runner.invoke(main, ["ingest", "--input", str(native_pdf), "--output", str(ingest_out)])
    assert res_ing.exit_code == EXIT_OK, res_ing.output
    assert (ingest_out / "manifest.json").exists()

    # Neither --project nor --output -> exit code 2
    assert runner.invoke(main, ["ingest", "--input", str(native_pdf)]).exit_code == 2
    # Conflicting --project and --output -> exit code 2
    assert (
        runner.invoke(
            main,
            ["ingest", "--input", str(native_pdf), "--project", str(tmp_path / "a"), "--output", str(tmp_path / "b")],
        ).exit_code
        == 2
    )

    # 2. `vetcase run --input <pdf> --output <dir> --mode auto --provider none`
    run_out = tmp_path / "run_via_output"
    res_run = runner.invoke(
        main,
        ["run", "--input", str(native_pdf), "--output", str(run_out), "--mode", "auto", "--provider", "none"],
    )
    assert res_run.exit_code == EXIT_OK, res_run.output
    assert (run_out / "manifest.json").exists()
    assert (run_out / "index.json").exists()

    # Also verify `run --input ... --output <dir>` with a stub provider writes exports to <output>/export/
    stub = _StubProvider()
    monkeypatch.setattr("vet_case_extractor.pipeline.get_provider", lambda name, cfg, model=None: stub)
    run_export_dir = tmp_path / "run_via_output_extract"
    res_run_ext = runner.invoke(
        main,
        [
            "run",
            "--input",
            str(native_pdf),
            "--output",
            str(run_export_dir),
            "--mode",
            "auto",
            "--provider",
            "openai_compat",
        ],
    )
    assert res_run_ext.exit_code == EXIT_OK, res_run_ext.output
    assert (run_export_dir / "export" / "病例抽取_总表.xlsx").exists()

    # Neither --project nor --output on `run` -> exit code 2
    assert runner.invoke(main, ["run", "--input", str(native_pdf), "--mode", "auto"]).exit_code == 2
