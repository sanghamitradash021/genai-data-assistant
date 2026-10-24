"""POST /documents/ingest, GET /documents, DELETE /documents/{id}."""
import logging
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile

from app.config.settings import settings
from app.routes.dependencies import get_store, require_api_key
from app.schemas.documents import DocumentInfo, IngestResponse
from app.services import document_service as documents
from app.starter import docs_dir, ingest_directory
from app.utils.rate_limit import limiter
from app.utils.security import safe_filename

log = logging.getLogger("app")

router = APIRouter(dependencies=[Depends(require_api_key)])


@router.post("/documents/ingest", response_model=IngestResponse)
@limiter.limit(lambda: settings.rate_limit_ingest)
def ingest(
    request: Request,
    file: UploadFile | None = File(default=None, description="Optional file to upload and index"),
    force: bool = Query(default=False, description="Re-embed files that are already indexed"),
    store=Depends(get_store),
):
    """Upload one file (PDF/DOCX/TXT/MD), or call with no body to index everything in data/documents/."""
    try:
        if file is None:
            return ingest_directory(store, force)
        name = safe_filename(file.filename or "upload")
        if Path(name).suffix.lower() not in documents.SUPPORTED_EXTENSIONS:
            raise HTTPException(415, f"Unsupported type. Allowed: {sorted(documents.SUPPORTED_EXTENSIONS)}")
        data = file.file.read(settings.max_upload_bytes + 1)
        if len(data) > settings.max_upload_bytes:
            raise HTTPException(413, f"File exceeds {settings.max_upload_bytes} bytes.")
        path = docs_dir() / name
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


@router.get("/documents", response_model=list[DocumentInfo])
def list_documents(store=Depends(get_store)):
    """List every currently-indexed document."""
    return store.list_documents()


@router.delete("/documents/{doc_id}", status_code=200)
def delete_document(doc_id: str, store=Depends(get_store)):
    """Removes the document's vectors and its file, so it is not re-indexed on restart."""
    doc = store.get_document(doc_id)
    if doc is None:
        raise HTTPException(404, "Document not found.")
    store.delete_vectors(doc_id)
    (docs_dir() / safe_filename(doc.filename)).unlink(missing_ok=True)
    return {"deleted": doc.id, "filename": doc.filename}
