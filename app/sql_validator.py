"""Static validation of LLM-generated SQL. Fails closed: anything not provably a plain SELECT is rejected.

This is one of three layers: (1) this validator, (2) a read-only DB role with SELECT on an
allowlist of tables, (3) a READ ONLY transaction with statement_timeout.
"""
import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

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


class SQLValidationError(ValueError):
    """retryable=False for security rejections (writes, forbidden functions, system schemas): the agent must
    not ask the model to "repair" those, or it may swap in an unrelated query."""

    def __init__(self, message: str, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


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

    cte_names = {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}
    for node in tree.walk():
        node.comments = None  # never forward LLM-supplied comments to the database
        if isinstance(node, _FORBIDDEN_NODES):
            raise SQLValidationError(f"Forbidden construct: {type(node).__name__}.")
        if isinstance(node, exp.Anonymous):
            fn = node.name.lower()
            if fn in _BLOCKED_FUNCS or fn.startswith(_BLOCKED_FUNC_PREFIXES):
                raise SQLValidationError(f"Function not allowed: {fn}.")
        if isinstance(node, exp.Table):
            name = node.name.lower()
            if not isinstance(node.this, exp.Identifier):
                raise SQLValidationError("Table-valued functions are not allowed.")
            if node.db and node.db.lower() != "public":
                raise SQLValidationError(f"Schema not allowed: {node.db}.")
            if name not in cte_names and name not in allowed_tables:
                system = bool(node.db) or name.startswith("pg_")
                raise SQLValidationError(f"Table not allowed: {name}.", retryable=not system)

    if not tree.args.get("limit"):
        tree = tree.limit(max_rows)
    return tree.sql(dialect="postgres")
