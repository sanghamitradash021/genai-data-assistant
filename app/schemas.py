"""Pydantic request/response models for the HTTP API."""
from typing import Any

from pydantic import BaseModel, Field


class DocumentInfo(BaseModel):
    id: str
    filename: str
    file_type: str
    chunks: int
    ingested_at: str


class IngestResponse(BaseModel):
    ingested: list[DocumentInfo]
    skipped: list[str] = Field(default_factory=list, description="Already indexed (use force=true)")
    errors: dict[str, str] = Field(default_factory=dict)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
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


class HealthResponse(BaseModel):
    status: str
    services: dict[str, str]
