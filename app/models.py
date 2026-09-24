"""Plain data types shared across modules (no heavy imports)."""
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field


class RouteDecision(BaseModel):
    """Structured output of the router LLM call."""

    route: Literal["document", "database", "both"] = Field(
        description="document = policies/handbooks/text files; database = numbers, customers, "
        "orders, revenue, refunds amounts; both = needs a document AND database figures"
    )
    doc_question: str = Field(default="", description="Sub-question for the documents ('' if none)")
    db_question: str = Field(default="", description="Sub-question for the database ('' if none)")


@dataclass
class Hit:
    doc_id: str
    filename: str
    page: int | None
    chunk_index: int
    score: float  # final (reranked) score, 0..1
    text: str
    section: str | None = None
    heading: str | None = None
    heading_path: str | None = None
    semantic: float = 0.0  # ranking signals, kept for debugging / future reranking
    keyword: float = 0.0
    matched: list[str] = field(default_factory=list)


@dataclass
class SQLResult:
    question: str
    sql: str | None = None
    columns: list[str] = field(default_factory=list)
    rows: list[list[Any]] = field(default_factory=list)
    error: str | None = None
