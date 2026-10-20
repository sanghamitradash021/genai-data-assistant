import os
from dataclasses import dataclass


def _int(name: str, default: int) -> int:
    return int(os.getenv(name, default))


@dataclass(frozen=True)
class Settings:
    ollama_base_url: str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    chat_model: str = os.getenv("CHAT_MODEL", "llama3.1:8b")
    embed_model: str = os.getenv("EMBED_MODEL", "nomic-embed-text")

    qdrant_url: str = os.getenv("QDRANT_URL", "http://localhost:6333")
    qdrant_collection: str = os.getenv("QDRANT_COLLECTION", "documents")

    # Generated SQL runs as the read-only role; only the audit log uses the admin role.
    database_url_ro: str = os.getenv(
        "DATABASE_URL_RO", "postgresql://assistant_ro:change-me-readonly@localhost:5432/shop"
    )
    database_url_rw: str = os.getenv(
        "DATABASE_URL_RW", "postgresql://shop_admin:change-me-admin@localhost:5432/shop"
    )

    documents_dir: str = os.getenv("DOCUMENTS_DIR", "data/documents")
    chunk_size: int = _int("CHUNK_SIZE", 800)
    chunk_overlap: int = _int("CHUNK_OVERLAP", 120)
    top_k: int = _int("TOP_K", 4)
    min_score: float = float(os.getenv("RAG_MIN_SCORE", "0.6"))  # semantic score that alone qualifies a chunk

    # Retrieval: fetch candidates, rerank with semantic + lexical signals, then pick a diverse, small context.
    retrieval_candidate_k: int = _int("RETRIEVAL_CANDIDATE_K", 10)
    broad_top_k: int = _int("BROAD_TOP_K", 6)                 # final context size for broad questions
    semantic_weight: float = float(os.getenv("SEMANTIC_WEIGHT", "0.7"))
    keyword_weight: float = float(os.getenv("KEYWORD_WEIGHT", "0.3"))
    filename_boost: float = float(os.getenv("FILENAME_BOOST", "0.25"))
    keyword_min_score: float = float(os.getenv("KEYWORD_MIN_SCORE", "0.5"))  # lexical score that qualifies a chunk
    semantic_floor: float = float(os.getenv("SEMANTIC_FLOOR", "0.35"))       # never keep below this, whatever the keywords
    relative_cutoff: float = float(os.getenv("RELATIVE_CUTOFF", "0.8"))      # drop chunks < cutoff * best final score
    broad_relative_cutoff: float = float(os.getenv("BROAD_RELATIVE_CUTOFF", "0.6"))  # looser: answers span sections
    max_chunks_per_section: int = _int("MAX_CHUNKS_PER_SECTION", 2)
    rag_debug: bool = os.getenv("RAG_DEBUG", "false").lower() == "true"       # log (and expose) ranking details

    sql_max_rows: int = _int("SQL_MAX_ROWS", 100)
    sql_timeout_ms: int = _int("SQL_TIMEOUT_MS", 5000)

    max_message_chars: int = _int("MAX_MESSAGE_CHARS", 2000)
    max_upload_bytes: int = _int("MAX_UPLOAD_BYTES", 10 * 1024 * 1024)
    history_turns: int = _int("HISTORY_TURNS", 6)
    auto_ingest: bool = os.getenv("AUTO_INGEST", "true").lower() == "true"


settings = Settings()
