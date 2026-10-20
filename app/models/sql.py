"""Internal domain object for a SQL agent run (not an API schema)."""
from dataclasses import dataclass, field
from typing import Any


@dataclass
class SQLResult:
    question: str
    sql: str | None = None
    columns: list[str] = field(default_factory=list)
    rows: list[list[Any]] = field(default_factory=list)
    error: str | None = None
