from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from quant_signal.application.llm_analysis import (
    AnalysisContextService,
    BacktestJobService,
    DebateAnalysisService,
    LLMAnalysisService,
    OpenAICompatibleClient,
)
from quant_signal.domain.models import Bar, JobStatus, MarketObservation
from quant_signal.infrastructure.memory import MemoryQuantRepository


class FakeLLMClient:
    model_name = "fake-analyst"

    async def complete_json(self, system_prompt: str, payload: dict) -> dict:
        assert "不得補造" in system_prompt
        assert payload["symbol_signal"]["symbol"] == "TEST"
        assert payload["market_environment"]["market"] == "TW"
        return {
            "headline": "個股偏強，但仍需依大盤條件控管風險",
            "stance": "bullish",
            "operation_guide": "僅在訊號維持偏多且大盤未轉弱時考慮增加曝險。",
            "key_evidence": ["個股量化分數高於中性區間"],
            "risk_warnings": ["合成資料不可作為投資依據"],
            "scenarios": [
                {
                    "name": "偏多延續",
                    "condition": "個股與大盤分數同步維持",
                    "response": "維持條件式偏多觀察",
                }
            ],
            "limitations": ["目前使用 synthetic_demo"],
        }


async def test_llm_analysis_uses_quant_context(
    trending_bars: list[Bar],
    healthy_market: list[MarketObservation],
) -> None:
    repository = MemoryQuantRepository()
    await repository.upsert_bars(trending_bars)
    await repository.upsert_market_observations(healthy_market)

    report = await LLMAnalysisService(repository, FakeLLMClient()).analyze(
        symbol="TEST",
        as_of=trending_bars[-1].trading_date,
    )

    assert report.model == "fake-analyst"
    assert report.judgement.stance == "bullish"
    assert report.context.symbol_signal.symbol == "TEST"
    assert report.context.market_environment.market == "TW"


class FakeDebateLLMClient:
    model_name = "fake-debater"

    async def complete_json(self, system_prompt: str, payload: dict) -> dict:
        assert "不得補造" in system_prompt
        if "多方（看漲）" in system_prompt:
            assert payload["symbol_signal"]["symbol"] == "TEST"
            return {
                "stance": "bullish",
                "thesis": "個股量化分數偏高，趨勢延續。",
                "key_evidence": ["訊號分數高於中性區間"],
                "counterpoints_to_address": ["樣本仍為合成資料"],
            }
        if "空方（看跌）" in system_prompt:
            assert payload["symbol_signal"]["symbol"] == "TEST"
            return {
                "stance": "bearish",
                "thesis": "缺乏真實成交量佐證，風險偏高。",
                "key_evidence": ["合成資料樣本不足"],
                "counterpoints_to_address": ["個股分數持續偏多"],
            }
        if "風險管理研究員" in system_prompt and "裁決者" not in system_prompt:
            return {"summary": "注意資料限制", "risk_warnings": ["資料限制"],
                    "invalidation_conditions": ["訊號轉弱"],
                    "evidence_refs": ["symbol_signal", "invented"]}
        assert "裁決者" in system_prompt
        assert payload["bull_case"]["stance"] == "bullish"
        assert payload["bear_case"]["stance"] == "bearish"
        return {
            "headline": "多方證據較強，但需留意合成資料限制",
            "stance": "bullish",
            "operation_guide": "在樣本轉為真實資料前，僅以小部位驗證偏多假設。",
            "key_evidence": ["訊號分數高於中性區間"],
            "risk_warnings": ["合成資料不可作為投資依據"],
            "scenarios": [],
            "limitations": ["目前使用 synthetic_demo"],
        }


async def test_debate_analysis_weighs_bull_and_bear_cases(
    trending_bars: list[Bar],
    healthy_market: list[MarketObservation],
) -> None:
    repository = MemoryQuantRepository()
    await repository.upsert_bars(trending_bars)
    await repository.upsert_market_observations(healthy_market)

    report = await DebateAnalysisService(repository, FakeDebateLLMClient()).analyze(
        symbol="TEST",
        as_of=trending_bars[-1].trading_date,
    )

    assert report.model == "fake-debater"
    assert report.judge_model == "fake-debater"
    assert report.bull_case.stance == "bullish"
    assert report.bear_case.stance == "bearish"
    assert report.risk_review is not None
    assert report.risk_review.evidence_refs == ["symbol_signal"]
    assert {e.id for e in report.evidence} >= {"symbol_signal", "market_environment"}
    assert "合成資料" in " ".join(report.judgement.risk_warnings)


