#!/bin/bash
# Least-privilege role used to execute LLM-generated SQL: SELECT on business tables only.
set -e
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<SQL
CREATE ROLE assistant_ro LOGIN PASSWORD '${READONLY_PASSWORD}';
ALTER ROLE assistant_ro SET default_transaction_read_only = on;
ALTER ROLE assistant_ro SET statement_timeout = '5s';
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO assistant_ro;
GRANT SELECT ON customers, products, orders, order_items, refunds TO assistant_ro;
SQL
