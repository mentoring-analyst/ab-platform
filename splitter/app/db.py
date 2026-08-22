import os
from contextlib import contextmanager

from psycopg2.extras import RealDictCursor
from psycopg2.pool import ThreadedConnectionPool

_pool = None


def _get_pool():
    global _pool
    if _pool is None:
        _pool = ThreadedConnectionPool(1, 5, dsn=os.environ["PG_DSN"])
    return _pool


@contextmanager
def conn():
    c = _get_pool().getconn()
    try:
        yield c
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        _get_pool().putconn(c)


def fetch_all(sql, params=None):
    with conn() as c:
        with c.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, params or {})
            return cur.fetchall()


def fetch_one(sql, params=None):
    rows = fetch_all(sql, params)
    return rows[0] if rows else None


def execute(sql, params=None):
    with conn() as c:
        with c.cursor() as cur:
            cur.execute(sql, params or {})


def sim_now():
    row = fetch_one("SELECT virtual_now FROM ab.sim_clock WHERE id = 1")
    return row["virtual_now"] if row else None
