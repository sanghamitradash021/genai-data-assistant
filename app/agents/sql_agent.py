"""Text-to-SQL agent: schema-aware prompt -> structured SQL -> validate -> read-only execute -> log."""
import logging
import time
from datetime import date, datetime
from decimal import Decimal
from functools import lru_cache

import psycopg
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from app.config.settings import settings
from app.exceptions.sql import SQLValidationError
from app.llms.ollama_client import structured
from app.models.sql import SQLResult
from app.prompts.sql_prompt import BUSINESS_RULES as _BUSINESS_RULES
from app.prompts.sql_prompt import SQL_EXAMPLES as _EXAMPLES
from app.tools.sql_validator import ALLOWED_TABLES, validate_sql
from app.utils.security import is_write_request

log = logging.getLogger(__name__)


class SQLDraft(BaseModel):
    sql: str = Field(description="One PostgreSQL SELECT statement, no trailing explanation")


@lru_cache
def schema_prompt() -> str:
    """Introspect the live schema (only tables the read-only role may see).

    Cached for the lifetime of the process: a schema migration applied after startup will not be
    picked up without a restart (or schema_prompt.cache_clear()). Acceptable for this project's
    static demo schema; revisit if migrations are ever applied against a running instance.
    """
    with psycopg.connect(settings.database_url_ro, connect_timeout=5) as conn:
        rows = conn.execute(
            """SELECT table_name, column_name, data_type FROM information_schema.columns
               WHERE table_schema = 'public' AND table_name = ANY(%s)
               ORDER BY table_name, ordinal_position""",
            (sorted(ALLOWED_TABLES),),
        ).fetchall()
    tables: dict[str, list[str]] = {}
    for t, c, dt in rows:
        tables.setdefault(t, []).append(f"{c} {dt}")
    ddl = "\n".join(f"TABLE {t} ({', '.join(cols)})" for t, cols in tables.items())
    return f"{ddl}\n\n{_BUSINESS_RULES}"


def _prompt(question: str, prev_sql: str | None, error: str | None) -> list:
    system = (
        "You translate questions into a single read-only PostgreSQL SELECT query.\n"
        f"Today's date is {date.today().isoformat()}.\n"
        "Use ONLY these tables and columns:\n\n"
        f"{schema_prompt()}\n\n"
        "Rules: output one SELECT (CTEs allowed); never modify data; never reference other tables or "
        "schemas; alias aggregates with readable names; prefer explicit JOINs.\n\n"
        f"Examples:\n{_EXAMPLES}"
    )
    human = f"Question: {question}"
    if prev_sql and error:
        human += f"\n\nYour previous SQL failed.\nSQL: {prev_sql}\nError: {error}\nReturn a corrected query."
    return [SystemMessage(system), HumanMessage(human)]


def _jsonable(v):
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    return v


def _execute(sql: str) -> tuple[list[str], list[list]]:
    with psycopg.connect(settings.database_url_ro, connect_timeout=5) as conn:
        conn.read_only = True
        with conn.cursor() as cur:
            cur.execute(f"SET LOCAL statement_timeout = {int(settings.sql_timeout_ms)}")
            cur.execute(sql)
            cols = [d.name for d in cur.description]
            rows = [[_jsonable(v) for v in r] for r in cur.fetchmany(settings.sql_max_rows)]
    return cols, rows


def _log_query(question, sql, status, error, row_count, duration_ms) -> None:
    log.info("sql_query status=%s ms=%s rows=%s sql=%s err=%s", status, duration_ms, row_count, sql, error)
    try:
        with psycopg.connect(settings.database_url_rw, connect_timeout=5) as conn:
            conn.execute(
                """INSERT INTO query_log (question, generated_sql, status, error, row_count, duration_ms)
                   VALUES (%s, %s, %s, %s, %s, %s)""",
                (question, sql, status, error, row_count, duration_ms),
            )
    except Exception:  # audit failure must never break the user's request
        log.exception("Could not persist query_log row")


def run_sql_agent(question: str, max_attempts: int = 2) -> SQLResult:
    result = SQLResult(question=question)
    if is_write_request(question):  # decided on the user's words; the model is never asked
        msg = "Only SELECT statements are allowed."
        _log_query(question, None, "rejected", msg, None, 0)
        result.error = f"Could not produce a valid query: {msg}"
        return result
    prev_sql = error = None
    for _ in range(max_attempts):
        start = time.monotonic()
        raw_sql = None
        try:
            raw_sql = structured(SQLDraft).invoke(_prompt(question, prev_sql, error)).sql
            sql = validate_sql(raw_sql, max_rows=settings.sql_max_rows)
            result.sql = sql
            cols, rows = _execute(sql)
        except SQLValidationError as e:
            error, prev_sql = str(e), raw_sql
            _log_query(question, raw_sql, "rejected", error, None, int((time.monotonic() - start) * 1000))
            if not e.retryable:
                break
            continue
        except psycopg.Error as e:
            error, prev_sql = str(e).splitlines()[0], result.sql
            _log_query(question, result.sql, "error", error, None, int((time.monotonic() - start) * 1000))
            continue
        _log_query(question, sql, "ok", None, len(rows), int((time.monotonic() - start) * 1000))
        result.columns, result.rows, result.error = cols, rows, None
        return result
    result.error = f"Could not produce a valid query: {error}"
    return result
