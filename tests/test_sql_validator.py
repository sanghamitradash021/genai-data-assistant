import pytest

from app.sql_validator import SQLValidationError, validate_sql


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
