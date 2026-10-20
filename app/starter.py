"""Application startup/bootstrap: the FastAPI lifespan, and the data/documents/ auto-ingest it runs.

`ingest_directory` is also called directly by POST /documents/ingest (app/routes/document_routes.py) when
no file is uploaded, so startup and that endpoint share one ingestion path.
"""
import logging
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from app.config.settings import settings
from app.routes.dependencies import get_store
from app.schemas.documents import IngestResponse
from app.services import document_service as documents

log = logging.getLogger("app")


def docs_dir() -> Path:
    p = Path(settings.documents_dir)
    p.mkdir(parents=True, exist_ok=True)
    return p


def ingest_directory(store, force: bool = False) -> IngestResponse:
    indexed = {d.id for d in store.list_documents()} - store.stale_doc_ids()  # old chunker -> re-embed
    out = IngestResponse(ingested=[])
    for path in sorted(docs_dir().iterdir()):
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
        res = ingest_directory(get_store())
        log.info("Auto-ingest: %d new, %d skipped, %d errors", len(res.ingested), len(res.skipped), len(res.errors))
    except Exception:
        log.exception("Auto-ingest failed; call POST /documents/ingest once services are up")


@asynccontextmanager
async def lifespan(_: FastAPI):
    if settings.auto_ingest:
        threading.Thread(target=_auto_ingest, daemon=True).start()
    yield
