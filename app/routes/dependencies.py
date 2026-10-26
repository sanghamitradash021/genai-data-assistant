"""Shared FastAPI dependency providers, used by every router and by app/health.py.

Kept in one place (and re-exported from app.main for convenience/tests) so `app.main.app.dependency_overrides`
always targets the exact same callables the routers depend on.
"""
import hmac
from functools import lru_cache

from fastapi import Header, HTTPException

from app.config.settings import settings


@lru_cache
def get_store():
    from app.repository.vector_store import VectorStore

    return VectorStore()


@lru_cache
def get_graph():
    from app.agents.chat_agent import build_graph, default_deps

    return build_graph(default_deps(get_store()))


def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    """Gate write/compute-heavy routes behind X-API-Key. No-op when API_KEY is unset/empty (local dev).

    `not settings.api_key` (not `is None`): docker-compose.yml passes API_KEY="" when the host env var
    isn't set at all, and an empty string must disable auth the same way None does, or every request
    gets rejected with no header able to match "".
    """
    if not settings.api_key:
        return
    # compare_digest (not !=): a short-circuiting != leaks the key's length/prefix via response timing.
    if x_api_key is None or not hmac.compare_digest(x_api_key, settings.api_key):
        raise HTTPException(401, "Missing or invalid API key.")
