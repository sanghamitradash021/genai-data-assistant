#!/bin/bash
# Least-privilege roles for the two things the API ever does as itself:
#   assistant_ro - runs LLM-generated SQL; SELECT only, on the business tables.
#   assistant_rw - writes the query_log audit row and prunes old ones (INSERT + DELETE, query_log alone;
#                  no SELECT - it cannot read the log back, only append to and trim it). It is NOT the
#                  Postgres superuser/DB owner (POSTGRES_USER/shop_admin) that the API's "rw" connection
#                  used to reuse just to append one audit row - that user can do anything to any table.
#
# Passwords reach SQL via psql's own `-v name=value` + `:'name'` substitution, not bash string
# interpolation into the SQL text: a password containing a single quote would otherwise break out of the
# `'...'` literal in `CREATE ROLE ... PASSWORD '${VAR}'` and inject arbitrary SQL into this init script.
set -e
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  -v ro_password="$READONLY_PASSWORD" -v rw_password="$ASSISTANT_RW_PASSWORD" <<'SQL'
CREATE ROLE assistant_ro LOGIN PASSWORD :'ro_password';
ALTER ROLE assistant_ro SET default_transaction_read_only = on;
ALTER ROLE assistant_ro SET statement_timeout = '5s';

CREATE ROLE assistant_rw LOGIN PASSWORD :'rw_password';
ALTER ROLE assistant_rw SET statement_timeout = '5s';

REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO assistant_ro, assistant_rw;
GRANT SELECT ON customers, products, orders, order_items, refunds TO assistant_ro;
GRANT INSERT, DELETE ON query_log TO assistant_rw;
GRANT USAGE ON SEQUENCE query_log_id_seq TO assistant_rw;
-- Column-level, not table-level: retention pruning deletes WHERE asked_at < ..., which Postgres requires
-- SELECT on the referenced column to evaluate even for a role that already has DELETE - but assistant_rw
-- must still never be able to read back `question`/`generated_sql`/`error` (the sensitive columns).
GRANT SELECT (asked_at) ON query_log TO assistant_rw;
SQL
