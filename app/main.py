"""FastAPI application entry point: creates the app, wires in startup/lifespan, and mounts the routers.

Request handling itself lives in app/health.py and app/routes/*; app/starter.py owns startup/auto-ingest.
"""
import logging

from fastapi import FastAPI

from app import health
from app.config.settings import settings  # noqa: F401  (re-exported: tests mutate app.main.settings)
from app.routes import chat_routes, document_routes
from app.routes.dependencies import get_graph, get_store  # noqa: F401  (re-exported for dependency_overrides)
from app.starter import lifespan

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("app")

app = FastAPI(
    title="Local GenAI Data Assistant",
    description="Chat with documents (RAG) and PostgreSQL data (text-to-SQL), fully local.",
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(health.router)
app.include_router(chat_routes.router)
app.include_router(document_routes.router)
