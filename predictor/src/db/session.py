from contextlib import contextmanager
from typing import Any, Generator

import psycopg2
import psycopg2.extras
import psycopg2.pool

_pool: psycopg2.pool.ThreadedConnectionPool | None = None


def init_pool(postgres_url: str, min_conn: int = 2, max_conn: int = 10) -> None:
    global _pool
    _pool = psycopg2.pool.ThreadedConnectionPool(min_conn, max_conn, postgres_url)


@contextmanager
def get_conn() -> Generator[psycopg2.extensions.connection, None, None]:
    assert _pool is not None, "Pool not initialised — call init_pool() first"
    conn = _pool.getconn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        _pool.putconn(conn)


def execute(sql: str, params: Any = None) -> None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)


def execute_returning(sql: str, params: Any = None) -> Any:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchone()[0]


def fetch_one(sql: str, params: Any = None) -> dict | None:
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            row = cur.fetchone()
            return dict(row) if row else None


def fetch_all(sql: str, params: Any = None) -> list[dict]:
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            return [dict(r) for r in cur.fetchall()]
