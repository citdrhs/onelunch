# postgres when configured, otherwise local sqlite file
import os
import re
import sqlite3
import threading
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent / ".env")
except ImportError:
    pass

_CONNECTOR = None
try:
    import psycopg

    _CONNECTOR = "psycopg3"
except ImportError:
    try:
        import psycopg2 as psycopg

        _CONNECTOR = "psycopg2"
    except ImportError:
        _CONNECTOR = None

USE_SQLITE = False
_SQLITE_PATH = Path(__file__).parent / "dev_local.db"
_sqlite_local = threading.local()

NO_DATABASE_DRIVER = False


# postgres connection

def _pg_connect():
    from config import DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD

    timeout = int(os.environ.get("DB_CONNECT_TIMEOUT", "10"))
    if _CONNECTOR == "psycopg3":
        return psycopg.connect(
            host=DB_HOST,
            port=DB_PORT,
            dbname=DB_NAME,
            user=DB_USER,
            password=DB_PASSWORD,
            connect_timeout=timeout,
        )
    return psycopg.connect(
        host=DB_HOST,
        port=DB_PORT,
        database=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD,
        connect_timeout=timeout,
    )


def _try_postgres():
    if _CONNECTOR is None:
        return False
    try:
        from config import DB_PASSWORD

        if not (DB_PASSWORD or "").strip():
            return False
        conn = _pg_connect()
        conn.close()
        return True
    except Exception:
        return False


USE_SQLITE = not _try_postgres()
if USE_SQLITE:
    print("\n[DEV] Postgres unavailable - using SQLite at", _SQLITE_PATH, "\n")


def _pg_to_sqlite(sql: str) -> str:
    sql = re.sub(r"(?<!%)%s", "?", sql)
    sql = re.sub(r"\s+RETURNING\s+\w+", "", sql, flags=re.IGNORECASE)
    sql = re.sub(r"\bnow\(\)", "datetime('now')", sql, flags=re.IGNORECASE)
    sql = re.sub(r"\bCURRENT_DATE\b", "date('now')", sql, flags=re.IGNORECASE)
    sql = re.sub(r"::\w+", "", sql)
    sql = re.sub(r"\+ interval '1 day'", "+1", sql, flags=re.IGNORECASE)
    sql = re.sub(r"\bTRUE\b", "1", sql)
    sql = re.sub(r"\bFALSE\b", "0", sql)
    return sql


# sqlite shim for app code

class _SQLiteAdapter:
    def __init__(self, path: Path):
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._closed = False

    def cursor(self):
        return _CursorAdapter(self._conn.cursor())

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        if self._closed:
            return
        try:
            self._conn.close()
        except Exception:
            pass
        self._closed = True
        try:
            if getattr(_sqlite_local, "adapter", None) is self:
                delattr(_sqlite_local, "adapter")
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, *_):
        if exc_type:
            self.rollback()
        else:
            self.commit()


class _CursorAdapter:
    # makes with cursor blocks behave like psycopg

    def __init__(self, cur):
        self._cur = cur
        self.rowcount = 0

    @property
    def lastrowid(self):
        return self._cur.lastrowid

    def execute(self, sql, params=()):
        sql = _pg_to_sqlite(sql)
        self._cur.execute(sql, params or ())
        self.rowcount = self._cur.rowcount
        return self

    def fetchone(self):
        row = self._cur.fetchone()
        return tuple(row) if row else None

    def fetchall(self):
        return [tuple(r) for r in self._cur.fetchall()]

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass


# thread local sqlite

def get_connection():
    if USE_SQLITE:
        ad = getattr(_sqlite_local, "adapter", None)
        if ad is None or getattr(ad, "_closed", False):
            _sqlite_local.adapter = _SQLiteAdapter(_SQLITE_PATH)
        return _sqlite_local.adapter
    return _pg_connect()


def insert_returning_id(cursor, sql: str, params=()):
    if USE_SQLITE:
        sql = re.sub(r"\s+RETURNING\s+\w+", "", sql, flags=re.IGNORECASE)
    cursor.execute(sql, params)
    if USE_SQLITE:
        return cursor.lastrowid
    row = cursor.fetchone()
    return row[0] if row else None


