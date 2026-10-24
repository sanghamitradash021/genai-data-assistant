"""Env-driven configuration. One frozen Settings instance (below) is the single source every module reads from."""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All tunables for this service, each overridable by an env var of the same name (upper-cased)."""

    model_config = SettingsConfigDict(frozen=True, extra="ignore")

    ollama_base_url: str = "http://localhost:11434"
    chat_model: str = "llama3.1:8b"
    embed_model: str = "nomic-embed-text"

    qdrant_url: str = "http://localhost:6333"
    qdrant_collection: str = "documents"

    # Generated SQL runs as the read-only role; only the audit log uses the admin role.
    database_url_ro: str = "postgresql://assistant_ro:rainbow@localhost:5432/shop"
    database_url_rw: str = "postgresql://shop_admin:development@localhost:5432/shop"

    documents_dir: str = "data/documents"
    chunk_size: int = 800
    chunk_overlap: int = 120
    top_k: int = 4
    min_score: float = 0.6  # semantic score that alone qualifies a chunk

    # Retrieval: fetch candidates, rerank with semantic + lexical signals, then pick a diverse, small context.
    retrieval_candidate_k: int = 10
    broad_top_k: int = 6                 # final context size for broad questions
    semantic_weight: float = 0.7
    keyword_weight: float = 0.3
    filename_boost: float = 0.25
    keyword_min_score: float = 0.5       # lexical score that qualifies a chunk
    semantic_floor: float = 0.35         # never keep below this, whatever the keywords
    relative_cutoff: float = 0.8         # drop chunks < cutoff * best final score
    broad_relative_cutoff: float = 0.6   # looser: answers span sections
    max_chunks_per_section: int = 2
    rag_debug: bool = False              # log (and expose) ranking details

    sql_max_rows: int = 100
    sql_timeout_ms: int = 5000

    max_message_chars: int = 2000
    max_upload_bytes: int = 10 * 1024 * 1024
    history_turns: int = 6
    auto_ingest: bool = True

    # Security: API key auth (None = disabled, e.g. local dev) and per-IP rate limits ("N/minute").
    api_key: str | None = None
    rate_limit_chat: str = "20/minute"
    rate_limit_ingest: str = "10/minute"


settings = Settings()
