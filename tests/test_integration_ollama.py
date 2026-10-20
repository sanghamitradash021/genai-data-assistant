"""Opt-in integration test against a real Ollama model and the seeded Postgres schema.

Skipped by default: the rest of the suite verifies the SQL *validator* rejects bad SQL in
isolation (tests/test_sql_validator.py) using hand-written queries, never that the real local
model's own output is good SQL in the first place. This closes that gap, but needs live services
(`docker compose up`) and is opt-in via RUN_INTEGRATION=1 so normal `pytest`/CI runs stay fast and
offline.
"""
import os

import httpx
import psycopg
import pytest

from app.agents.sql_agent import run_sql_agent
from app.config.settings import settings

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_INTEGRATION") != "1",
    reason="opt-in: set RUN_INTEGRATION=1 with `docker compose up` running to exercise a real model",
)


def _service_up(check) -> bool:
    try:
        check()
        return True
    except Exception:
        return False


@pytest.fixture(scope="module", autouse=True)
def _require_live_services():
    ollama_up = _service_up(lambda: httpx.get(f"{settings.ollama_base_url}/api/tags", timeout=3).raise_for_status())
    db_up = _service_up(lambda: psycopg.connect(settings.database_url_ro, connect_timeout=3).close())
    if not (ollama_up and db_up):
        pytest.skip("Ollama and/or Postgres not reachable; run `docker compose up` first")


@pytest.mark.parametrize("question", [
    "Which are the top 5 customers by revenue?",
    "How many orders were placed last month?",
    "What is the average order value by country?",
])
def test_real_model_produces_valid_sql(question):
    result = run_sql_agent(question)
    assert result.error is None, f"SQL agent failed for {question!r}: {result.error}"
    assert result.sql is not None
    assert result.columns
