"""Postgres access helpers.

The serving API uses a small connection pool (created lazily) to keep
per-request latency low. Worker scripts use one-off connections.
"""
from __future__ import annotations

from contextlib import contextmanager

import psycopg2
import psycopg2.extras
from psycopg2.pool import SimpleConnectionPool

from app import config

_pool: SimpleConnectionPool | None = None


def connect(maintenance: bool = False):
    """Open a single new connection (used by workers and the migration)."""
    cfg = config.maintenance_db_config() if maintenance else config.db_config()
    return psycopg2.connect(**cfg)


def _get_pool() -> SimpleConnectionPool:
    global _pool
    if _pool is None:
        _pool = SimpleConnectionPool(minconn=1, maxconn=8, **config.db_config())
    return _pool


@contextmanager
def pooled_cursor(dict_rows: bool = True):
    """Yield a cursor from the pool, committing on success.

    Used by the API request path so we reuse connections instead of paying
    TCP + auth setup on every request (helps the p95 < 200ms SLA).
    """
    pool = _get_pool()
    conn = pool.getconn()
    try:
        factory = psycopg2.extras.RealDictCursor if dict_rows else None
        with conn.cursor(cursor_factory=factory) as cur:
            yield cur
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        pool.putconn(conn)


def close_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.closeall()
        _pool = None
