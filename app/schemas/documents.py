"""API request/response contracts for the /documents endpoints."""
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
