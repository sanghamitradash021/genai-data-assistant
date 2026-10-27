"""Static validation of LLM-generated SQL. Fails closed: anything not provably a plain SELECT is rejected.

This is one of three layers: (1) this validator, (2) a read-only DB role with SELECT on an
allowlist of tables, (3) a READ ONLY transaction with statement_timeout.
"""
import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError
from sqlglot.optimizer.scope import Scope, build_scope

from app.exceptions.sql import SQLValidationError

ALLOWED_TABLES = frozenset({"customers", "products", "orders", "order_items", "refunds"})

_ROOT_TYPES = (exp.Select, exp.Union, exp.Intersect, exp.Except)
_FORBIDDEN_NAMES = (
    "Insert", "Update", "Delete", "Drop", "Create", "Alter", "Merge", "Command",
    "TruncateTable", "Copy", "Set", "Into", "Lock", "Grant", "Use", "Transaction",
    "Commit", "Rollback", "Pragma", "Attach", "Detach",
)
_FORBIDDEN_NODES = tuple(getattr(exp, n) for n in _FORBIDDEN_NAMES if hasattr(exp, n))
_BLOCKED_FUNCS = {"dblink", "set_config", "current_setting", "version", "txid_current"}
_BLOCKED_FUNC_PREFIXES = ("pg_", "lo_", "dblink", "query_to_xml", "xpath")


def _check_table(node: exp.Table, allowed_tables: frozenset[str]) -> None:
    name = node.name.lower()
    if not isinstance(node.this, exp.Identifier):
        raise SQLValidationError("Table-valued functions are not allowed.")
    if node.db and node.db.lower() != "public":
        raise SQLValidationError(f"Schema not allowed: {node.db}.")
    if name not in allowed_tables:
        system = bool(node.db) or name.startswith("pg_")
        raise SQLValidationError(f"Table not allowed: {name}.", retryable=not system)


def validate_sql(sql: str, *, max_rows: int, allowed_tables: frozenset[str] = ALLOWED_TABLES) -> str:
    """Return a normalized, single-statement, LIMIT-bounded SELECT, or raise SQLValidationError."""
    if not sql or not sql.strip():
        raise SQLValidationError("Empty SQL.")
    try:
        statements = [s for s in sqlglot.parse(sql, read="postgres") if s is not None]
    except SqlglotError as e:
        raise SQLValidationError(f"SQL could not be parsed: {e}", retryable=True) from e

    if len(statements) != 1:
        raise SQLValidationError("Exactly one SQL statement is allowed.")
    tree = statements[0]
    if not isinstance(tree, _ROOT_TYPES):
        raise SQLValidationError("Only SELECT statements are allowed.")

    # A CTE named after an allowed/system table ("WITH pg_shadow AS (...) ...", "WITH customers AS
    # (SELECT 1) ..."), while harmless by itself under correct scope resolution (see below), is exactly
    # the kind of name confusion a future sqlglot change or edge case could turn into a real bypass.
    # Simplest to just never allow it, independent of whether the scope check below would also catch it.
    for cte in tree.find_all(exp.CTE):
        cname = cte.alias_or_name.lower()
        if cname in allowed_tables or cname.startswith("pg_") or cname == "information_schema":
            raise SQLValidationError(f"CTE name not allowed: {cname}.")

    for node in tree.walk():
        node.comments = None  # never forward LLM-supplied comments to the database
        if isinstance(node, _FORBIDDEN_NODES):
            raise SQLValidationError(f"Forbidden construct: {type(node).__name__}.")
        if isinstance(node, exp.Anonymous):
            fn = node.name.lower()
            if fn in _BLOCKED_FUNCS or fn.startswith(_BLOCKED_FUNC_PREFIXES):
                raise SQLValidationError(f"Function not allowed: {fn}.")

    # Table references are checked via sqlglot's *scope* resolution, not a flat "is this name anywhere
    # in the tree a CTE" set: a bare name match would let a real table reference outside a CTE's scope
    # (e.g. a sibling branch of a UNION) slip through unchecked just because some unrelated CTE
    # elsewhere in the statement happens to share its name ("SELECT * FROM pg_shadow UNION ALL (WITH
    # pg_shadow AS (...) SELECT ...)" - the first `pg_shadow` is the real system table and must still be
    # rejected). build_scope()'s `sources` only ever contains what that specific SELECT can actually see.
    for scope in build_scope(tree).traverse():
        for source in scope.sources.values():
            if isinstance(source, Scope):
                continue  # resolves to an in-scope CTE or derived table, not a real table reference
            if isinstance(source, exp.Table):
                _check_table(source, allowed_tables)

    limit_node = tree.args.get("limit")
    within_cap = (
        limit_node is not None
        and isinstance(limit_node.expression, exp.Literal)
        and limit_node.expression.is_int
        and int(limit_node.expression.this) <= max_rows
    )
    if not within_cap:
        # Clamps LIMIT down to max_rows (not just "add one if missing"): `LIMIT 1000000` parses fine and
        # only fetchmany() capped the *returned* rows, so the database still did the full-size work.
        tree = tree.limit(max_rows)
    return tree.sql(dialect="postgres")
