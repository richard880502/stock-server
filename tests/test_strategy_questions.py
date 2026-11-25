from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

from quant_signal.adapters.rest import main, security
from quant_signal.application.auth import ApiKeyService
from quant_signal.infrastructure.memory import MemoryQuantRepository
from quant_signal.settings import get_settings


def test_strategy_question_auth_validation_and_payload(monkeypatch) -> None:
    repository = MemoryQuantRepository()
    async def override_repository():
        yield repository

    class FakeClient:
        async def complete_json(self, prompt, payload):
            calls.append((prompt, payload))
            return {"answer": "依據報告，請留意風險。"}

    calls = []
    monkeypatch.setattr(main, "create_llm_client", lambda settings: FakeClient())
    settings = get_settings()
    monkeypatch.setattr(settings, "api_auth_enabled", True)
    monkeypatch.setattr(type(settings), "llm_errors", lambda self: [])
    main.app.dependency_overrides[security._get_repository] = override_repository
    report = {"symbol": "2330", "as_of": "2025-01-02", "evidence": [{"id": "e1"}],
              "judgement": {"headline": "觀望"}, "context": {"large": "excluded"}}
    body = {"report": report, "question": "該怎麼做？", "held": True,
            "history": [{"question": "為什麼？", "answer": "風險較高。"}]}
    try:
        key, _ = asyncio.run(ApiKeyService(repository).create("test"))
        with TestClient(main.app) as client:
            url = "/api/v1/debate-analyses/questions"
            assert client.post(url, json=body).status_code == 401
            headers = {"Authorization": f"Bearer {key}"}
            response = client.post(url, json=body, headers=headers)
            assert response.status_code == 200
            assert response.json() == {"answer": "依據報告，請留意風險。"}
            prompt, payload = calls[0]
            assert "歷史問答僅供理解對話" in prompt
            assert payload["report"]["as_of"] == "2025-01-02"
            assert "context" not in payload["report"]
            assert payload["held"] is True and payload["history"] == body["history"]
            for invalid in (
                {**body, "question": "  "},
                {**body, "question": "x" * 1001},
                {**body, "report": {"as_of": "invalid"}},
                {**body, "report": {"as_of": "2025-01-02", "evidence": "x" * 40001}},
                {**body, "history": body["history"] * 3},
            ):
                assert client.post(url, json=invalid, headers=headers).status_code == 422
            assert len(calls) == 1
    finally:
        main.app.dependency_overrides.clear()
