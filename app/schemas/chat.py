"""API request/response contracts for POST /chat."""
from typing import Any

from pydantic import BaseModel, Field

from app.config.settings import settings


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=settings.max_message_chars)
    session_id: str | None = Field(default=None, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")


class Source(BaseModel):
    document_id: str
    filename: str
    page: int | None = None
    score: float
    snippet: str
    section: str | None = None
    heading: str | None = None
    debug: dict[str, Any] | None = Field(default=None, description="Only present when RAG_DEBUG=true")


class SQLInfo(BaseModel):
    query: str | None
    columns: list[str]
    rows: list[list[Any]]
    row_count: int
    error: str | None = None


class ChatResponse(BaseModel):
    session_id: str
    answer: str
    route: str = Field(description="document | database | both")
    standalone_question: str
    sources: list[Source] = Field(default_factory=list)
    sql: SQLInfo | None = None