def init_db():
    if USE_SQLITE:
        conn = get_connection()._conn
        conn.executescript(
            """
 CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'teacher',
                department TEXT,
                email_verified INTEGER NOT NULL DEFAULT 1,
                email_verification_token TEXT,
                email_verification_expires TEXT,
                club_name TEXT,
                default_lunch TEXT,
                default_floor INTEGER,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS rooms (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                number TEXT NOT NULL UNIQUE,
                teacher_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                office_hours TEXT,
                lunch_duty TEXT,
                club_meeting TEXT,
                department TEXT,
                floor INTEGER NOT NULL DEFAULT 1,
                status TEXT NOT NULL DEFAULT 'open',
                todays_note TEXT,
                note_set_date TEXT,
                label TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS availabilities (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                room_id INTEGER NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
                day TEXT NOT NULL,
                lunch TEXT NOT NULL,
                UNIQUE(room_id, day)
            );

            CREATE TABLE IF NOT EXISTS club_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                room_id INTEGER NOT NULL REFERENCES rooms(id),
                requested_by INTEGER REFERENCES users(id),
                club_name TEXT NOT NULL,
                day TEXT NOT NULL,
                lunch TEXT NOT NULL,
                half TEXT DEFAULT 'full',
                notes TEXT,
                requester_name TEXT,
                requester_email TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                reviewed_by INTEGER REFERENCES users(id),
                reviewed_at TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS room_audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                room_id INTEGER REFERENCES rooms(id),
                user_id INTEGER REFERENCES users(id),
                action TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS password_reset_tokens (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id),
                token TEXT NOT NULL UNIQUE,
                expires_at TEXT NOT NULL,
                used_at TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS student_favorites (
                user_id INTEGER NOT NULL REFERENCES users(id),
                room_id INTEGER NOT NULL REFERENCES rooms(id),
                PRIMARY KEY (user_id, room_id)
            );

            CREATE TABLE IF NOT EXISTS notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id),
                message TEXT NOT NULL,
                link TEXT,
                is_read INTEGER NOT NULL DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS department_defaults (
                department TEXT PRIMARY KEY,
                office_hours TEXT,
                lunch_duty TEXT,
                default_avail TEXT
            );
            """
        )
        conn.commit()
        _seed_map_catalog()
        return

    schema_path = Path(__file__).parent / "schema.sql"
    if not schema_path.exists():
        print("[WARN] schema.sql not found; Postgres schema not applied.")
        _seed_map_catalog()
        return
    sql_text = schema_path.read_text(encoding="utf-8")
    try:
        import sqlparse

        statements = [s.strip() for s in sqlparse.split(sql_text) if s.strip()]
    except ImportError:
        statements = [sql_text.strip()]

    conn = _pg_connect()
    try:
        auto = True
        if _CONNECTOR == "psycopg3":
            conn.autocommit = True
        elif hasattr(conn, "autocommit"):
            conn.autocommit = True
        elif hasattr(conn, "set_session"):
            conn.set_session(autocommit=True)
        else:
            auto = False
        with conn.cursor() as cur:
            for stmt in statements:
                if stmt:
                    cur.execute(stmt)
        if not auto and hasattr(conn, "commit"):
            conn.commit()
    except Exception as e:
        print(f"[WARN] Postgres init_db failed: {e}")
    finally:
        if _CONNECTOR == "psycopg3":
            try:
                conn.autocommit = False
            except Exception:
                pass
        elif hasattr(conn, "autocommit"):
            try:
                conn.autocommit = False
            except Exception:
                pass
        conn.close()

    _seed_map_catalog()


def _seed_map_catalog():
    try:
        from map_rooms import seed_map_rooms

        seed_map_rooms()
    except Exception as e:
        print(f"[WARN] seed_map_rooms: {e}")


def test_connection():
    try:
        conn = get_connection()
        if not USE_SQLITE:
            conn.close()
        return True
    except Exception:
        return False
