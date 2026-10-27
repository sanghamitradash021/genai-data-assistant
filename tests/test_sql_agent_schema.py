"""Unit tests for sql_agent.py's schema introspection: FK/CHECK/sample-value extraction (against fake
connections - no real Postgres needed) and the TTL-based schema_prompt() cache."""
import pytest

from app.agents import sql_agent


class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _FakeConn:
    """Returns one canned row-set per execute() call, in order; records every call made."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def execute(self, query, params=None):
        self.calls.append((query, params))
        return _FakeCursor(self._responses.pop(0))


@pytest.fixture(autouse=True)
def _clear_schema_cache():
    sql_agent.schema_prompt.cache_clear()
    original_ttl = sql_agent.settings.schema_cache_ttl_s
    yield
    sql_agent.schema_prompt.cache_clear()
    object.__setattr__(sql_agent.settings, "schema_cache_ttl_s", original_ttl)


def test_foreign_keys_formats_table_column_arrows():
    conn = _FakeConn([[
        ("orders", "customer_id", "customers", "id"),
        ("order_items", "order_id", "orders", "id"),
    ]])
    assert sql_agent._foreign_keys(conn, ["orders", "order_items"]) == [
        "orders.customer_id -> customers.id",
        "order_items.order_id -> orders.id",
    ]


def test_check_constraints_returns_table_definition_pairs():
    conn = _FakeConn([[("orders", "CHECK ((status = ANY (ARRAY['completed'::text])))")]])
    assert sql_agent._check_constraints(conn, ["orders"]) == [
        ("orders", "CHECK ((status = ANY (ARRAY['completed'::text])))")
    ]


def test_sample_values_skips_high_cardinality_and_check_covered_columns():
    cols = [
        ("products", "category", "text"),      # low cardinality -> sampled
        ("products", "price", "numeric"),       # not text -> skipped, no query
        ("customers", "email", "text"),         # high cardinality -> queried, then rejected
        ("orders", "status", "text"),           # already covered by a CHECK -> skipped, no query
    ]
    checks = [("orders", "CHECK ((status = ANY (ARRAY['completed'::text])))")]
    conn = _FakeConn([
        [("Accessories",), ("Audio",)],                                   # category: 2 values
        [(f"user{i}@x.com",) for i in range(sql_agent._MAX_DISTINCT_SAMPLED + 1)],  # email: too many
    ])
    samples = sql_agent._sample_values(conn, cols, checks)
    assert samples == {("products", "category"): ["Accessories", "Audio"]}
    assert len(conn.calls) == 2  # price and status never even queried


class _Ctx:
    """Wraps a plain fake connection as the context manager `with ro_pool().connection() as conn:` expects."""

    def __init__(self, conn):
        self._conn = conn

    def __enter__(self):
        return self._conn

    def __exit__(self, *exc):
        return False


def test_build_schema_prompt_assembles_columns_fks_checks_and_business_rules(monkeypatch):
    conn = _FakeConn([[("orders", "status", "text"), ("orders", "total_amount", "numeric")]])
    monkeypatch.setattr(sql_agent, "ro_pool", lambda: type("P", (), {"connection": lambda self: _Ctx(conn)})())
    monkeypatch.setattr(sql_agent, "_foreign_keys", lambda c, t: ["orders.customer_id -> customers.id"])
    monkeypatch.setattr(sql_agent, "_check_constraints", lambda c, t: [("orders", "CHECK ((status = ANY (...)))")])
    monkeypatch.setattr(sql_agent, "_sample_values", lambda c, cols, checks: {("orders", "status"): ["completed"]})

    text = sql_agent._build_schema_prompt()
    assert "TABLE orders (status text (e.g. completed), total_amount numeric)" in text
    assert "Foreign keys:\norders.customer_id -> customers.id" in text
    assert "Check constraints:\norders: CHECK" in text
    assert "Business rules:" in text  # from app/prompts/sql_prompt.py


def test_schema_prompt_is_cached_within_the_ttl(monkeypatch):
    calls = []
    monkeypatch.setattr(sql_agent, "_build_schema_prompt", lambda: calls.append(1) or f"schema v{len(calls)}")
    object.__setattr__(sql_agent.settings, "schema_cache_ttl_s", 100.0)

    now = [1000.0]
    monkeypatch.setattr(sql_agent.time, "monotonic", lambda: now[0])

    first = sql_agent.schema_prompt()
    assert first == "schema v1"
    now[0] += 10  # still within the TTL
    assert sql_agent.schema_prompt() == "schema v1"
    assert len(calls) == 1


def test_schema_prompt_refreshes_after_the_ttl_expires(monkeypatch):
    calls = []
    monkeypatch.setattr(sql_agent, "_build_schema_prompt", lambda: calls.append(1) or f"schema v{len(calls)}")
    object.__setattr__(sql_agent.settings, "schema_cache_ttl_s", 5.0)

    now = [1000.0]
    monkeypatch.setattr(sql_agent.time, "monotonic", lambda: now[0])

    assert sql_agent.schema_prompt() == "schema v1"
    now[0] += 10  # past the TTL
    assert sql_agent.schema_prompt() == "schema v2"
    assert len(calls) == 2


def test_schema_prompt_cache_clear_forces_an_immediate_refresh(monkeypatch):
    calls = []
    monkeypatch.setattr(sql_agent, "_build_schema_prompt", lambda: calls.append(1) or f"schema v{len(calls)}")
    object.__setattr__(sql_agent.settings, "schema_cache_ttl_s", 10_000.0)

    assert sql_agent.schema_prompt() == "schema v1"
    sql_agent.schema_prompt.cache_clear()
    assert sql_agent.schema_prompt() == "schema v2"


# --- query_log: session_id plumbing and retention pruning -----------------------------------------------
class _FakePoolConn:
    """Stand-in for `with rw_pool().connection() as conn: conn.execute(...)`."""

    def __init__(self, rowcount=0):
        self.calls = []
        self._rowcount = rowcount

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, query, params=None):
        self.calls.append((query, params))
        self.rowcount = self._rowcount
        return self


def test_log_query_persists_session_id(monkeypatch):
    conn = _FakePoolConn()
    monkeypatch.setattr(sql_agent, "rw_pool", lambda: type("P", (), {"connection": lambda self: conn})())
    sql_agent._log_query("q", "SELECT 1", "ok", None, 1, 5, session_id="abc123")
    query, params = conn.calls[0]
    assert "session_id" in query and params[0] == "abc123"


def test_run_sql_agent_forwards_session_id_to_the_audit_log(monkeypatch):
    logged = []
    monkeypatch.setattr(sql_agent, "_log_query", lambda *a: logged.append(a))
    sql_agent.run_sql_agent("drop table customers", session_id="sess-1")
    # is_write_request() short-circuits before any model/DB call, but the rejection must still be logged
    # against the right session - session_id is always the last positional arg to _log_query.
    assert logged and logged[0][-1] == "sess-1"


def test_prune_query_log_deletes_older_than_retention_and_returns_the_count(monkeypatch):
    conn = _FakePoolConn(rowcount=7)
    monkeypatch.setattr(sql_agent, "rw_pool", lambda: type("P", (), {"connection": lambda self: conn})())
    object.__setattr__(sql_agent.settings, "query_log_retention_days", 30)
    assert sql_agent.prune_query_log() == 7
    query, params = conn.calls[0]
    assert "DELETE FROM query_log" in query and "asked_at" in query and params == (30,)
