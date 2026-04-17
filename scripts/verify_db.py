"""One-off and CI: apply schema, list tables, ensure admin row exists."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db import get_connection, init_db, USE_SQLITE  # noqa: E402


def main():
    print("USE_SQLITE:", USE_SQLITE)
    init_db()
    import app  # noqa: F401, E402 — runs default-admin bootstrap on import
    conn = get_connection()
    try:
        cur = conn.cursor()
        if USE_SQLITE:
            cur.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )
        else:
            cur.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename"
            )
        tables = [r[0] for r in cur.fetchall()]
        print("tables:", ", ".join(tables))
        cur.execute("SELECT COUNT(*) FROM users WHERE role = %s", ("admin",))
        print("admin users:", cur.fetchone()[0])
    finally:
        conn.close()


if __name__ == "__main__":
    main()
