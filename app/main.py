import logging
import threading
from contextlib import asynccontextmanager
from functools import lru_cache
from pathlib import Path
from uuid import uuid4

import httpx
import psycopg
from fastapi import Depends, FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import JSONResponse

from . import documents
from .config import settings
from .graph import build_graph, default_deps, initial_state
from .memory import ConversationStore
from .models import Hit
from .schemas import (
    ChatRequest, ChatResponse, DocumentInfo, HealthResponse, IngestResponse, Source, SQLInfo,
)
from .security import safe_filename, sanitize_message

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("app")

_conversations = ConversationStore(max_turns=settings.history_turns)


@lru_cache
def get_store():
    from .vectorstore import VectorStore

    return VectorStore()


@lru_cache
def get_graph():
    return build_graph(default_deps(get_store()))


def _docs_dir() -> Path:
    p = Path(settings.documents_dir)
    p.mkdir(parents=True, exist_ok=True)
    return p


def _ingest_dir(store, force: bool = False) -> IngestResponse:
    indexed = {d.id for d in store.list_documents()} - store.stale_doc_ids()  # old chunker -> re-embed
    out = IngestResponse(ingested=[])
    for path in sorted(_docs_dir().iterdir()):
        if not path.is_file() or path.suffix.lower() not in documents.SUPPORTED_EXTENSIONS:
            continue
        if not force and documents.doc_id_for(path.name) in indexed:
            out.skipped.append(path.name)
            continue
        try:
            out.ingested.append(store.ingest_file(path))
        except Exception as e:
            log.exception("Ingest failed for %s", path.name)
            out.errors[path.name] = str(e)
    return out


def _auto_ingest() -> None:
    try:
        res = _ingest_dir(get_store())
        log.info("Auto-ingest: %d new, %d skipped, %d errors", len(res.ingested), len(res.skipped), len(res.errors))
    except Exception:
        log.exception("Auto-ingest failed; call POST /documents/ingest once services are up")


@asynccontextmanager
async def lifespan(_: FastAPI):
    if settings.auto_ingest:
        threading.Thread(target=_auto_ingest, daemon=True).start()
    yield


app = FastAPI(
    title="Local GenAI Data Assistant",
    description="Chat with documents (RAG) and PostgreSQL data (text-to-SQL), fully local.",
    version="1.0.0",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
@app.get("/health/live", include_in_schema=False)
def live():
    return {"status": "ok"}


@app.get("/health", response_model=HealthResponse)
def health(store=Depends(get_store)):
    checks = {}

    def probe(name, fn):
        try:
            fn()
            checks[name] = "ok"
        except Exception as e:
            checks[name] = f"unavailable: {type(e).__name__}"

    probe("qdrant", store.ping)
    probe("ollama", lambda: httpx.get(f"{settings.ollama_base_url}/api/tags", timeout=3).raise_for_status())
    probe("postgres", lambda: psycopg.connect(settings.database_url_ro, connect_timeout=3).close())
    ok = all(v == "ok" for v in checks.values())
    body = HealthResponse(status="ok" if ok else "degraded", services=checks)
    return JSONResponse(body.model_dump(), status_code=200 if ok else 503)


@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest, graph=Depends(get_graph)):
    message = sanitize_message(req.message)
    if not message:
        raise HTTPException(422, "Message is empty.")
    session_id = req.session_id or uuid4().hex
    try:
        state = graph.invoke(initial_state(message, _conversations.get(session_id)))
    except (httpx.HTTPError, ConnectionError) as e:
        log.exception("LLM backend unavailable")
        raise HTTPException(503, "Language model backend is unavailable. Is Ollama running with the models pulled?") from e
    except Exception as e:
        log.exception("Chat pipeline failed")
        raise HTTPException(500, "Internal error while answering.") from e

    _conversations.add_turn(session_id, message, state["answer"])

    route = state["route"]
    sql = state.get("sql") if route in ("database", "both") else None
    sources: list[Source] = []
    if route in ("document", "both"):
        seen = set()
        for h in state.get("hits", []):
            key = (h.doc_id, h.page, h.heading_path)  # one source per page / Markdown section
            if key in seen:
                continue
            seen.add(key)
            sources.append(_source(h))
    return ChatResponse(
        session_id=session_id,
        answer=state["answer"],
        route=route,
        standalone_question=state["standalone"],
        sources=sources,
        sql=SQLInfo(query=sql.sql, columns=sql.columns, rows=sql.rows, row_count=len(sql.rows), error=sql.error)
        if sql
        else None,
    )


def _source(h: Hit) -> Source:
    debug = None
    if settings.rag_debug:  # ranking internals are for development only
        debug = {"chunk_index": h.chunk_index, "semantic": h.semantic, "keyword": h.keyword,
                 "final": h.score, "matched": h.matched, "heading_path": h.heading_path}
    return Source(document_id=h.doc_id, filename=h.filename, page=h.page, score=h.score, snippet=h.text[:240],
                  section=h.section, heading=h.heading, debug=debug)


@app.post("/documents/ingest", response_model=IngestResponse)
def ingest(
    file: UploadFile | None = File(default=None, description="Optional file to upload and index"),
    force: bool = Query(default=False, description="Re-embed files that are already indexed"),
    store=Depends(get_store),
):
    """Upload one file (PDF/DOCX/TXT/MD), or call with no body to index everything in data/documents/."""
    try:
        if file is None:
            return _ingest_dir(store, force)
        name = safe_filename(file.filename or "upload")
        if Path(name).suffix.lower() not in documents.SUPPORTED_EXTENSIONS:
            raise HTTPException(415, f"Unsupported type. Allowed: {sorted(documents.SUPPORTED_EXTENSIONS)}")
        data = file.file.read(settings.max_upload_bytes + 1)
        if len(data) > settings.max_upload_bytes:
            raise HTTPException(413, f"File exceeds {settings.max_upload_bytes} bytes.")
        path = _docs_dir() / name
        path.write_bytes(data)
        try:
            return IngestResponse(ingested=[store.ingest_file(path)])
        except ValueError as e:
            path.unlink(missing_ok=True)
            raise HTTPException(422, str(e)) from e
    except HTTPException:
        raise
    except (httpx.HTTPError, ConnectionError) as e:
        log.exception("Ingest backend unavailable")
        raise HTTPException(503, "Embedding backend or vector store is unavailable.") from e


@app.get("/documents", response_model=list[DocumentInfo])
def list_documents(store=Depends(get_store)):
    return store.list_documents()


@app.delete("/documents/{doc_id}", status_code=200)
def delete_document(doc_id: str, store=Depends(get_store)):
    """Removes the document's vectors and its file, so it is not re-indexed on restart."""
    doc = store.get_document(doc_id)
    if doc is None:
        raise HTTPException(404, "Document not found.")
    store.delete_vectors(doc_id)
    (_docs_dir() / safe_filename(doc.filename)).unlink(missing_ok=True)
    return {"deleted": doc.id, "filename": doc.filename}
