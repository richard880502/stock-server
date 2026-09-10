from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from typing import Protocol
from uuid import UUID

import httpx

from quant_signal.application.ports import QuantRepository
from quant_signal.application.services import (
    BacktestJobService,
    ChipFlowService,
    MarketEnvironmentService,
    SignalService,
)
from quant_signal.domain.models import (
    DebateAnalysisReport,
    DebateCase,
    JobStatus,
    LLMAnalysisContext,
    LLMAnalysisReport,
    LLMJudgement,
)
from quant_signal.settings import Settings

PROMPT_VERSION = "quant_analyst_v1"
DEBATE_PROMPT_VERSION = "debate_v1"

SYSTEM_PROMPT = """你是一位以風險管理為優先的量化市場分析師。
你會收到由確定性程式計算的市場寬度、資金環境、個股、異常與可選回測證據。

規則：
1. 只能使用輸入 JSON 內的證據，不得補造新聞、價格、基本面或即時資訊。
2. 清楚區分量化訊號、歷史回測與未來情境；不得把回測描述成保證。
3. 操作指南必須用條件式情境表達，指出確認條件與失效風險。
4. 若資料是 synthetic_demo、樣本不足或沒有回測，必須寫入 limitations。
5. 使用繁體中文。
6. 只輸出符合下列結構的 JSON，不要 Markdown：
{
  "headline": "一句話結論",
  "stance": "bullish|neutral|cautious|bearish",
  "operation_guide": "條件式操作指南",
  "key_evidence": ["證據"],
  "risk_warnings": ["風險"],
  "scenarios": [
    {"name": "情境名稱", "condition": "觸發條件", "response": "應對方式"}
  ],
  "limitations": ["限制"]
}
"""

BULL_SYSTEM_PROMPT = """你是投資研究團隊中負責提出「多方（看漲）論點」的研究員。
你會收到由確定性程式計算的市場寬度、資金環境、個股、異常與可選回測證據。

規則：
1. 只能使用輸入 JSON 內的證據，不得補造新聞、價格、基本面或即時資訊。
2. 你的立場固定為看漲，但論點必須誠實建立在證據之上，不得誇大或扭曲數字。
3. 必須列出你預期空方會提出的反駁重點，讓最終裁決者知道要檢視什麼。
4. 使用繁體中文。
5. 只輸出符合下列結構的 JSON，不要 Markdown：
{
  "stance": "bullish",
  "thesis": "一段多方論證",
  "key_evidence": ["支持看漲的具體證據"],
  "counterpoints_to_address": ["空方可能提出的反駁"]
}
"""

BEAR_SYSTEM_PROMPT = """你是投資研究團隊中負責提出「空方（看跌）論點」的研究員。
你會收到由確定性程式計算的市場寬度、資金環境、個股、異常與可選回測證據。

規則：
1. 只能使用輸入 JSON 內的證據，不得補造新聞、價格、基本面或即時資訊。
2. 你的立場固定為看跌，但論點必須誠實建立在證據之上，不得誇大或扭曲數字。
3. 必須列出你預期多方會提出的反駁重點，讓最終裁決者知道要檢視什麼。
4. 使用繁體中文。
5. 只輸出符合下列結構的 JSON，不要 Markdown：
{
  "stance": "bearish",
  "thesis": "一段空方論證",
  "key_evidence": ["支持看跌的具體證據"],
  "counterpoints_to_address": ["多方可能提出的反駁"]
}
"""

DEBATE_SYNTHESIS_PROMPT = """你是以風險管理為優先的量化市場分析師，同時是這場多空辯論的最終裁決者。
你會收到原始的量化證據，以及多方研究員與空方研究員各自提出的論證。

規則：
1. 只能使用輸入 JSON 內的證據（包含 bull_case 與 bear_case），不得補造新聞、價格、基本面或即時資訊。
2. 必須明確權衡兩方論點的證據強弱，不能各打五十大板、不能只是各方各給一半權重。
3. 清楚區分量化訊號、歷史回測與未來情境；不得把回測描述成保證。
4. 操作指南必須用條件式情境表達，指出確認條件與失效風險。
5. 若資料是 synthetic_demo、樣本不足或沒有回測，必須寫入 limitations。
6. 使用繁體中文。
7. 只輸出符合下列結構的 JSON，不要 Markdown：
{
  "headline": "一句話結論",
  "stance": "bullish|neutral|cautious|bearish",
  "operation_guide": "條件式操作指南",
  "key_evidence": ["證據"],
  "risk_warnings": ["風險"],
  "scenarios": [
    {"name": "情境名稱", "condition": "觸發條件", "response": "應對方式"}
  ],
  "limitations": ["限制"]
}
"""

