from __future__ import annotations

from quant_signal.application.llm_analysis import (
    DebateAnalysisService,
    LLMAnalysisService,
    OpenAICompatibleClient,
)
from quant_signal.domain.models import Bar, MarketObservation
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
    assert report.judgement.stance == "bullish"
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
