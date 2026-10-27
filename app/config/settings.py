"""Env-driven configuration. One frozen Settings instance (below) is the single source every module reads from."""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All tunables for this service, each overridable by an env var of the same name (upper-cased)."""

    model_config = SettingsConfigDict(frozen=True, extra="ignore")

    ollama_base_url: str = "http://localhost:11434"
    chat_model: str = "llama3.1:8b"
    embed_model: str = "nomic-embed-text"
    ollama_timeout_s: float = 120.0  # a hung Ollama must not block a worker thread/request indefinitely
    # Worst case (route "both", broad question): ANSWER_SYSTEM (~200 tokens) + up to BROAD_TOP_K document
    # chunks (~1200 tokens) + up to 50 SQL result rows (~1600 tokens) + the question, then still needs
    # room left over for the model's own reply. 4096 left very little headroom for that combination;
    # Ollama silently truncates from the *front* of the prompt on overflow, which can drop ANSWER_SYSTEM
    # itself. 8192 keeps a comfortable margin without a large memory cost for an 8B model.
    chat_num_ctx: int = 8192

    qdrant_url: str = "http://localhost:6333"
    qdrant_collection: str = "documents"

    # Dev-only fallbacks for running the API directly against a manually-created local Postgres (tests,
    # `uvicorn app.main:app` without Docker). The shipped docker-compose.yml never uses these: it requires
    # real passwords from .env and fails to start without them (see .env.example).
    database_url_ro: str = "postgresql://assistant_ro:rainbow@localhost:5432/shop"
    database_url_rw: str = "postgresql://assistant_rw:development@localhost:5432/shop"

    documents_dir: str = "data/documents"
    max_pdf_pages: int = 500          # a PDF with more pages than this is rejected before extract_text()
    max_docx_decompressed_bytes: int = 200 * 1024 * 1024  # zip-bomb guard: decompressed size, not upload size
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
    schema_cache_ttl_s: float = 300.0   # how long schema_prompt() trusts its introspection before re-querying
    query_log_retention_days: int = 30  # query_log rows (raw user questions) older than this are pruned
    query_log_prune_interval_s: float = 3600.0  # how often the background pruning pass runs

    max_message_chars: int = 2000
    max_upload_bytes: int = 10 * 1024 * 1024
    history_turns: int = 6
    auto_ingest: bool = True

    # Security: API key auth (None = disabled, e.g. local dev) and per-IP rate limits ("N/minute").
    api_key: str | None = None
    rate_limit_chat: str = "20/minute"
    rate_limit_ingest: str = "10/minute"


settings = Settings()
