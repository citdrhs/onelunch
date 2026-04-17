import os
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent / ".env")
except ImportError:
    pass

# Defaults target the school Postgres server; override any value with .env or the environment.
DB_HOST = os.environ.get("DB_HOST", "drhscit.org")
DB_NAME = os.environ.get("DB_NAME", "onelunch")
DB_USER = os.environ.get("DB_USER", "onelunch")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "")
DB_PORT = int(os.environ.get("DB_PORT", "5433"))

SECRET_KEY = os.environ.get("SECRET_KEY", "")
WTF_CSRF_SECRET_KEY = os.environ.get("WTF_CSRF_SECRET_KEY") or os.environ.get("SECRET_KEY") or "csrf-dev-only-change-me"

BASE_URL = os.environ.get("BASE_URL", "http://127.0.0.1:5000").rstrip("/")


def get_database_url():
    return f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