STREAM_GUIDANCE_PROMPT = """你是一位以風險管理為優先的量化市場分析師。
你會收到由確定性程式計算的市場寬度、資金環境、個股、異常與可選回測證據。

請只撰寫一段繁體中文的「條件式操作指南」，供投資研究介面逐字顯示。
必須以輸入 JSON 的證據為準，不得補造新聞、價格、基本面或即時資訊；
清楚寫出可採取的動作、需要確認的條件與失效風險。若資料為 synthetic_demo、
樣本不足或缺少回測，務必在文中說明限制。不要輸出 Markdown、標題、JSON 或免責聲明以外的內容。
"""


class LLMClient(Protocol):
    @property
    def model_name(self) -> str: ...

    async def complete_json(self, system_prompt: str, payload: dict) -> dict: ...

    def stream_text(
        self,
        system_prompt: str,
        payload: dict,
    ) -> AsyncIterator[str]: ...


class OpenAICompatibleClient:
    """Small OpenAI-compatible client for hosted or local chat-completion servers."""

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str,
        temperature: float,
        max_tokens: int,
        timeout_seconds: float,
    ) -> None:
        self.base_url = base_url.strip().rstrip("/")
        self._model_name = model
        self.api_key = api_key
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout_seconds = timeout_seconds

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def completion_url(self) -> str:
        if self.base_url.endswith("/chat/completions"):
            return self.base_url
        return f"{self.base_url}/chat/completions"

    async def complete_json(self, system_prompt: str, payload: dict) -> dict:
        request_body = {
            "model": self._model_name,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "messages": [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": json.dumps(payload, ensure_ascii=False, sort_keys=True),
                },
            ],
            "response_format": {"type": "json_object"},
        }
        headers = {"Authorization": f"Bearer {self.api_key}"}
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(
                self.completion_url,
                headers=headers,
                json=request_body,
            )
            if response.status_code == 400:
                # Some local OpenAI-compatible servers do not implement JSON mode.
                request_body.pop("response_format", None)
                response = await client.post(
                    self.completion_url,
                    headers=headers,
                    json=request_body,
                )
            response.raise_for_status()
            data = response.json()
        content = data["choices"][0]["message"]["content"]
        return self._parse_json(content)

    async def stream_text(
        self,
        system_prompt: str,
        payload: dict,
    ) -> AsyncIterator[str]:
        request_body = {
            "model": self._model_name,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "stream": True,
            "messages": [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": json.dumps(payload, ensure_ascii=False, sort_keys=True),
                },
            ],
        }
        headers = {"Authorization": f"Bearer {self.api_key}"}
        timeout = httpx.Timeout(self.timeout_seconds, connect=20)
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream(
                "POST",
                self.completion_url,
                headers=headers,
                json=request_body,
            ) as response:
                response.raise_for_status()
                non_sse_lines: list[str] = []
                saw_sse = False
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        non_sse_lines.append(line)
                        continue
                    saw_sse = True
                    data = line.removeprefix("data:").strip()
                    if data == "[DONE]":
                        break
                    try:
                        event = json.loads(data)
                        delta = event["choices"][0].get("delta", {})
                        content = delta.get("content")
                    except (IndexError, KeyError, TypeError, json.JSONDecodeError):
                        continue
                    if isinstance(content, str) and content:
                        yield content
                if not saw_sse and non_sse_lines:
                    try:
                        response_data = json.loads("\n".join(non_sse_lines))
                        content = response_data["choices"][0]["message"]["content"]
                    except (IndexError, KeyError, TypeError, json.JSONDecodeError):
                        return
                    if isinstance(content, str) and content:
                        yield content

    @staticmethod
    def _parse_json(content: str | dict) -> dict:
        if isinstance(content, dict):
            return content
        text = content.strip()
        if text.startswith("```"):
            text = text.removeprefix("```json").removeprefix("```")
            text = text.removesuffix("```").strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            start = text.find("{")
            end = text.rfind("}")
            if start < 0 or end <= start:
                raise ValueError("LLM response did not contain a JSON object") from None
            return json.loads(text[start : end + 1])


def create_llm_client(settings: Settings) -> OpenAICompatibleClient:
    errors = settings.llm_errors()
    if errors:
        raise ValueError(f"LLM is not configured: {', '.join(errors)}")
    assert settings.llm_model is not None
    return OpenAICompatibleClient(
        base_url=settings.llm_base_url,
        model=settings.llm_model,
        api_key=settings.llm_api_key.get_secret_value(),
        temperature=settings.llm_temperature,
        max_tokens=settings.llm_max_tokens,
        timeout_seconds=settings.llm_timeout_seconds,
    )


