"""
PostgreSQL connection and schema. Uses psycopg (v3) or psycopg2.
If no driver is installed, uses an in-memory mock so the app can import.
"""
import os
import pathlib

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

# Only when Postgres driver is missing — not a user-facing "demo mode".
NO_DATABASE_DRIVER = _connector is None

if not NO_DATABASE_DRIVER:
    from config import DB_HOST, DB_NAME, DB_USER, DB_PASSWORD, DB_PORT

_CONNECT_TIMEOUT = int(os.environ.get("DB_CONNECT_TIMEOUT", "5"))


class _MockCursor:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass

    def execute(self, *a, **k):
        pass

    def fetchone(self):
        return None

    def fetchall(self):
        return []


class _MockConn:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass

    def cursor(self):
        return _MockCursor()

    def commit(self):
        pass

    def close(self):
        pass


def get_connection():
    if NO_DATABASE_DRIVER:
        return _MockConn()
    if _connector == "psycopg3":
        return psycopg.connect(
            host=DB_HOST,
            dbname=DB_NAME,
            user=DB_USER,
            password=DB_PASSWORD,
            port=DB_PORT,
            connect_timeout=_CONNECT_TIMEOUT,
        )
    return psycopg.connect(
        host=DB_HOST,
        database=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD,
        port=DB_PORT,
        connect_timeout=_CONNECT_TIMEOUT,
    )


def init_db():
    """Create or update schema in the database. Safe to run multiple times."""
    if NO_DATABASE_DRIVER:
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
    if NO_DATABASE_DRIVER:
        return True
    try:
        conn = get_connection()
        conn.close()
        return True
    except Exception:
        return False