class FakeJudgeLLMClient:
    model_name = "fake-judge"

    async def complete_json(self, system_prompt: str, payload: dict) -> dict:
        assert "裁決者" in system_prompt
        assert payload["bull_case"]["stance"] == "bullish"
        assert payload["bear_case"]["stance"] == "bearish"
        return {
            "headline": "獨立裁決模型：證據不足以支持任一方向",
            "stance": "neutral",
            "operation_guide": "維持觀望，等待真實資料補齊後再行判斷。",
            "key_evidence": ["雙方證據都建立在合成資料上"],
            "risk_warnings": ["合成資料不可作為投資依據"],
            "scenarios": [],
            "limitations": ["目前使用 synthetic_demo"],
        }


async def test_debate_analysis_uses_independent_judge_model(
    trending_bars: list[Bar],
    healthy_market: list[MarketObservation],
) -> None:
    repository = MemoryQuantRepository()
    await repository.upsert_bars(trending_bars)
    await repository.upsert_market_observations(healthy_market)

    report = await DebateAnalysisService(
        repository,
        FakeDebateLLMClient(),
        judge_client=FakeJudgeLLMClient(),
    ).analyze(symbol="TEST", as_of=trending_bars[-1].trading_date)

    assert report.model == "fake-debater"
    assert report.judge_model == "fake-judge"
    assert report.bull_case.stance == "bullish"
    assert report.bear_case.stance == "bearish"
    # The independent judge reached a different conclusion than the
    # single-model fallback test above -- proving it's actually a separate
    # call, not silently reusing the debaters' own client.
    assert report.judgement.stance == "neutral"


def test_openai_compatible_client_parses_fenced_json() -> None:
    parsed = OpenAICompatibleClient._parse_json(
        '```json\n{"headline":"ok","stance":"neutral"}\n```'
    )
    assert parsed["headline"] == "ok"


def test_openai_compatible_client_accepts_base_or_completion_url() -> None:
    base_client = OpenAICompatibleClient(
        base_url="https://example.test/v1",
        model="test-model",
        api_key="test-key",
        temperature=0,
        max_tokens=100,
        timeout_seconds=10,
    )
    endpoint_client = OpenAICompatibleClient(
        base_url="https://example.test/v1/chat/completions",
        model="test-model",
        api_key="test-key",
        temperature=0,
        max_tokens=100,
        timeout_seconds=10,
    )

    assert base_client.completion_url == "https://example.test/v1/chat/completions"
    assert endpoint_client.completion_url == "https://example.test/v1/chat/completions"


async def test_debate_synthetic_market_forces_wait(
    trending_bars: list[Bar], healthy_market: list[MarketObservation],
) -> None:
    repository = MemoryQuantRepository()
    await repository.upsert_bars(trending_bars)
    await repository.upsert_market_observations(
        [item.model_copy(update={"source": "synthetic_demo"}) for item in healthy_market]
    )
    service = DebateAnalysisService(repository, FakeDebateLLMClient())
    report = await service.analyze(symbol="TEST", as_of=trending_bars[-1].trading_date)
    assert not report.data_sufficient
    assert report.judgement.stance == "neutral"
    assert "觀望" in report.judgement.operation_guide
    assert not report.judgement.scenarios


