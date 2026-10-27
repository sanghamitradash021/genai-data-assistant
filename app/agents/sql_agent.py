"""Text-to-SQL agent: schema-aware prompt -> structured SQL -> validate -> read-only execute -> log."""
import logging
import time
from datetime import date, datetime
from decimal import Decimal
from typing import cast

import psycopg
from langchain_core.messages import HumanMessage, SystemMessage
from psycopg import sql as pg_sql
from pydantic import BaseModel, Field
from typing_extensions import LiteralString

from app.config.settings import settings
from app.exceptions.sql import SQLValidationError
from app.llms.ollama_client import structured
from app.models.sql import SQLResult
from app.prompts.sql_prompt import BUSINESS_RULES as _BUSINESS_RULES
from app.prompts.sql_prompt import SQL_EXAMPLES as _EXAMPLES
from app.repository.db import ro_pool, rw_pool
from app.tools.sql_validator import ALLOWED_TABLES, validate_sql
from app.utils.security import is_write_request

log = logging.getLogger(__name__)

_TEXT_TYPES = ("text", "character varying")
_MAX_DISTINCT_SAMPLED = 12  # a text column with more distinct values than this isn't a category, skip it
_MAX_SAMPLES_SHOWN = 8


class SQLDraft(BaseModel):
    sql: str = Field(description="One PostgreSQL SELECT statement, no trailing explanation")


def _foreign_keys(conn, tables: list[str]) -> list[str]:
    """pg_catalog, not information_schema.constraint_column_usage/key_column_usage: those information_schema
    views only surface rows a role can see based on ownership, not plain SELECT privilege - assistant_ro
    (owns nothing; SELECT-granted only) gets 0 rows from them even though it can see the tables themselves.
    pg_constraint/pg_attribute don't have that restriction."""
    rows = conn.execute(
        """SELECT conrelid::regclass::text, a.attname, confrelid::regclass::text, af.attname
           FROM pg_constraint c
           JOIN unnest(c.conkey) WITH ORDINALITY AS ck(attnum, ord) ON true
           JOIN unnest(c.confkey) WITH ORDINALITY AS cfk(attnum, ord) ON cfk.ord = ck.ord
           JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = ck.attnum
           JOIN pg_attribute af ON af.attrelid = c.confrelid AND af.attnum = cfk.attnum
           WHERE c.contype = 'f' AND c.conrelid = ANY(%s::regclass[])
           ORDER BY 1, 2""",
        ([f"public.{t}" for t in tables],),
    ).fetchall()
    return [f"{t}.{c} -> {rt}.{rc}" for t, c, rt, rc in rows]


def _check_constraints(conn, tables: list[str]) -> list[tuple[str, str]]:
    """(table, constraint definition) for every CHECK on an allowed table - this is where an enum like
    orders.status's allowed values actually lives; read it from Postgres instead of hand-copying it into
    BUSINESS_RULES prose, which can drift from the real constraint after a migration."""
    rows = conn.execute(
        """SELECT conrelid::regclass::text, pg_get_constraintdef(oid)
           FROM pg_constraint WHERE contype = 'c' AND conrelid = ANY(%s::regclass[])""",
        ([f"public.{t}" for t in tables],),
    ).fetchall()
    return list(rows)

def _sample_values(conn, cols: list[tuple[str, str, str]], checks: list[tuple[str, str]]) -> dict[tuple[str, str], list]:
    """Up to `_MAX_SAMPLES_SHOWN` distinct values for a text column with few enough distinct values to be
    a category (country, category, ...) rather than free text (name, email, ...) - the cardinality check
    is also what keeps this from ever showing near-unique columns like customers.email/name as "samples".
    Skips a column already fully covered by a CHECK constraint (those are shown verbatim instead).
    """
    samples: dict[tuple[str, str], list] = {}
    for table, col, dtype in cols:
        if dtype not in _TEXT_TYPES or any(table in t and col in defn for t, defn in checks):
            continue
        rows = conn.execute(
            pg_sql.SQL("SELECT DISTINCT {col} FROM {tbl} WHERE {col} IS NOT NULL LIMIT %s").format(
                col=pg_sql.Identifier(col), tbl=pg_sql.Identifier(table)
            ),
            (_MAX_DISTINCT_SAMPLED + 1,),
        ).fetchall()
        values = sorted(r[0] for r in rows)
        if len(values) <= _MAX_DISTINCT_SAMPLED:  # fetched everything there is; not just a sample
            samples[(table, col)] = values[:_MAX_SAMPLES_SHOWN]
    return samples


def _build_schema_prompt() -> str:
    tables = sorted(ALLOWED_TABLES)
    with ro_pool().connection() as conn:
        cols = conn.execute(
            """SELECT table_name, column_name, data_type FROM information_schema.columns
               WHERE table_schema = 'public' AND table_name = ANY(%s)
               ORDER BY table_name, ordinal_position""",
            (tables,),
        ).fetchall()
        fks = _foreign_keys(conn, tables)
        checks = _check_constraints(conn, tables)
        samples = _sample_values(conn, cols, checks)

    by_table: dict[str, list[str]] = {}
    for t, c, dt in cols:
        values = samples.get((t, c))
        # Parenthesized, not a trailing "-- e.g." comment: columns are joined with ", " on one line, and
        # a comment suffix wouldn't actually terminate there - it would silently swallow every column
        # listed after it into what looks like the sample-values list.
        suffix = f" (e.g. {', '.join(map(str, values))})" if values else ""
        by_table.setdefault(t, []).append(f"{c} {dt}{suffix}")
    ddl = "\n".join(f"TABLE {t} ({', '.join(cols_)})" for t, cols_ in by_table.items())

    sections = [ddl]
    if fks:
        sections.append("Foreign keys:\n" + "\n".join(fks))
    if checks:
        sections.append("Check constraints:\n" + "\n".join(f"{t}: {defn}" for t, defn in checks))
    sections.append(_BUSINESS_RULES)
    return "\n\n".join(sections)


