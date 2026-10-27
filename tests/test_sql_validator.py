import pytest

from app.exceptions.sql import SQLValidationError
from app.tools.sql_validator import validate_sql


def v(sql):
    return validate_sql(sql, max_rows=100)


def test_simple_select_gets_limit():
    assert v("SELECT * FROM customers").lower().endswith("limit 100")


def test_existing_limit_kept():
    assert "LIMIT 5" in v("SELECT name FROM customers LIMIT 5")


def test_join_cte_and_aggregate_allowed():
    sql = """WITH r AS (SELECT customer_id, SUM(total_amount) AS rev FROM orders GROUP BY customer_id)
             SELECT c.name, r.rev FROM customers c JOIN r ON r.customer_id = c.id ORDER BY r.rev DESC"""
    assert "LIMIT" in v(sql)


def test_trailing_semicolon_ok():
    v("SELECT 1 FROM products;")


@pytest.mark.parametrize(
    "sql",
    [
        "DROP TABLE customers",
        "DELETE FROM orders",
        "UPDATE orders SET status='x'",
        "INSERT INTO customers(name) VALUES ('x')",
        "TRUNCATE orders",
        "SELECT 1; DROP TABLE customers",
        "SELECT * INTO newtab FROM customers",
        "WITH d AS (DELETE FROM orders RETURNING *) SELECT * FROM d",
        "SELECT * FROM customers FOR UPDATE",
        "SELECT * FROM pg_shadow",
        "SELECT * FROM information_schema.tables",
        "SELECT * FROM pg_catalog.pg_user",
        "SELECT * FROM query_log",
        "SELECT pg_sleep(10)",
        "SELECT pg_read_file('/etc/passwd')",
        "SELECT * FROM generate_series(1, 10)",
        "COPY customers TO '/tmp/x'",
        "SET ROLE postgres",
        "",
        "not sql at all ((",
    ],
)
def test_rejected(sql):
    with pytest.raises(SQLValidationError):
        v(sql)


def test_comment_smuggling_is_stripped():
    out = v("SELECT id FROM orders -- ; DROP TABLE orders")
    assert "DROP" not in out.upper()


# --- CTE-name shadowing must never let a real table reference slip through ----------------------------
@pytest.mark.parametrize(
    "sql",
    [
        # A sibling branch's real system-table reference must still be rejected even though a CTE
        # elsewhere in the statement happens to reuse that exact name for something harmless.
        "SELECT * FROM pg_shadow UNION ALL (WITH pg_shadow AS (SELECT 1 AS x) SELECT x FROM pg_shadow)",
        "SELECT * FROM information_schema.tables "
        "UNION ALL (WITH information_schema AS (SELECT 1 AS x) SELECT x FROM information_schema)",
        # The CTE name itself is disallowed outright, independent of what it actually selects.
        "WITH pg_shadow AS (SELECT 1 AS x) SELECT x FROM pg_shadow",
        "WITH customers AS (SELECT 1 AS id) SELECT id FROM customers",  # shadows an allowed table too
    ],
)
def test_cte_cannot_shadow_an_allowed_or_system_table_name(sql):
    with pytest.raises(SQLValidationError):
        v(sql)


def test_genuinely_scoped_cte_still_allowed():
    # A CTE with an ordinary, non-shadowing name is unaffected by the scope-based table check.
    sql = "WITH recent AS (SELECT * FROM orders) SELECT * FROM recent"
    assert "LIMIT" in v(sql)


# --- LIMIT is clamped, not just added when absent ------------------------------------------------------
def test_oversized_limit_is_clamped_to_max_rows():
    out = v("SELECT * FROM customers LIMIT 1000000")
    assert "LIMIT 100" in out and "1000000" not in out


def test_non_numeric_limit_is_replaced():
    out = v("SELECT * FROM customers LIMIT ALL")
    assert "LIMIT 100" in out


def test_limit_under_cap_is_left_alone():
    assert "LIMIT 5" in v("SELECT * FROM customers LIMIT 5")
