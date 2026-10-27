"""Process-wide PostgreSQL connection pools (read-only role, read-write role for query_log).

Everything that talks to Postgres (schema introspection, SQL execution, audit logging, /health) was
opening a brand-new connection per call - two round trips per chat question (execute + log) plus one for
every /health check. Pooling keeps a handful of connections warm per role instead.
"""
from functools import lru_cache

from psycopg_pool import ConnectionPool

from app.config.settings import settings


def _pool(conninfo: str) -> ConnectionPool:
    """`open=False` + `open(wait=False)`: the pool object is created and returned immediately (no
    blocking at startup if Postgres isn't reachable yet), and connects in the background."""
    pool = ConnectionPool(conninfo, min_size=1, max_size=5, timeout=5, kwargs={"connect_timeout": 5}, open=False)
    pool.open(wait=False)
    return pool


@lru_cache
def ro_pool() -> ConnectionPool:
    """Read-only role: schema introspection (sql_agent.schema_prompt), SQL execution, /health probe."""
    return _pool(settings.database_url_ro)


@lru_cache
def rw_pool() -> ConnectionPool:
    """Read-write role: the query_log audit insert only."""
    return _pool(settings.database_url_rw)


def close_pools() -> None:
    """Release pooled connections on shutdown; safe to call even if a pool was never used."""
    for pool in (ro_pool, rw_pool):
        if pool.cache_info().currsize:
            pool().close()
