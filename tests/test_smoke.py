import os

import pytest

from db import USE_SQLITE, get_connection, init_db, test_connection as db_ping


@pytest.fixture
def client():
    import app as app_module

    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


def test_database_reachable():
    assert db_ping() is True


def test_postgres_configured_for_deployment():
    if USE_SQLITE:
        pytest.skip("Set DB_PASSWORD in .env to run against Postgres (drhscit.org).")
    init_db()
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT COUNT(*) FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name = 'users'
            """
        )
        assert cur.fetchone()[0] == 1
    finally:
        conn.close()


def test_index_ok(client):
    rv = client.get("/")
    assert rv.status_code == 200


def test_login_page_ok(client):
    rv = client.get("/login")
    assert rv.status_code == 200


def test_default_admin_exists_when_postgres():
    if USE_SQLITE:
        pytest.skip("Postgres deployment check only.")
    init_db()
    import app as app_module  # noqa: F401 — ensure bootstrap

    email = (os.environ.get("DEFAULT_ADMIN_EMAIL") or "admin@admin.local").strip().lower()
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT 1 FROM users WHERE lower(email) = %s AND role = 'admin'",
            (email,),
        )
        assert cur.fetchone() is not None
    finally:
        conn.close()
