"""Application startup/bootstrap: the FastAPI lifespan, and the auto-ingest it kicks off at boot.

Directory-ingest itself (`ingest_directory`/`docs_dir`) lives in app.services.document_service, which both
this module and POST /documents/ingest (app/routes/document_routes.py) call, so startup and that endpoint
share one ingestion path without routes importing the bootstrap module.
"""
import logging
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config.settings import settings
from app.container import get_store
from app.repository.db import close_pools
from app.services.document_service import ingest_directory

log = logging.getLogger("app")

# Auto-ingest status, polled by GET /health so clients can tell "no results yet" apart from
# "still indexing" during the slow first run. "disabled" when AUTO_INGEST is off.
ingest_status = "disabled"


def _auto_ingest() -> None:
    global ingest_status
    ingest_status = "running"
    try:
        res = ingest_directory(get_store())
        log.info("Auto-ingest: %d new, %d skipped, %d errors", len(res.ingested), len(res.skipped), len(res.errors))
        ingest_status = "done"
    except Exception:
        log.exception("Auto-ingest failed; call POST /documents/ingest once services are up")
        ingest_status = "failed"


_prune_stop = threading.Event()


def _prune_loop() -> None:
    """Runs prune_query_log() immediately and then every QUERY_LOG_PRUNE_INTERVAL_S, until shutdown.

    query_log holds raw user questions with no redaction (see db/01_schema.sql); this is what keeps that
    bounded to QUERY_LOG_RETENTION_DAYS instead of growing forever.
    """
    from app.agents.sql_agent import prune_query_log

    while True:
        try:
            removed = prune_query_log()
            if removed:
                log.info("query_log retention: pruned %d row(s) older than %d day(s)",
                          removed, settings.query_log_retention_days)
        except Exception:
            log.exception("query_log retention prune failed")
        if _prune_stop.wait(settings.query_log_prune_interval_s):
            return


@asynccontextmanager
async def lifespan(_: FastAPI):
    """FastAPI startup hook: kicks off auto-ingest and query_log retention pruning in background threads
    so neither ever blocks readiness."""
    global ingest_status
    if settings.auto_ingest:
        ingest_status = "pending"
        threading.Thread(target=_auto_ingest, daemon=True).start()
    if settings.query_log_retention_days > 0:
        threading.Thread(target=_prune_loop, daemon=True).start()
    yield
    _prune_stop.set()
    close_pools()
