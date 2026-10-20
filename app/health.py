"""GET /health and GET /health/live."""
import httpx
import psycopg
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from app.config.settings import settings
from app.routes.dependencies import get_store
from app.schemas.health import HealthResponse

router = APIRouter()


@router.get("/health/live", include_in_schema=False)
def live():
    return {"status": "ok"}


@router.get("/health", response_model=HealthResponse)
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