_schema_cache: dict[str, tuple[float, str]] = {}


def schema_prompt() -> str:
    """Introspect the live schema (only tables the read-only role may see): columns, foreign keys, CHECK
    constraints (where an enum like orders.status's valid values actually lives) and a few sample values
    for other low-cardinality text columns (country, category, ...).

    Cached for `SCHEMA_CACHE_TTL_S` (default 5 minutes), not the process lifetime: a migration applied
    while the API is running is picked up on the next cache expiry instead of needing a restart (or call
    schema_prompt.cache_clear() for an immediate refresh).
    """
    now = time.monotonic()
    cached = _schema_cache.get("v")
    if cached is not None and now - cached[0] < settings.schema_cache_ttl_s:
        return cached[1]
    text = _build_schema_prompt()
    _schema_cache["v"] = (now, text)
    return text


schema_prompt.cache_clear = _schema_cache.clear


def _prompt(question: str, prev_sql: str | None, error: str | None) -> list:
    """Build the SQL-generation prompt; prev_sql/error (if set) turn this into a self-repair retry."""
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
    """Run already-validated SQL as the read-only role, under a statement timeout and a row cap."""
    with ro_pool().connection() as conn:
        conn.read_only = True
        with conn.cursor() as cur:
            # psycopg's stubs require a LiteralString (a real source-code literal); neither string here is
            # one, since both are built at runtime. Each is still safe to run: the timeout value is our own
            # trusted int (never user text), and `sql` has already passed validate_sql()'s sqlglot AST check.
            # cast() records that out-of-band guarantee for the type checker; it performs no runtime check.
            cur.execute(cast(LiteralString, f"SET LOCAL statement_timeout = {int(settings.sql_timeout_ms)}"))
            cur.execute(cast(LiteralString, sql))
            # None only for a statement that returns no resultset; validate_sql() guarantees a SELECT reaches
            # here, so this is always populated in practice. Checked explicitly rather than assumed.
            if cur.description is None:
                raise psycopg.Error("Query returned no resultset (expected a SELECT)")
            cols = [d.name for d in cur.description]
            rows = [[_jsonable(v) for v in r] for r in cur.fetchmany(settings.sql_max_rows)]
    return cols, rows


def _log_query(question, sql, status, error, row_count, duration_ms, session_id=None) -> None:
    """Audit every attempt (ok / rejected / error) to the app log and the query_log table; never raises."""
    log.info("sql_query session=%s status=%s ms=%s rows=%s sql=%s err=%s",
              session_id, status, duration_ms, row_count, sql, error)
    try:
        with rw_pool().connection() as conn:
            conn.execute(
                """INSERT INTO query_log (session_id, question, generated_sql, status, error, row_count, duration_ms)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                (session_id, question, sql, status, error, row_count, duration_ms),
            )
    except Exception:  # audit failure must never break the user's request
        log.exception("Could not persist query_log row")


def prune_query_log() -> int:
    """Deletes query_log rows older than QUERY_LOG_RETENTION_DAYS; returns the number removed.

    `question` is raw user text (possibly containing PII) with no redaction, so this table is not meant
    to grow forever - see the column comment in db/01_schema.sql. Runs as assistant_rw, which can only
    INSERT/DELETE on query_log (plus SELECT on just the asked_at column, to evaluate this WHERE clause -
    see db/03_roles.sh): even this maintenance task can never read back a question's actual content.
    """
    with rw_pool().connection() as conn:
        cur = conn.execute(
            "DELETE FROM query_log WHERE asked_at < now() - %s * interval '1 day'",
            (settings.query_log_retention_days,),
        )
        return cur.rowcount


def run_sql_agent(question: str, session_id: str | None = None, max_attempts: int = 2) -> SQLResult:
    """Text-to-SQL entry point: generate, validate, execute, self-repair on error, and audit-log every attempt."""
    result = SQLResult(question=question)
    if is_write_request(question):  # decided on the user's words; the model is never asked
        msg = "Only SELECT statements are allowed."
        _log_query(question, None, "rejected", msg, None, 0, session_id)
        result.error = f"Could not produce a valid query: {msg}"
        return result
    prev_sql = error = None
    for _ in range(max_attempts):
        start = time.monotonic()
        raw_sql = None
        try:
            # with_structured_output().invoke() is typed dict | BaseModel (dict when the model's native
            # JSON mode is used); narrow explicitly, same pattern as route() in chat_agent.py.
            draft = structured(SQLDraft).invoke(_prompt(question, prev_sql, error))
            draft = draft if isinstance(draft, SQLDraft) else SQLDraft.model_validate(draft)
            raw_sql = draft.sql
            sql = validate_sql(raw_sql, max_rows=settings.sql_max_rows)
            result.sql = sql
            cols, rows = _execute(sql)
        except SQLValidationError as e:
            error, prev_sql = str(e), raw_sql
            _log_query(question, raw_sql, "rejected", error, None, int((time.monotonic() - start) * 1000), session_id)
            if not e.retryable:
                break
            continue
        except psycopg.Error as e:
            error, prev_sql = str(e).splitlines()[0], result.sql
            _log_query(question, result.sql, "error", error, None, int((time.monotonic() - start) * 1000), session_id)
            continue
        _log_query(question, sql, "ok", None, len(rows), int((time.monotonic() - start) * 1000), session_id)
        result.columns, result.rows, result.error = cols, rows, None
        return result
    result.error = f"Could not produce a valid query: {error}"
    return result
