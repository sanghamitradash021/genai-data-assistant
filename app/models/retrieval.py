"""Internal domain object for a single retrieved document chunk (not an API schema)."""
from dataclasses import dataclass, field


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