class AnalysisContextService:
    def __init__(self, repository: QuantRepository) -> None:
        self.repository = repository

    async def build(
        self,
        *,
        symbol: str,
        as_of: date,
        market: str,
        benchmark: str | None,
        strategy_version: str,
        backtest_job_id: UUID | None = None,
    ) -> LLMAnalysisContext:
        signal_service = SignalService(self.repository)
        symbol_signal = await signal_service.analyze(
            symbol,
            as_of=as_of,
            benchmark=benchmark,
            strategy_version=strategy_version,
        )
        market_environment = await MarketEnvironmentService(self.repository).analyze(
            market,
            as_of=as_of,
            strategy_version="market_env_v1",
        )
        try:
            chip_flow = await ChipFlowService(self.repository).analyze(
                symbol,
                as_of=as_of,
            )
        except ValueError:
            chip_flow = None
        backtest = None
        if backtest_job_id:
            job = await BacktestJobService(self.repository).get(backtest_job_id)
            if job is None:
                raise LookupError("backtest job not found")
            if job.status != JobStatus.COMPLETED or job.result is None:
                raise ValueError("backtest job is not completed")
            backtest = job.result
        return LLMAnalysisContext(
            symbol_signal=symbol_signal,
            market_environment=market_environment,
            chip_flow=chip_flow,
            backtest=backtest,
        )


class LLMAnalysisService:
    def __init__(
        self,
        repository: QuantRepository,
        client: LLMClient,
    ) -> None:
        self.context_service = AnalysisContextService(repository)
        self.client = client

    async def analyze(
        self,
        *,
        symbol: str,
        as_of: date,
        market: str = "TW",
        benchmark: str | None = "^TWII",
        strategy_version: str = "regime_v1",
        backtest_job_id: UUID | None = None,
    ) -> LLMAnalysisReport:
        context = await self.context_service.build(
            symbol=symbol,
            as_of=as_of,
            market=market,
            benchmark=benchmark,
            strategy_version=strategy_version,
            backtest_job_id=backtest_job_id,
        )
        judgement_payload = await self.client.complete_json(
            SYSTEM_PROMPT,
            context.model_dump(mode="json"),
        )
        judgement = LLMJudgement.model_validate(judgement_payload)
        return LLMAnalysisReport(
            symbol=symbol,
            as_of=as_of,
            model=self.client.model_name,
            prompt_version=PROMPT_VERSION,
            generated_at=datetime.now(UTC),
            judgement=judgement,
            context=context,
        )

    async def stream_guidance(
        self,
        *,
        symbol: str,
        as_of: date,
        market: str = "TW",
        benchmark: str | None = "^TWII",
        strategy_version: str = "regime_v1",
        backtest_job_id: UUID | None = None,
    ) -> AsyncIterator[str]:
        context = await self.context_service.build(
            symbol=symbol,
            as_of=as_of,
            market=market,
            benchmark=benchmark,
            strategy_version=strategy_version,
            backtest_job_id=backtest_job_id,
        )
        async for token in self.client.stream_text(
            STREAM_GUIDANCE_PROMPT,
            context.model_dump(mode="json"),
        ):
            yield token


class DebateAnalysisService:
    def __init__(
        self,
        repository: QuantRepository,
        client: LLMClient,
    ) -> None:
        self.context_service = AnalysisContextService(repository)
        self.client = client

    async def analyze(
        self,
        *,
        symbol: str,
        as_of: date,
        market: str = "TW",
        benchmark: str | None = "^TWII",
        strategy_version: str = "regime_v1",
        backtest_job_id: UUID | None = None,
    ) -> DebateAnalysisReport:
        context = await self.context_service.build(
            symbol=symbol,
            as_of=as_of,
            market=market,
            benchmark=benchmark,
            strategy_version=strategy_version,
            backtest_job_id=backtest_job_id,
        )
        evidence_payload = context.model_dump(mode="json")
        bull_payload, bear_payload = await asyncio.gather(
            self.client.complete_json(BULL_SYSTEM_PROMPT, evidence_payload),
            self.client.complete_json(BEAR_SYSTEM_PROMPT, evidence_payload),
        )
        bull_case = DebateCase.model_validate(bull_payload)
        bear_case = DebateCase.model_validate(bear_payload)
        synthesis_payload = {
            **evidence_payload,
            "bull_case": bull_case.model_dump(mode="json"),
            "bear_case": bear_case.model_dump(mode="json"),
        }
        judgement_payload = await self.client.complete_json(
            DEBATE_SYNTHESIS_PROMPT,
            synthesis_payload,
        )
        judgement = LLMJudgement.model_validate(judgement_payload)
        return DebateAnalysisReport(
            symbol=symbol,
            as_of=as_of,
            model=self.client.model_name,
            prompt_version=DEBATE_PROMPT_VERSION,
            generated_at=datetime.now(UTC),
            bull_case=bull_case,
            bear_case=bear_case,
            judgement=judgement,
            context=context,
        )
