"""FastAPI application entry point: creates the app, wires in startup/lifespan, and mounts the routers.

Request handling itself lives in app/health.py and app/routes/*; app/starter.py owns startup/auto-ingest.
"""
import logging
from typing import cast

from fastapi import FastAPI, Request
from fastapi.responses import Response
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app import health
from app.config.settings import settings  # noqa: F401  (re-exported: tests mutate app.main.settings)
from app.routes import chat_routes, document_routes
from app.routes.dependencies import get_graph, get_store  # noqa: F401  (re-exported for dependency_overrides)
from app.starter import lifespan
from app.utils.rate_limit import limiter

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("app")

app = FastAPI(
    title="Local GenAI Data Assistant",
    description="Chat with documents (RAG) and PostgreSQL data (text-to-SQL), fully local.",
    version="1.0.0",
    lifespan=lifespan,
)

# Per-IP rate limiting on the expensive routes (LLM/embedding/DB calls); see chat_routes.py / document_routes.py.
app.state.limiter = limiter


def _rate_limit_handler(request: Request, exc: Exception) -> Response:
    """add_exception_handler() requires an (Request, Exception) -> Response handler; slowapi's own
    handler is typed narrower as (Request, RateLimitExceeded) -> Response. This only ever runs for a
    RateLimitExceeded (that's the type we register it against below), so the cast is safe."""
    return _rate_limit_exceeded_handler(request, cast(RateLimitExceeded, exc))


app.add_exception_handler(RateLimitExceeded, _rate_limit_handler)

app.include_router(health.router)
app.include_router(chat_routes.router)
app.include_router(document_routes.router)
