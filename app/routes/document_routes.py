"""POST /documents/ingest, GET /documents, DELETE /documents/{id}."""
import logging
import tempfile
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile

from app.config.settings import settings
from app.routes.dependencies import get_store, require_api_key
from app.schemas.documents import DocumentInfo, IngestResponse
from app.services import document_service as documents
from app.services.document_service import docs_dir, ingest_directory
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
        # Written to a same-filesystem temp dir and ingested from there first, not straight to docs_dir():
        # re-uploading a file under a name that's already indexed (e.g. a routine re-upload of
        # leave_policy.md) used to overwrite that file on disk immediately, so a corrupt replacement
        # destroyed the previously-good file's bytes before validation ever ran, with no way to recover
        # them (the old vectors stayed in Qdrant, now pointing at a file that no longer exists). The real
        # path is only touched - via an atomic rename - after store.ingest_file() has already succeeded.
        path = docs_dir() / name
        with tempfile.TemporaryDirectory(dir=docs_dir()) as tmp_dir:
            tmp_path = Path(tmp_dir) / name
            tmp_path.write_bytes(data)
            try:
                info = store.ingest_file(tmp_path)
            except ValueError as e:
                raise HTTPException(422, str(e)) from e
            tmp_path.replace(path)
        return IngestResponse(ingested=[info])
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
