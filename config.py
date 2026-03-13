# Onelunch App — database and app config
import os

# Your PostgreSQL database (drhscit.org)
DB_HOST = os.environ.get("DB_HOST", "drhscit.org")
DB_NAME = os.environ.get("DB_NAME", "shahnrdb")
DB_USER = os.environ.get("DB_USER", "shahnr")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "2003")
DB_PORT = int(os.environ.get("DB_PORT", 5432))

SECRET_KEY = os.environ.get("SECRET_KEY", "onelunch-dev-secret-change-in-production")

# Email configuration (set MAIL_USERNAME and MAIL_PASSWORD to send real emails)
# Gmail: use an App Password (Google Account → Security → 2-Step Verification → App passwords)
MAIL_SERVER = os.environ.get("MAIL_SERVER", "smtp.gmail.com")
MAIL_PORT = int(os.environ.get("MAIL_PORT", 587))
MAIL_USE_TLS = os.environ.get("MAIL_USE_TLS", "true").lower() in ("true", "1", "yes")
MAIL_USERNAME = os.environ.get("MAIL_USERNAME", "").strip() or None
MAIL_PASSWORD = os.environ.get("MAIL_PASSWORD", "").strip() or None
MAIL_DEFAULT_SENDER = os.environ.get("MAIL_DEFAULT_SENDER", "").strip() or MAIL_USERNAME


def get_database_url():
    return f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
