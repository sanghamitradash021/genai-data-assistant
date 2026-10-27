"""app/starter.py: auto-ingest status transitions, the query_log retention loop, and that lifespan()
wires both background threads (and shuts them down) based on settings - without ever starting a real
thread or touching a real Postgres/Qdrant."""
import asyncio

import pytest

from app import starter


@pytest.fixture(autouse=True)
def _reset_module_state():
    saved = {k: getattr(starter.settings, k) for k in
             ("auto_ingest", "query_log_retention_days", "query_log_prune_interval_s")}
    starter.ingest_status = "disabled"
    starter._prune_stop.clear()
    yield
    starter.ingest_status = "disabled"
    starter._prune_stop.clear()
    for k, v in saved.items():
        object.__setattr__(starter.settings, k, v)


def test_auto_ingest_success_sets_status_running_then_done(monkeypatch):
    class Result:
        ingested, skipped, errors = [1], [], {}

    seen_status_during_call = []

    def fake_ingest_directory(store):
        seen_status_during_call.append(starter.ingest_status)
        return Result()

    monkeypatch.setattr(starter, "ingest_directory", fake_ingest_directory)
    monkeypatch.setattr(starter, "get_store", lambda: object())
    starter.ingest_status = "pending"
    starter._auto_ingest()
    assert seen_status_during_call == ["running"]
    assert starter.ingest_status == "done"


def test_auto_ingest_failure_sets_status_failed_without_raising(monkeypatch):
    def boom(store):
        raise ConnectionError("qdrant down")

    monkeypatch.setattr(starter, "ingest_directory", boom)
    monkeypatch.setattr(starter, "get_store", lambda: object())
    starter._auto_ingest()  # must not raise - it's run in a daemon thread with nothing to catch it
    assert starter.ingest_status == "failed"


def test_prune_loop_runs_once_then_stops_on_the_stop_event(monkeypatch):
    calls = []

    def fake_prune():
        calls.append(1)
        starter._prune_stop.set()  # simulate shutdown happening right after the first pass
        return 3

    monkeypatch.setattr("app.agents.sql_agent.prune_query_log", fake_prune)
    object.__setattr__(starter.settings, "query_log_prune_interval_s", 0.01)
    starter._prune_loop()  # blocking, but returns immediately once the stop event is set
    assert calls == [1]


def test_prune_loop_survives_a_failed_prune_and_keeps_going(monkeypatch):
    calls = []

    def flaky_prune():
        calls.append(1)
        if len(calls) == 1:
            raise ConnectionError("postgres down")
        starter._prune_stop.set()
        return 0

    monkeypatch.setattr("app.agents.sql_agent.prune_query_log", flaky_prune)
    object.__setattr__(starter.settings, "query_log_prune_interval_s", 0.01)
    starter._prune_loop()
    assert len(calls) == 2  # the first failure didn't kill the loop


def test_lifespan_starts_both_background_threads_when_enabled(monkeypatch):
    started = []
    monkeypatch.setattr(starter.threading, "Thread",
                         lambda target, daemon: started.append(target) or _FakeThread())
    monkeypatch.setattr(starter, "close_pools", lambda: started.append("close_pools"))
    object.__setattr__(starter.settings, "auto_ingest", True)
    object.__setattr__(starter.settings, "query_log_retention_days", 30)

    async def run():
        async with starter.lifespan(None):
            assert starter.ingest_status == "pending"
            assert started == [starter._auto_ingest, starter._prune_loop]

    asyncio.run(run())
    assert started[-1] == "close_pools"
    assert starter._prune_stop.is_set()


def test_lifespan_skips_both_threads_when_disabled(monkeypatch):
    started = []
    monkeypatch.setattr(starter.threading, "Thread",
                         lambda target, daemon: started.append(target) or _FakeThread())
    monkeypatch.setattr(starter, "close_pools", lambda: None)
    object.__setattr__(starter.settings, "auto_ingest", False)
    object.__setattr__(starter.settings, "query_log_retention_days", 0)

    async def run():
        async with starter.lifespan(None):
            assert starter.ingest_status == "disabled"
            assert started == []

    asyncio.run(run())


class _FakeThread:
    def start(self):
        pass
