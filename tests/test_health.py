"""GET /health and GET /health/live: all three dependency probes (ok and failing), the aggregate
status/HTTP code, and that the response carries the current auto-ingest status."""
import httpx
import pytest
from fastapi.testclient import TestClient

from app import health as health_module
from app import main, starter


class _OkStore:
    def ping(self):
        pass


class _DownStore:
    def ping(self):
        raise ConnectionError("qdrant unreachable")


class _FakePgConnCtx:
    def __init__(self, fail=False):
        self._fail = fail

    def __enter__(self):
        if self._fail:
            raise ConnectionError("postgres unreachable")
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, *a, **k):
        pass


class _FakePool:
    def __init__(self, fail=False):
        self._fail = fail

    def connection(self, timeout=None):
        return _FakePgConnCtx(self._fail)


@pytest.fixture
def client():
    c = TestClient(main.app)
    yield c
    main.app.dependency_overrides.clear()


def test_live_never_touches_any_dependency(client, monkeypatch):
    def boom():
        raise AssertionError("should never be called")

    monkeypatch.setattr(health_module, "ro_pool", boom)
    assert client.get("/health/live").json() == {"status": "ok"}


def test_health_ok_when_all_three_dependencies_are_reachable(client, monkeypatch):
    main.app.dependency_overrides[main.get_store] = lambda: _OkStore()
    monkeypatch.setattr(health_module, "ro_pool", lambda: _FakePool(fail=False))
    monkeypatch.setattr(health_module.httpx, "get", lambda *a, **k: httpx.Response(200, request=httpx.Request("GET", "http://x")))
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["services"] == {"qdrant": "ok", "ollama": "ok", "postgres": "ok"}


@pytest.mark.parametrize("break_qdrant,break_ollama,break_postgres", [
    (True, False, False),
    (False, True, False),
    (False, False, True),
])
def test_health_degrades_to_503_when_any_one_dependency_is_down(client, monkeypatch, break_qdrant, break_ollama, break_postgres):
    main.app.dependency_overrides[main.get_store] = lambda: (_DownStore() if break_qdrant else _OkStore())
    monkeypatch.setattr(health_module, "ro_pool", lambda: _FakePool(fail=break_postgres))

    def fake_get(*a, **k):
        if break_ollama:
            raise httpx.ConnectError("ollama unreachable")
        return httpx.Response(200, request=httpx.Request("GET", "http://x"))

    monkeypatch.setattr(health_module.httpx, "get", fake_get)
    r = client.get("/health")
    assert r.status_code == 503
    body = r.json()
    assert body["status"] == "degraded"
    assert (body["services"]["qdrant"] == "ok") != break_qdrant
    assert (body["services"]["ollama"] == "ok") != break_ollama
    assert (body["services"]["postgres"] == "ok") != break_postgres


def test_health_reports_the_current_auto_ingest_status(client, monkeypatch):
    main.app.dependency_overrides[main.get_store] = lambda: _OkStore()
    monkeypatch.setattr(health_module, "ro_pool", lambda: _FakePool(fail=False))
    monkeypatch.setattr(health_module.httpx, "get", lambda *a, **k: httpx.Response(200, request=httpx.Request("GET", "http://x")))
    original = starter.ingest_status
    starter.ingest_status = "running"
    try:
        assert client.get("/health").json()["ingest"] == "running"
    finally:
        starter.ingest_status = original
