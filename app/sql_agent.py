"""Text-to-SQL agent: schema-aware prompt -> structured SQL -> validate -> read-only execute -> log."""
import logging
import time
from datetime import date, datetime
from decimal import Decimal
from functools import lru_cache

import psycopg
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from .config import settings
from .llm import structured
from .models import SQLResult
from .security import is_write_request
from .sql_validator import ALLOWED_TABLES, SQLValidationError, validate_sql

log = logging.getLogger(__name__)

_BUSINESS_RULES = """\
Business rules:
- Revenue = SUM(orders.total_amount) over orders with status = 'completed' (ALWAYS add that filter).
- orders.total_amount is already the whole-order total: NEVER join order_items when summing it (double counting).
  Only join order_items/products for product-level questions, using SUM(quantity * unit_price).
- orders.status is one of: completed, pending, cancelled, refunded.
- Money refunded = SUM(refunds.amount); the date of a refund is refunds.refunded_at (NOT orders.order_date).
- "Last month" = the previous calendar month: refunded_at >= date_trunc('month', CURRENT_DATE) - INTERVAL '1 month'
  AND refunded_at < date_trunc('month', CURRENT_DATE).
- order_items.unit_price * order_items.quantity = line revenue.
- customers.segment is one of: consumer, small_business, enterprise.
Relationships: orders.customer_id -> customers.id; order_items.order_id -> orders.id;
order_items.product_id -> products.id; refunds.order_id -> orders.id."""

_EXAMPLES = """\
Q: Which are the top 5 customers by revenue?
SQL: SELECT c.name, SUM(o.total_amount) AS revenue FROM customers c JOIN orders o ON o.customer_id = c.id WHERE o.status = 'completed' GROUP BY c.id, c.name ORDER BY revenue DESC LIMIT 5

Q: How much was refunded last month?
SQL: SELECT COALESCE(SUM(amount), 0) AS total_refunded FROM refunds WHERE refunded_at >= date_trunc('month', CURRENT_DATE) - INTERVAL '1 month' AND refunded_at < date_trunc('month', CURRENT_DATE)"""


class SQLDraft(BaseModel):
    sql: str = Field(description="One PostgreSQL SELECT statement, no trailing explanation")


@lru_cache
def schema_prompt() -> str:
    """Introspect the live schema (only tables the read-only role may see)."""
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
