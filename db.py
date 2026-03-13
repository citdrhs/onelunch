"""
PostgreSQL connection and schema. Uses psycopg (v3) or psycopg2.
Falls back to demo mode only if no driver is available.
"""
import os
import pathlib

# Prefer psycopg (v3) — has Windows binary wheels; else use psycopg2
_connector = None
try:
    import psycopg
    _connector = "psycopg3"
except ImportError:
    try:
        import psycopg2 as psycopg
        _connector = "psycopg2"
    except ImportError:
        pass

DEMO_MODE = _connector is None or os.environ.get("DEMO_MODE", "").strip() == "1"

if not DEMO_MODE:
    from config import DB_HOST, DB_NAME, DB_USER, DB_PASSWORD, DB_PORT


class _MockCursor:
    def __enter__(self): return self
    def __exit__(self, *a): pass
    def execute(self, *a, **k): pass
    def fetchone(self): return None
    def fetchall(self): return []


class _MockConn:
    def __enter__(self): return self
    def __exit__(self, *a): pass
    def cursor(self): return _MockCursor()
    def commit(self): pass
    def close(self): pass


def get_connection():
    """Return a new connection to the database, or mock in demo mode."""
    if DEMO_MODE:
        return _MockConn()
    if _connector == "psycopg3":
        return psycopg.connect(
            host=DB_HOST,
            dbname=DB_NAME,
            user=DB_USER,
            password=DB_PASSWORD,
            port=DB_PORT,
        )
    # psycopg2
    return psycopg.connect(
        host=DB_HOST,
        database=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD,
        port=DB_PORT,
    )


def init_db():
    """Create or update schema in the database. Safe to run multiple times."""
    if DEMO_MODE:
        return
    schema_path = pathlib.Path(__file__).parent / "schema.sql"
    if not schema_path.exists():
        raise RuntimeError("schema.sql not found")
    sql = schema_path.read_text()
    conn = get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(sql)
    finally:
        conn.close()


def test_connection():
    if DEMO_MODE:
        return True
    try:
        conn = get_connection()
        conn.close()
        return True
    except Exception:
        return False
