"""Shared FastAPI dependency providers, used by every router and by app/health.py.

Kept in one place (and re-exported from app.main for convenience/tests) so `app.main.app.dependency_overrides`
always targets the exact same callables the routers depend on.
"""
from functools import lru_cache


@lru_cache
def get_store():
    from app.repository.vector_store import VectorStore

    return VectorStore()


@lru_cache
def get_graph():
    from app.agents.chat_agent import build_graph, default_deps

    return build_graph(default_deps(get_store()))
