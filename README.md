# Onelunch

Simple Flask app for teachers to publish room availability during lunch and students to find open rooms.

Requirements
- Python 3.10+
- PostgreSQL available and reachable from the machine

Setup (local)
1. Create a Python virtual environment and activate it:

```bash
python -m venv .venv
.\.venv\Scripts\activate
```

2. Install dependencies

```bash
pip install -r requirements.txt
```

3. Configure environment variables (optional). Defaults are in `config.py`.

```powershell
$env:DB_HOST = "your-db-host"
$env:DB_NAME = "your-db-name"
$env:DB_USER = "your-db-user"
$env:DB_PASSWORD = "your-db-pass"
$env:DB_PORT = "5432"
$env:SECRET_KEY = "change-this"
```

4. Initialize schema and seed sample data

```bash
python seed.py
```

5. Run the app

```bash
python app.py
```

6. Open http://localhost:5000

Testing

Run pytest to execute basic tests (these expect the DB to be reachable):

```bash
pip install pytest
pytest -q
```

Notes
- This project uses raw SQL (see `schema.sql`) and `psycopg2` for DB access.
- If you want migrations, convert schema into SQLAlchemy models and add Flask-Migrate.
