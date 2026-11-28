"""The ``paper-matcher`` task must survive a failing tick and back off, never end."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from quant_signal.workers import paper as worker


class FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class Harness:
    def __init__(self, monkeypatch, outcomes, *, in_session: bool = True) -> None:
        self.outcomes = list(outcomes)
        self.ticks = 0
        self.pauses: list[float] = []
        harness = self

        class FakeJobs:
            def __init__(self, **kwargs) -> None:
                pass

            async def tick(self, repo, now):
                harness.ticks += 1
                outcome = harness.outcomes.pop(0)
                if isinstance(outcome, BaseException):
                    raise outcome
                return outcome

        async def fake_sleep(seconds: float) -> None:
            harness.pauses.append(seconds)
            if not harness.outcomes:
                raise asyncio.CancelledError  # the test is done: end the task from outside

        monkeypatch.setattr(worker, "PaperJobs", FakeJobs)
        monkeypatch.setattr(worker, "session_factory", lambda: FakeSession())
        monkeypatch.setattr(
            worker,
            "asyncio",
            SimpleNamespace(sleep=fake_sleep, CancelledError=asyncio.CancelledError),
        )
        monkeypatch.setattr(worker, "matcher_open", lambda now: in_session)
        monkeypatch.setattr(
            "quant_signal.infrastructure.db.PostgresQuantRepository", lambda session: object()
        )
        self.settings = SimpleNamespace(
            quotes_enabled=False, paper_slippage_ticks=1, paper_match_seconds=3.0
        )


async def test_a_failing_tick_backs_off_and_the_task_keeps_running(monkeypatch) -> None:
    harness = Harness(monkeypatch, [RuntimeError("db hiccup"), None, ValueError("again"), None])
    with pytest.raises(asyncio.CancelledError):
        await worker.run_forever(harness.settings)
    assert harness.ticks == 4  # every failure was followed by another tick
    assert harness.pauses == [
        worker.FAILURE_BACKOFF_SECONDS,
        3.0,
        worker.FAILURE_BACKOFF_SECONDS,
        3.0,
    ]


async def test_outside_the_session_the_matcher_idles_slowly(monkeypatch) -> None:
    harness = Harness(monkeypatch, [None, None], in_session=False)
    with pytest.raises(asyncio.CancelledError):
        await worker.run_forever(harness.settings)
    assert harness.pauses == [worker.IDLE_SECONDS, worker.IDLE_SECONDS]


async def test_cancellation_is_not_swallowed(monkeypatch) -> None:
    harness = Harness(monkeypatch, [asyncio.CancelledError(), None])
    with pytest.raises(asyncio.CancelledError):
        await worker.run_forever(harness.settings)
    assert harness.ticks == 1 and harness.pauses == []


async def test_a_session_factory_failure_also_backs_off(monkeypatch) -> None:
    harness = Harness(monkeypatch, [None])
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            raise ConnectionError("db down")
        return FakeSession()

    monkeypatch.setattr(worker, "session_factory", flaky)
    harness.outcomes.insert(0, None)  # the first (failed) pass never reaches tick
    with pytest.raises(asyncio.CancelledError):
        await worker.run_forever(harness.settings)
    assert harness.pauses[0] == worker.FAILURE_BACKOFF_SECONDS and harness.ticks >= 1
