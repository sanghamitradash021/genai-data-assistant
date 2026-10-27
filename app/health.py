"""GET /health and GET /health/live."""
import httpx
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from app import starter
from app.config.settings import settings
from app.repository.db import ro_pool
from app.routes.dependencies import get_store
from app.schemas.health import HealthResponse

router = APIRouter()


@router.get("/health/live", include_in_schema=False)
def live():
    """Container/orchestrator liveness probe: "is this process able to serve HTTP at all". Always 200
    once the process is up - it never touches Qdrant/Ollama/Postgres, so a dependency outage never makes
    Docker/Kubernetes restart a perfectly healthy API container. This is the endpoint the Dockerfile's
    HEALTHCHECK uses; GET /health below is a diagnostic for humans/dashboards, not a liveness probe."""
    return {"status": "ok"}


@router.get("/health", response_model=HealthResponse)
def health(store=Depends(get_store)):
    """Diagnostic status of Qdrant, Ollama and Postgres; 200 only if all three are reachable, else 503.

    Not a liveness/readiness probe (see /health/live above): a single slow dependency flipping this to
    503 should never make an orchestrator kill or stop routing to the container - restarting the API
    process fixes nothing if, say, Ollama is what's down. Point infra health checks at /health/live and
    use this one for dashboards/alerting instead.

    Also reports auto-ingest status: on first run the backing services can come up (and this check pass)
    before documents are indexed, so a query made in that window finds nothing. `ingest` in the response
    lets a client distinguish "not ingested yet" from "no matching document".
    """
    checks = {}

    def probe(name, fn):
        try:
            fn()
            checks[name] = "ok"
        except Exception as e:
            checks[name] = f"unavailable: {type(e).__name__}"

    def probe_postgres() -> None:
        with ro_pool().connection(timeout=3) as conn:
            conn.execute("SELECT 1")

    probe("qdrant", store.ping)
    probe("ollama", lambda: httpx.get(f"{settings.ollama_base_url}/api/tags", timeout=3).raise_for_status())
    probe("postgres", probe_postgres)
    ok = all(v == "ok" for v in checks.values())
    body = HealthResponse(status="ok" if ok else "degraded", services=checks, ingest=starter.ingest_status)
    return JSONResponse(body.model_dump(), status_code=200 if ok else 503)
