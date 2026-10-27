"""run_sql_agent()'s own retry/self-repair loop and _log_query()'s audit-failure isolation, with fake
structured()/_execute() (no real Ollama/Postgres) - exercises the retry-continue branches that the
chat-pipeline tests (tests/test_state_isolation.py) don't happen to hit."""
import pytest

from app.agents import sql_agent


class _FakeStructured:
    """structured(SQLDraft).invoke(prompt) -> next canned draft, one per call."""

    def __init__(self, drafts):
        self._drafts = list(drafts)
        self.calls = 0

    def invoke(self, _prompt):
        self.calls += 1
        return sql_agent.SQLDraft(sql=self._drafts.pop(0))


@pytest.fixture
def no_real_logging(monkeypatch):
    """Most of these tests only care about the generate/validate/execute/retry loop, not the audit log -
    _log_query's own behavior (including that its failures never propagate) is tested separately below.
    Also stubs schema_prompt(): _prompt() calls it (for the system message) even when `structured` itself
    is faked, and the real one needs a live Postgres connection."""
    monkeypatch.setattr(sql_agent, "_log_query", lambda *a, **k: None)
    monkeypatch.setattr(sql_agent, "schema_prompt", lambda: "TABLE customers (id int)")


def test_run_sql_agent_succeeds_on_the_first_attempt(monkeypatch, no_real_logging):
    fake = _FakeStructured(["SELECT * FROM customers"])
    monkeypatch.setattr(sql_agent, "structured", lambda schema: fake)
    monkeypatch.setattr(sql_agent, "_execute", lambda sql: (["id", "name"], [[1, "a"]]))

    result = sql_agent.run_sql_agent("how many customers?")
    assert result.error is None and result.rows == [[1, "a"]] and fake.calls == 1


def test_run_sql_agent_retries_after_a_retryable_validation_error(monkeypatch, no_real_logging):
    # First draft references a table outside the allowlist (retryable: the model can fix this itself);
    # second draft is valid.
    fake = _FakeStructured(["SELECT * FROM secret_table", "SELECT * FROM customers"])
    monkeypatch.setattr(sql_agent, "structured", lambda schema: fake)
    monkeypatch.setattr(sql_agent, "_execute", lambda sql: (["id"], [[1]]))

    result = sql_agent.run_sql_agent("how many customers?")
    assert result.error is None and fake.calls == 2


def test_run_sql_agent_does_not_retry_a_non_retryable_validation_error(monkeypatch, no_real_logging):
    # The model itself (not the user's words - that path is_write_request() catches earlier, before the
    # model is ever consulted) hallucinates a DROP; the rejection is non-retryable, so no second attempt.
    fake = _FakeStructured(["DROP TABLE customers"])
    monkeypatch.setattr(sql_agent, "structured", lambda schema: fake)
    monkeypatch.setattr(sql_agent, "_execute", lambda sql: pytest.fail("must never execute"))

    result = sql_agent.run_sql_agent("who are our top customers?")
    assert result.error is not None and fake.calls == 1


def test_run_sql_agent_retries_after_a_database_error(monkeypatch, no_real_logging):
    import psycopg

    fake = _FakeStructured(["SELECT * FROM customers", "SELECT * FROM customers"])
    monkeypatch.setattr(sql_agent, "structured", lambda schema: fake)

    calls = []

    def flaky_execute(sql):
        calls.append(sql)
        if len(calls) == 1:
            raise psycopg.Error("connection reset")
        return ["id"], [[1]]

    monkeypatch.setattr(sql_agent, "_execute", flaky_execute)
    result = sql_agent.run_sql_agent("how many customers?")
    assert result.error is None and len(calls) == 2


def test_run_sql_agent_gives_up_after_max_attempts(monkeypatch, no_real_logging):
    import psycopg

    fake = _FakeStructured(["SELECT * FROM customers"] * 5)
    monkeypatch.setattr(sql_agent, "structured", lambda schema: fake)
    monkeypatch.setattr(sql_agent, "_execute", lambda sql: (_ for _ in ()).throw(psycopg.Error("down")))

    result = sql_agent.run_sql_agent("how many customers?", max_attempts=2)
    assert result.error is not None and fake.calls == 2


def test_log_query_failure_never_breaks_the_caller(monkeypatch):
    """_log_query is a best-effort audit write: if rw_pool is unreachable, the caller (run_sql_agent, on
    every attempt) must not see an exception from it."""
    class _BoomPool:
        def connection(self):
            raise ConnectionError("rw pool unreachable")

    monkeypatch.setattr(sql_agent, "rw_pool", lambda: _BoomPool())
    sql_agent._log_query("q", "SELECT 1", "ok", None, 1, 5)  # must not raise
