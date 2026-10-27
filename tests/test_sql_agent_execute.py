"""sql_agent._execute()/_jsonable(): row fetching and JSON-safe value conversion, against a fake
connection/cursor (no real Postgres) - this code path is normally bypassed entirely in the chat-pipeline
tests, which monkeypatch _execute() itself."""
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

import pytest

from app.agents import sql_agent


@pytest.fixture(autouse=True)
def _restore_settings():
    saved = {k: getattr(sql_agent.settings, k) for k in ("sql_timeout_ms", "sql_max_rows")}
    yield
    for k, v in saved.items():
        object.__setattr__(sql_agent.settings, k, v)


@dataclass
class _ColDesc:
    name: str


class _FakeCursor:
    def __init__(self, cols, rows):
        self._cols = cols
        self._rows = rows
        self.executed = []
        self.description = None

    def execute(self, sql):
        self.executed.append(sql)
        self.description = [_ColDesc(c) for c in self._cols]

    def fetchmany(self, n):
        return self._rows[:n]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeConn:
    def __init__(self, cursor):
        self._cursor = cursor
        self.read_only = False

    def cursor(self):
        return self._cursor

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakePool:
    def __init__(self, conn):
        self._conn = conn

    def connection(self):
        return self._conn


def test_execute_sets_statement_timeout_then_runs_the_query(monkeypatch):
    cur = _FakeCursor(["id", "name"], [[1, "a"], [2, "b"]])
    monkeypatch.setattr(sql_agent, "ro_pool", lambda: _FakePool(_FakeConn(cur)))
    object.__setattr__(sql_agent.settings, "sql_timeout_ms", 5000)

    cols, rows = sql_agent._execute("SELECT id, name FROM customers")
    assert cols == ["id", "name"]
    assert rows == [[1, "a"], [2, "b"]]
    assert "statement_timeout = 5000" in cur.executed[0]
    assert cur.executed[1] == "SELECT id, name FROM customers"


def test_execute_caps_rows_at_sql_max_rows(monkeypatch):
    cur = _FakeCursor(["n"], [[i] for i in range(10)])
    monkeypatch.setattr(sql_agent, "ro_pool", lambda: _FakePool(_FakeConn(cur)))
    object.__setattr__(sql_agent.settings, "sql_max_rows", 3)

    _, rows = sql_agent._execute("SELECT n FROM t")
    assert rows == [[0], [1], [2]]


def test_execute_converts_decimal_and_date_values_to_json_safe_types(monkeypatch):
    cur = _FakeCursor(["amount", "d", "ts"], [[Decimal("12.50"), date(2026, 1, 1), datetime(2026, 1, 1, 9, 30)]])
    monkeypatch.setattr(sql_agent, "ro_pool", lambda: _FakePool(_FakeConn(cur)))

    _, rows = sql_agent._execute("SELECT amount, d, ts FROM refunds")
    assert rows == [[12.5, "2026-01-01", "2026-01-01T09:30:00"]]
    assert isinstance(rows[0][0], float)


def test_execute_sets_the_connection_read_only(monkeypatch):
    cur = _FakeCursor(["n"], [[1]])
    conn = _FakeConn(cur)
    monkeypatch.setattr(sql_agent, "ro_pool", lambda: _FakePool(conn))
    sql_agent._execute("SELECT 1")
    assert conn.read_only is True


def test_execute_raises_on_a_statement_with_no_resultset(monkeypatch):
    class _NoDescriptionCursor(_FakeCursor):
        def execute(self, sql):
            self.executed.append(sql)
            self.description = None  # e.g. a statement that returns no rows at all

    cur = _NoDescriptionCursor(["n"], [])
    monkeypatch.setattr(sql_agent, "ro_pool", lambda: _FakePool(_FakeConn(cur)))
    with pytest.raises(sql_agent.psycopg.Error):
        sql_agent._execute("SELECT 1")
