"""Singleton providers for app-wide dependencies (vector store, chat graph).

Lives below both app.routes and app.starter so neither has to import from the other to get a
store/graph instance; both import from here instead.
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
