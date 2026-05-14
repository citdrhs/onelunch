# Onelunch

A lunch room availability tracker for schools. Teachers set which days their room is open, students see what's available today.

## What it does

- Teachers log in and mark which lunch periods their room is available
- Students see a live map/list of open rooms for today's lunch
- Clubs can request room space through a simple form
- Admins can manage users, rooms, and review club requests

## Running locally

1. Clone the repo
2. Install dependencies: `pip install -r requirements.txt`
3. Copy `.env.example` to `.env` and fill in your settings (or leave blank to use SQLite locally)
4. Run: `python app.py`
5. Open http://127.0.0.1:5000

No database setup needed locally — the app falls back to SQLite automatically if Postgres isn't configured.

For dev login, use email `admin` and password `admin`.

## Environment variables

| Variable | Description | Required |
|---|---|---|
| DATABASE_URL or DB_* vars | Postgres connection | No (SQLite used if missing) |
| SECRET_KEY | Flask session secret | Recommended in prod |
| MAIL_USERNAME / MAIL_PASSWORD | SMTP for email sending | No (emails logged to console) |
| MAIL_SERVER / MAIL_PORT | SMTP server settings | No |

## Stack

- Python / Flask
- PostgreSQL (prod) / SQLite (local dev)
- Vanilla JS + CSS (no frontend framework)

## Deploying

Set your Postgres `DB_*` environment variables and `SECRET_KEY`. Run with gunicorn:

```bash
gunicorn app:app
```

Teacher email verification requires SMTP config. Without it, verification codes are printed to the console.

## Notes

- Teacher accounts must use a `@henrico.k12.va.us` email (school domain restriction)
- The `admin`/`admin` dev login is only active when Postgres is unavailable — it won't work in production