@pytest.mark.parametrize("symbol_mismatch", [False, True])
async def test_context_rejects_incompatible_backtest(
    trending_bars: list[Bar], healthy_market: list[MarketObservation], monkeypatch,
    symbol_mismatch: bool,
) -> None:
    repository = MemoryQuantRepository()
    await repository.upsert_bars(trending_bars)
    await repository.upsert_market_observations(healthy_market)
    cutoff = trending_bars[-1].trading_date
    spec = SimpleNamespace(
        symbol="OTHER" if symbol_mismatch else "TEST",
        end=cutoff if symbol_mismatch else cutoff + timedelta(days=1),
    )

    async def get_job(self, job_id):
        return SimpleNamespace(status=JobStatus.COMPLETED, result=SimpleNamespace(spec=spec))

    monkeypatch.setattr(BacktestJobService, "get", get_job)
    with pytest.raises(ValueError, match="backtest symbol" if symbol_mismatch else "backtest end"):
        await AnalysisContextService(repository).build(
            symbol="TEST", as_of=cutoff, market="TW", benchmark="^TWII",
            strategy_version="regime_v1", backtest_job_id=uuid4(),
        )


class VerboseDebateLLMClient(FakeDebateLLMClient):
    """Answers like Claude did in production: more list items than the schema allows."""

    model_name = "verbose-debater"

    async def complete_json(self, system_prompt: str, payload: dict) -> dict:
        assert "每個陣列最多 8 項" in system_prompt
        result = await super().complete_json(system_prompt, payload)
        if "stance" in result and "thesis" in result:
            result["key_evidence"] = [f"證據 {i}" for i in range(1, 10)]  # 9 items
            result["counterpoints_to_address"] = [f"反駁 {i}" for i in range(1, 11)]  # 10
        if "invalidation_conditions" in result:
            result["risk_warnings"] = [f"風險 {i}" for i in range(1, 12)]
        if "headline" in result:
            result["scenarios"] = [
                {"name": f"情境 {i}", "condition": "條件", "response": "應對"} for i in range(7)
            ]
        return result


async def test_debate_keeps_the_most_important_items_when_a_model_lists_too_many(
    trending_bars: list[Bar],
    healthy_market: list[MarketObservation],
) -> None:
    repository = MemoryQuantRepository()
    await repository.upsert_bars(trending_bars)
    await repository.upsert_market_observations(healthy_market)

    report = await DebateAnalysisService(repository, VerboseDebateLLMClient()).analyze(
        symbol="TEST",
        as_of=trending_bars[-1].trading_date,
    )

    assert report.bull_case.key_evidence == [f"證據 {i}" for i in range(1, 9)]
    assert len(report.bear_case.counterpoints_to_address) == 8
    assert report.bear_case.counterpoints_to_address[0] == "反駁 1"
    assert report.risk_review is not None and len(report.risk_review.risk_warnings) == 8
    assert len(report.judgement.scenarios) == 5


def test_an_answer_in_the_wrong_shape_becomes_a_readable_error() -> None:
    from quant_signal.application.llm_analysis import LLMProviderError, validate_llm_output
    from quant_signal.domain.models import DebateCase

    with pytest.raises(LLMProviderError, match="key_evidence") as caught:
        validate_llm_output(DebateCase, {"stance": "bullish", "thesis": "x", "key_evidence": []})
    assert "請再試一次" in str(caught.value)
    assert isinstance(caught.value, ValueError)  # routes still answer 502


def test_openai_compatible_errors_keep_the_provider_reason() -> None:
    import httpx

    from quant_signal.application.llm_analysis import LLMProviderError

    request = httpx.Request("POST", "https://api.example.test/v1/chat/completions")
    too_long = httpx.Response(
        400,
        request=request,
        json={"error": {"message": "This model's maximum context length is 8192 tokens",
                        "code": "context_length_exceeded"}},
    )
    with pytest.raises(LLMProviderError) as caught:
        OpenAICompatibleClient._raise_for_status(too_long)
    message = str(caught.value)
    assert "api.example.test" in message and "HTTP 400" in message
    assert "maximum context length" in message

    leaky = httpx.Response(401, request=request, text="invalid key sk-abcdefghijklmnop")
    with pytest.raises(LLMProviderError) as caught:
        OpenAICompatibleClient._raise_for_status(leaky)
    assert "sk-abcdefghijklmnop" not in str(caught.value)
    assert "[redacted]" in str(caught.value)

    OpenAICompatibleClient._raise_for_status(httpx.Response(200, request=request, json={}))
