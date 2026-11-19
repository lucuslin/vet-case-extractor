"""Tests for providers (Gemini, OpenAI-compat, agent_bridge), chunking, prompts, and CaseExtractor."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import pytest

from vet_case_extractor.config import load_config
from vet_case_extractor.extraction.chunking import chunk_case_text
from vet_case_extractor.extraction.extractor import CaseExtractor
from vet_case_extractor.extraction.prompts import build_case_user_prompt, build_system_prompt
from vet_case_extractor.models import (
    BBox,
    CaseSegment,
    IndexSummary,
    LLMExtractedPayload,
    PageArtifact,
    ReviewStatus,
    SourceType,
    TaskStatus,
    TextBlock,
)
from vet_case_extractor.providers import (
    GeminiProvider,
    OpenAICompatProvider,
    ProviderConfigError,
    ProviderRateLimitError,
    ProviderSchemaError,
    ProviderTimeoutError,
    export_agent_batch,
    get_provider,
    parse_agent_import_file,
)
from vet_case_extractor.providers.base import LLMProvider, LLMResponse


def test_provider_missing_keys_and_factory(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = load_config()
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    with pytest.raises(ProviderConfigError, match="GEMINI_API_KEY"):
        GeminiProvider()

    with pytest.raises(ProviderConfigError, match="OPENAI_API_KEY"):
        OpenAICompatProvider()

    with pytest.raises(ProviderConfigError, match="Unsupported"):
        get_provider("unknown_provider", cfg)


def test_gemini_provider_structured_retries_and_failures() -> None:
    # 1. Succeeds after rate-limit and malformed JSON retries
    fake_client = MagicMock()
    rate_err = RuntimeError("429 RESOURCE_EXHAUSTED quota exceeded")
    bad_json_resp = SimpleNamespace(text='{"case_id": "c1", "broken":', parsed=None, usage_metadata=None)
    good_payload = LLMExtractedPayload(case_id="c1", diagnosis_raw="猫脂肪肝")
    good_resp = SimpleNamespace(
        text=good_payload.model_dump_json(),
        parsed=good_payload,
        usage_metadata=SimpleNamespace(
            prompt_token_count=120,
            candidates_token_count=45,
            total_token_count=165,
        ),
    )
    fake_client.models.generate_content.side_effect = [rate_err, bad_json_resp, good_resp]

    provider = GeminiProvider(
        model="gemini-2.5-flash",
        max_retries=3,
        client=fake_client,
        retry_wait_min=0.01,
        retry_wait_max=0.02,
    )
    res = provider.extract_structured("sys", "user", LLMExtractedPayload)
    assert res.parsed.diagnosis_raw == "猫脂肪肝"
    assert res.token_usage["total_tokens"] == 165
    assert fake_client.models.generate_content.call_count == 3

    call_kwargs = fake_client.models.generate_content.call_args.kwargs
    assert call_kwargs["model"] == "gemini-2.5-flash"
    assert call_kwargs["config"].response_mime_type == "application/json"
    assert call_kwargs["config"].temperature == 0.0

    # 2. Exhausting timeout raises ProviderTimeoutError
    timeout_client = MagicMock()
    timeout_client.models.generate_content.side_effect = TimeoutError("deadline exceeded")
    provider_to = GeminiProvider(
        max_retries=1,
        client=timeout_client,
        retry_wait_min=0.01,
        retry_wait_max=0.02,
    )
    with pytest.raises(ProviderTimeoutError):
        provider_to.extract_structured("sys", "user", LLMExtractedPayload)

    # 3. Exhausting schema errors raises ProviderSchemaError
    schema_client = MagicMock()
    schema_client.models.generate_content.return_value = SimpleNamespace(
        text="{not valid json", parsed=None, usage_metadata=None
    )
    provider_sc = GeminiProvider(
        max_retries=1,
        client=schema_client,
        retry_wait_min=0.01,
        retry_wait_max=0.02,
    )
    with pytest.raises(ProviderSchemaError):
        provider_sc.extract_structured("sys", "user", LLMExtractedPayload)


def test_openai_compat_provider_mock_transport_retries_and_errors() -> None:
    attempts = 0

    def handler_retry_success(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        assert request.url.path.endswith("/chat/completions")
        if attempts == 1:
            return httpx.Response(429, text="rate limited")
        if attempts == 2:
            return httpx.Response(
                200,
                json={
                    "choices": [{"message": {"content": '{"case_id": "c1", "title": "truncated'}}],
                },
            )
        return httpx.Response(
            200,
            json={
                "model": "gpt-test",
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {"case_id": "c1", "diagnosis_raw": "犬骨肉瘤"},
                                ensure_ascii=False,
                            )
                        }
                    }
                ],
                "usage": {"prompt_tokens": 50, "completion_tokens": 20, "total_tokens": 70},
            },
        )

    provider = OpenAICompatProvider(
        api_key="test-key",
        base_url="https://mock.local/v1",
        model="gpt-test",
        max_retries=3,
        transport=httpx.MockTransport(handler_retry_success),
        retry_wait_min=0.01,
        retry_wait_max=0.02,
    )
    resp = provider.extract_structured("sys", "user", LLMExtractedPayload)
    assert attempts == 3
    assert resp.parsed.diagnosis_raw == "犬骨肉瘤"
    assert resp.token_usage["total_tokens"] == 70

    # Persistent 429 -> ProviderRateLimitError
    provider_429 = OpenAICompatProvider(
        api_key="test-key",
        max_retries=1,
        transport=httpx.MockTransport(lambda req: httpx.Response(429, text="quota")),
        retry_wait_min=0.01,
        retry_wait_max=0.02,
    )
    with pytest.raises(ProviderRateLimitError):
        provider_429.extract_structured("sys", "user", LLMExtractedPayload)

    # 401 -> ProviderConfigError immediately (1 attempt)
    auth_calls = 0

    def handler_401(req: httpx.Request) -> httpx.Response:
        nonlocal auth_calls
        auth_calls += 1
        return httpx.Response(401, text="bad key")

    provider_401 = OpenAICompatProvider(
        api_key="bad-key",
        max_retries=3,
        transport=httpx.MockTransport(handler_401),
    )
    with pytest.raises(ProviderConfigError):
        provider_401.extract_structured("sys", "user", LLMExtractedPayload)
    assert auth_calls == 1

    # Timeout -> ProviderTimeoutError
    def handler_timeout(req: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("read timed out")

    provider_to = OpenAICompatProvider(
        api_key="k",
        max_retries=1,
        transport=httpx.MockTransport(handler_timeout),
        retry_wait_min=0.01,
        retry_wait_max=0.02,
    )
    with pytest.raises(ProviderTimeoutError):
        provider_to.extract_structured("sys", "user", LLMExtractedPayload)


def test_agent_bridge_export_and_strict_import(tmp_path: Path) -> None:
    cfg = load_config()
    segments = [
        CaseSegment(
            case_id=f"c_{i}",
            document_id="doc1",
            source_filename="a.pdf",
            source_type=SourceType.JOURNAL,
            title=f"病例{i}",
            packet_path=str(tmp_path / f"c_{i}/packet.txt"),
        )
        for i in range(1, 6)
    ]
    index = IndexSummary(total_documents=1, total_cases=5, cases=segments)

    manifest_path = export_agent_batch(
        index=index,
        completed_case_ids={"c_1", "c_2"},
        output_dir=tmp_path / "agent_work",
        rules=cfg.rules,
        batch_size=2,
    )
    assert manifest_path.exists()
    assert (tmp_path / "agent_work" / "EXTRACTION_SPEC.md").exists()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["total_pending_cases"] == 3
    assert manifest["total_batches"] == 2
    assert manifest["batches"][0]["case_ids"] == ["c_3", "c_4"]

    # Valid list import and batch wrapper import
    list_file = tmp_path / "valid_list.json"
    list_file.write_text(
        json.dumps([{"case_id": "c_3", "diagnosis_raw": "猫脂肪肝"}], ensure_ascii=False),
        encoding="utf-8",
    )
    items = parse_agent_import_file(list_file)
    assert len(items) == 1 and items[0].case_id == "c_3"

    batch_file = tmp_path / "valid_batch.json"
    batch_file.write_text(
        json.dumps({"rows": [{"case_id": "c_4", "diagnosis_raw": "犬胰腺炎"}]}, ensure_ascii=False),
        encoding="utf-8",
    )
    batch_items = parse_agent_import_file(batch_file)
    assert len(batch_items) == 1 and batch_items[0].case_id == "c_4"

    # Truncated JSON detection
    truncated = tmp_path / "truncated.json"
    truncated.write_text('[{"case_id": "c_3", "diagnosis_raw": "猫', encoding="utf-8")
    with pytest.raises(ProviderSchemaError, match="Corrupted or truncated JSON"):
        parse_agent_import_file(truncated)

    # Wrong field types
    invalid_type = tmp_path / "invalid_type.json"
    invalid_type.write_text('[{"case_id": "c_3", "evidence_quotes": "not_a_dict"}]', encoding="utf-8")
    with pytest.raises(ProviderSchemaError):
        parse_agent_import_file(invalid_type)


def test_prompts_and_chunking() -> None:
    cfg = load_config()
    sys_prompt = build_system_prompt(cfg.rules, SourceType.JOURNAL)
    assert "一般体格检查" in sys_prompt
    assert "神经学检查" in sys_prompt
    assert "死后剖检" in sys_prompt
    assert "Tier C" in sys_prompt
    assert "Dx 书写五则" in sys_prompt

    seg = CaseSegment(
        case_id="c1",
        document_id="d1",
        source_filename="a.pdf",
        source_type=SourceType.JOURNAL,
        title="测试病例",
    )
    user_prompt = build_case_user_prompt(seg, "原文段落", chunk_index=1, total_chunks=2)
    assert "1/2" in user_prompt
    assert "原文段落" in user_prompt

    long_text = "\n\n".join(f"--- PDF第{i}页 ---\n" + ("动物临床检验数据。" * 20) for i in range(1, 8))
    chunks = chunk_case_text(long_text, max_chars=250, overlap_chars=40)
    assert len(chunks) >= 3
    assert all(len(c) <= 250 for c in chunks)
    assert chunk_case_text("", max_chars=200, overlap_chars=20) == []


class _FakeCaseProvider(LLMProvider):
    provider_name = "fake_llm"

    def __init__(self, fail_case_id: str | None = None) -> None:
        super().__init__(model_name="fake-extract-1")
        self.fail_case_id = fail_case_id

    def extract_structured(self, system_prompt, user_prompt, response_model):
        if self.fail_case_id and self.fail_case_id in user_prompt:
            raise ProviderRateLimitError("simulated failure")
        parsed = LLMExtractedPayload(
            case_id="c1",
            medical_history="英短猫，3岁，体重4.2kg。体温38.6℃。",
            medical_test="白细胞18.2×10⁹/L。",
            medical_treatment="阿莫西林口服，每日2次。",
            diagnosis_raw="猫 急性胰腺炎",
            follow_up="2周后复查康复。",
            evidence_quotes={
                "medical_history": ["英短猫，3岁，体重4.2kg"],
                "medical_test": ["白细胞18.2×10⁹/L"],
                "medical_treatment": ["阿莫西林口服，每日2次"],
                "diagnosis_raw": ["急性胰腺炎"],
                "follow_up": ["2周后复查康复"],
            },
        )
        return LLMResponse(
            parsed=parsed,
            model_name=self.model_name,
            token_usage={"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
        )


def test_case_extractor_concurrency_and_error_isolation() -> None:
    cfg = load_config()
    cfg.llm.concurrency = 2
    cfg.llm.chunk_max_chars = 100

    source_text = (
        "英短猫，3岁，体重4.2kg。体温38.6℃。"
        "白细胞18.2×10⁹/L。阿莫西林口服，每日2次。"
        "诊断：急性胰腺炎。2周后复查康复。"
    )
    pages = [
        PageArtifact(
            document_id="d1",
            page_index=0,
            pdf_page_number=1,
            raw_text=source_text,
            cleaned_text=source_text,
            blocks=[
                TextBlock(
                    block_id="b1",
                    text=source_text,
                    bbox=BBox(x0=10, y0=10, x1=400, y1=120),
                )
            ],
        )
    ]
    seg_ok = CaseSegment(
        case_id="case_ok",
        document_id="d1",
        source_filename="ok.pdf",
        source_type=SourceType.JOURNAL,
        title="猫急性胰腺炎",
        page_indices=[0],
        raw_text=source_text * 2,  # forces chunking across 2+ chunks
    )
    seg_fail = CaseSegment(
        case_id="case_fail",
        document_id="d1",
        source_filename="fail.pdf",
        source_type=SourceType.JOURNAL,
        title="失败病例",
        page_indices=[0],
        raw_text="病例内容",
    )

    completed_ids: list[str] = []
    extractor = CaseExtractor(_FakeCaseProvider(fail_case_id="case_fail"), cfg)
    records = extractor.extract_batch(
        segments=[seg_ok, seg_fail],
        pages_by_doc={"d1": pages},
        sha256_by_doc={"d1": "abc123"},
        on_case_done=lambda r: completed_ids.append(r.case_id),
    )

    assert len(records) == 2
    assert set(completed_ids) == {"case_ok", "case_fail"}

    ok_rec, fail_rec = records[0], records[1]
    assert ok_rec.status == TaskStatus.COMPLETED
    assert ok_rec.review_status == ReviewStatus.OK
    assert ok_rec.diagnosis_normalized == "猫急性胰腺炎"
    assert ok_rec.token_usage["total_tokens"] >= 300  # accumulated across chunks
    assert ok_rec.file_sha256 == "abc123"
    assert all(sp.page_index == 0 and sp.bbox is not None for sp in ok_rec.evidence_spans)

    assert fail_rec.status == TaskStatus.FAILED
    assert fail_rec.review_status == ReviewStatus.FAILED
    assert "simulated failure" in (fail_rec.error or "")
