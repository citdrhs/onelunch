import os
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent / ".env")
except ImportError:
    pass

import json
from datetime import datetime, timezone
from functools import wraps

from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    flash,
    jsonify,
    session,
    abort,
    send_from_directory,
    Response,
    stream_with_context,
    has_request_context,
)
from flask_wtf.csrf import CSRFProtect, generate_csrf
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from werkzeug.security import generate_password_hash, check_password_hash

_DEV_DEMO_PASSWORD_HASH = generate_password_hash("admin")

from db import get_connection, init_db, NO_DATABASE_DRIVER, USE_SQLITE, insert_returning_id
import config

# helpers

def _parse_default_avail(raw):
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw) if raw else {}
        except Exception:
            return {}
    return {}


def room_dropdown_caption(number, label=None):
    n = (number or "").strip()
    if not n:
        return ""
    extra = (label or "").strip()
    if extra:
        return extra
    if n.isdigit():
        return f"Room {n}"
    return n


def _coerce_aware_dt(val):
    if val is None:
        return None
    if isinstance(val, str):
        try:
            return datetime.fromisoformat(val.replace("Z", "+00:00"))
        except Exception:
            return None
    if getattr(val, "tzinfo", None):
        return val
    try:
        return val.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _flash_db_connection_failed():
    if session.get("dev_user"):
        flash(
            "PostgreSQL connection failed (often missing DB_PASSWORD in .env). "
            "Add your database password to .env, save, and restart the app.",
            "error",
        )
    else:
        flash(
            "Cannot connect to the database. Check DB_HOST, DB_USER, DB_PASSWORD, and DB_NAME in .env, then restart.",
            "error",
        )


def _dev_user_active():
    try:
        return has_request_context() and bool(session.get("dev_user"))
    except RuntimeError:
        return False


def _json_dev_no_db():
    return jsonify({"error": "Dev mode: database not available for this action"}), 503


app = Flask(__name__)

app.config.from_object(config)
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
_sk = getattr(config, "SECRET_KEY", None) or os.environ.get("SECRET_KEY")
app.secret_key = _sk if _sk else os.urandom(32)
if app.config.get("TESTING"):
    app.config["WTF_CSRF_ENABLED"] = False

csrf = CSRFProtect(app)
limiter = Limiter(get_remote_address, app=app, default_limits=[], storage_uri="memory://")
# expose csrf helper to every template
app.jinja_env.globals["csrf_token"] = generate_csrf


@app.errorhandler(429)
def _rate_limit_exceeded(_e):
    if request.path.startswith("/api/"):
        return jsonify({"error": "Too many requests"}), 429
    flash("Too many attempts. Please wait.", "error")
    return redirect(request.referrer or url_for("index"))

WEEKDAY_TO_DAY = {"0": "M", "1": "T", "2": "W", "3": "R", "4": "F"}

# nightly note cleanup

def clear_stale_notes():
    if _dev_user_active():
        return
    try:
        conn = get_connection()
    except Exception as e:
        app.logger.warning("clear_stale_notes: skipped (database unavailable): %s", e)
        return
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE rooms SET todays_note = NULL, note_set_date = NULL "
                "WHERE note_set_date IS NOT NULL AND note_set_date < CURRENT_DATE"
            )
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _ensure_default_admin():
    if NO_DATABASE_DRIVER:
        return
    email = (os.environ.get("DEFAULT_ADMIN_EMAIL") or "admin@admin.local").strip().lower()
    password = os.environ.get("DEFAULT_ADMIN_PASSWORD") or "admin"
    try:
        conn = get_connection()
    except Exception as e:
        app.logger.warning("ensure_default_admin: database unavailable: %s", e)
        return
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM users WHERE lower(email) = %s", (email,))
            if cur.fetchone():
                return
            cur.execute(
                """INSERT INTO users (name, email, password_hash, role, email_verified)
                   VALUES (%s,%s,%s,'admin', TRUE)""",
                ("Admin", email, generate_password_hash(password)),
            )
        conn.commit()
        app.logger.info("Created default admin user %s", email)
    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        app.logger.warning("ensure_default_admin failed: %s", e)
    finally:
        try:
            conn.close()
        except Exception:
            pass


try:
    init_db()
except Exception as e:
    try:
        app.logger.error("Database init failed: %s", e)
    except Exception:
        pass

try:
    _ensure_default_admin()
except Exception as e:
    try:
        app.logger.warning("Default admin bootstrap skipped: %s", e)
    except Exception:
        pass


def get_user_by_email(email):
    if (email or "").strip().lower() == "admin":
        return {
            "id": 0,
            "name": "Dev Admin",
            "email": "admin",
            "password_hash": _DEV_DEMO_PASSWORD_HASH,
            "role": "admin",
            "department": None,
            "email_verified": True,
        }
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, name, email, password_hash, role, department, email_verified FROM users WHERE email = %s",
                (email,),
            )
            row = cur.fetchone()
            if row:
                return {
                    "id": row[0],
                    "name": row[1],
                    "email": row[2],
                    "password_hash": row[3],
                    "role": row[4],
                    "department": row[5] if len(row) > 5 else None,
                    "email_verified": bool(row[6]) if len(row) > 6 else True,
                }
    except Exception:
        pass
    finally:
        conn.close()
    return None


def get_user_by_id(user_id):
    if user_id == 0:
        return {
            "id": 0,
            "name": "Dev Admin",
            "email": "admin",
            "role": "admin",
            "department": None,
            "email_verified": True,
            "club_name": None,
            "default_lunch": None,
            "default_floor": None,
        }
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id, name, email, role, department, email_verified, club_name, default_lunch, default_floor
                   FROM users WHERE id = %s""",
                (user_id,),
            )
            row = cur.fetchone()
            if row:
                return {
                    "id": row[0],
                    "name": row[1],
                    "email": row[2],
                    "role": row[3],
                    "department": row[4] if len(row) > 4 else None,
                    "email_verified": bool(row[5]) if len(row) > 5 else True,
                    "club_name": row[6] if len(row) > 6 else None,
                    "default_lunch": row[7] if len(row) > 7 else None,
                    "default_floor": row[8] if len(row) > 8 else None,
                }
    except Exception:
        pass
    finally:
        conn.close()
    return None


def get_current_day_lunch():
    day_param = request.args.get("day")
    lunch_param = request.args.get("lunch")
    if day_param and day_param in "MTWRF":
        day = day_param
    else:
        w = datetime.utcnow().weekday()
        day = WEEKDAY_TO_DAY.get(str(w), "M")
    lunch = lunch_param if lunch_param in ("A", "B") else "B"
    return day, lunch


def build_rooms_for_display(include_availability_map=True):
    if _dev_user_active():
        return {}
    out = {}
    conn = None
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute(
                """SELECT r.id, r.number, r.office_hours, r.lunch_duty, r.club_meeting, u.name, r.department, r.teacher_id, r.status, r.floor, r.todays_note, r.note_set_date, r.label
                   FROM rooms r LEFT JOIN users u ON r.teacher_id = u.id ORDER BY r.floor, r.number"""
            )
            rows = cur.fetchall()
            for row in rows:
                room_id, number = row[0], row[1]
                cur.execute(
                    "SELECT day, lunch FROM availabilities WHERE room_id = %s",
                    (room_id,),
                )
                avail_rows = cur.fetchall()
                avail_map = {a[0]: a[1] for a in avail_rows} if include_availability_map else None
                available_days = [a[0] for a in avail_rows if a[1] in ("A", "B")]
                status = row[8] if len(row) > 8 and row[8] else "open"
                floor = row[9] if len(row) > 9 and row[9] is not None else 1
                note = row[10] if len(row) > 10 else None
                note_date = row[11] if len(row) > 11 else None
                rlabel = row[12] if len(row) > 12 else None
                out[number] = {
                    "room_id": room_id,
                    "teacher_name": row[5],
                    "room": number,
                    "office_hours": row[2],
                    "lunch_duty": row[3],
                    "club_meeting": row[4],
                    "department": row[6],
                    "teacher_id": row[7],
                    "status": status,
                    "floor": floor,
                    "available_days": available_days,
                    "todays_note": note,
                    "note_set_date": str(note_date) if note_date else None,
                    "label": rlabel,
                    "dropdown_caption": room_dropdown_caption(number, rlabel),
                }
                if avail_map is not None:
                    out[number]["availability_map"] = avail_map
    except Exception as e:
        try:
            app.logger.warning("build_rooms_for_display failed: %s", e)
        except Exception:
            pass
        return {}
    finally:
        if conn is not None:
            conn.close()
    return out


def room_has_lunch_for_day(room_data, day, lunch):
    if room_data.get("status") == "closed":
        return False
    am = room_data.get("availability_map") or {}
    return am.get(day) == lunch


def query_room_by_teacher(user_id):
    if user_id == 0:
        return None
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id, number, office_hours, lunch_duty, club_meeting, department, status, floor, todays_note, note_set_date
                   FROM rooms WHERE teacher_id = %s""",
                (user_id,),
            )
            r = cur.fetchone()
            if not r:
                return None
            room = {
                "id": r[0],
                "number": r[1],
                "office_hours": r[2],
                "lunch_duty": r[3],
                "club_meeting": r[4],
                "department": r[5] if len(r) > 5 else None,
                "status": r[6] if len(r) > 6 else "open",
                "floor": r[7] if len(r) > 7 and r[7] is not None else 1,
                "todays_note": r[8] if len(r) > 8 else None,
                "note_set_date": str(r[9])[:10] if len(r) > 9 and r[9] else None,
            }
            cur.execute("SELECT day, lunch FROM availabilities WHERE room_id = %s", (room["id"],))
            room["avail_map"] = {row[0]: row[1] for row in cur.fetchall()}
            return room
    finally:
        conn.close()


def get_room_teacher_id(room_number):
    if _dev_user_active():
        return None
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT teacher_id FROM rooms WHERE number = %s", (room_number,))
            r = cur.fetchone()
            return r[0] if r and r[0] else None
    finally:
        conn.close()


def _db_user_fk(user_id):
    # demo login is not stored so never reference user id zero in fk columns
    if user_id is None or user_id == 0:
        return None
    return user_id


def log_room_audit(room_id, user_id, action):
    if not user_id or user_id == 0:
        return
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO room_audit_log (room_id, user_id, action) VALUES (%s,%s,%s)",
                (room_id, user_id, action),
            )
        conn.commit()
    except Exception:
        pass
    finally:
        conn.close()


# decorators

def login_required(f):
    @wraps(f)
    def wrapped(*args, **kwargs):
        if "user_id" not in session:
            flash("Please log in.", "error")
            return redirect(url_for("login", next=request.url))
        return f(*args, **kwargs)
    return wrapped


def admin_required(f):
    @wraps(f)
    def wrapped(*args, **kwargs):
        if "user_id" not in session or session.get("role") != "admin":
            flash("Admin access required.", "error")
            return redirect(url_for("index"))
        return f(*args, **kwargs)
    return wrapped


def require_verified_teacher(f):
    @wraps(f)
    def wrapped(*args, **kwargs):
        if session.get("dev_user"):
            return f(*args, **kwargs)
        if "user_id" not in session:
            flash("Please log in.", "error")
            return redirect(url_for("login", next=request.url))
        role = session.get("role")
        if role == "admin":
            return f(*args, **kwargs)
        if role != "teacher":
            flash("This page is for teachers.", "error")
            return redirect(url_for("index"))
        user = get_user_by_id(session["user_id"])
        if not user:
            session.clear()
            return redirect(url_for("login"))
        if not user.get("email_verified", False):
            session["unverified_teacher_email"] = user.get("email", "")
            flash(
                "Your teacher account is not active yet. Ask an administrator to verify your account.",
                "error",
            )
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return wrapped


def _json_teacher_unverified():
    return jsonify({
        "error": "Your teacher account is not active yet. Ask an administrator to verify your account.",
    }), 403


def _half_label(raw):
    k = (raw or "full").strip()
    return {"first_half": "First half", "second_half": "Second half", "full": "Full lunch"}.get(k, k.replace("_", " ").title())


# pages

@app.route("/")
def index():
    clear_stale_notes()
    rooms = build_rooms_for_display(include_availability_map=True)
    current_day, current_lunch = get_current_day_lunch()
    return render_template(
        "index.html",
        rooms=rooms,
        current_day=current_day,
        current_lunch=current_lunch,
    )


@app.route("/dashboard/teacher")
@login_required
def teacher_dashboard():
    return redirect(url_for("teacher"))


def _register_teacher(name, email, password, department=None):
    if not name or not email or not password:
        flash("Name, email, and password are required.", "error")
        return False
    email_norm = (email or "").strip().lower()
    if email_norm == "admin":
        flash("For the demo account, use the Log in tab with admin / admin, not sign up.", "info")
        return False
    if not email.endswith("@henrico.k12.va.us"):
        flash("Teacher accounts must use a @henrico.k12.va.us email address.", "error")
        return False
    if get_user_by_email(email):
        flash("Email already registered.", "error")
        return False

    password_hash = generate_password_hash(password)

    conn = get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO users (name, email, password_hash, role, department, email_verified, email_verification_token, email_verification_expires)
                       VALUES (%s,%s,%s,'teacher',%s,TRUE,NULL,NULL) RETURNING id""",
                    (name, email, password_hash, department),
                )
    except Exception as e:
        app.logger.exception("Teacher registration failed: %s", e)
        flash("Registration failed. Please try again.", "error")
        return False
    finally:
        conn.close()

    flash("Account created. You can log in with your email and password.", "success")
    return True


def _register_teacher_form_values():
    return {
        "name": request.form.get("name", "").strip(),
        "email": request.form.get("email", "").strip().lower(),
        "department": request.form.get("department", "").strip(),
    }


@app.route("/register/teacher", methods=["GET", "POST"])
@limiter.limit("5 per minute")
def register_teacher():
    if request.method == "GET":
        return render_template("register_teacher.html", form={})
    name = request.form.get("name", "").strip()
    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")
    password_confirm = request.form.get("password_confirm", "")
    department = request.form.get("department", "").strip() or None
    if password != password_confirm:
        flash("Passwords do not match.", "error")
        return render_template("register_teacher.html", form=_register_teacher_form_values())
    if _register_teacher(name, email, password, department):
        return redirect(url_for("login"))
    return render_template("register_teacher.html", form=_register_teacher_form_values())


@app.route("/verify-email", methods=["GET"])
def verify_teacher_email_link():
    token = request.args.get("token")
    if not token or len(token) < 16:
        flash("Invalid verification link.", "error")
        return redirect(url_for("teacher"))
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id, email, email_verified, email_verification_expires FROM users
                   WHERE email_verification_token = %s""",
                (token,),
            )
            row = cur.fetchone()
    finally:
        conn.close()
    if not row:
        flash("Invalid or unknown verification link.", "error")
        return redirect(url_for("teacher"))
    user_id, em, already_verified, expires = row[0], row[1], row[2], row[3]
    if already_verified:
        flash("That email is already verified. You can log in.", "info")
        return redirect(url_for("teacher"))
    now = datetime.now(timezone.utc)
    if expires is not None:
        exp = _coerce_aware_dt(expires)
        if exp is not None and exp < now:
            flash("That verification link has expired.", "error")
            return redirect(url_for("teacher"))
    conn = get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE users SET email_verified = TRUE, email_verification_token = NULL,
                       email_verification_expires = NULL, updated_at = now() WHERE id = %s""",
                    (user_id,),
                )
    finally:
        conn.close()
    session.pop("pending_teacher_email", None)
    flash("Email verified. You can log in.", "success")
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
@limiter.limit("10 per minute")
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        if email == "admin" and password == "admin":
            session.clear()
            session["user_id"] = 0
            session["role"] = "admin"
            session["dev_user"] = True
            flash("Logged in as dev admin", "success")
            return redirect(url_for("teacher"))

        user = get_user_by_email(email)
        if user and check_password_hash(user["password_hash"], password):
            if user.get("role") == "teacher" and not user.get("email_verified", False):
                session["unverified_teacher_email"] = email
                flash(
                    "Your teacher account is not active yet. Ask an administrator to verify your account.",
                    "error",
                )
                return redirect(url_for("login"))
            session.pop("unverified_teacher_email", None)
            session.clear()
            session["user_id"] = user["id"]
            session["role"] = user["role"]
            if user["id"] == 0:
                session["dev_user"] = True
            flash("Logged in", "success")
            if user["role"] == "admin":
                return redirect(url_for("admin"))
            return redirect(url_for("teacher"))
        flash("Invalid credentials", "error")
        return redirect(url_for("login"))
    unverified_email = session.get("unverified_teacher_email")
    return render_template("login.html", unverified_email=unverified_email)


@app.route("/floorplan.png")
def floorplan_image():
    return send_from_directory(app.root_path, "floorplan.png")


@app.route("/logout")
def logout():
    session.clear()
    flash("Logged out", "info")
    return redirect(url_for("index"))


@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        flash(
            "This app does not send email. Log in and use Account to change your password, or ask an administrator for help.",
            "info",
        )
        return redirect(url_for("login"))
    return render_template("forgot_password.html")


@app.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    conn = get_connection()
    user_id = None
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT user_id FROM password_reset_tokens
                   WHERE token = %s AND expires_at > %s AND used_at IS NULL""",
                (token, datetime.now(timezone.utc)),
            )
            r = cur.fetchone()
            if r:
                user_id = r[0]
    finally:
        conn.close()
    if not user_id:
        flash("Invalid or expired reset link.", "error")
        return redirect(url_for("login"))
    if request.method == "POST":
        password = request.form.get("password", "")
        if len(password) < 6:
            flash("Password must be at least 6 characters.", "error")
            return redirect(request.url)
        password_hash = generate_password_hash(password)
        conn = get_connection()
        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute("UPDATE users SET password_hash = %s, updated_at = now() WHERE id = %s", (password_hash, user_id))
                    cur.execute("UPDATE password_reset_tokens SET used_at = now() WHERE token = %s", (token,))
        finally:
            conn.close()
        flash("Password updated. Please log in.", "success")
        return redirect(url_for("login"))
    return render_template("reset_password.html", token=token)


@app.route("/account", methods=["GET", "POST"])
@login_required
def account():
    user = get_user_by_id(session["user_id"])
    if request.method == "POST":
        if user and user.get("id") == 0:
            flash("The demo login has no saved account. Use a real user to change password or delete.", "info")
            return redirect(url_for("account"))
        action = request.form.get("action")
        if action == "change_password":
            current = request.form.get("current_password", "")
            new_pass = request.form.get("new_password", "")
            if not current or not new_pass:
                flash("Current and new password required.", "error")
                return redirect(url_for("account"))
            u = get_user_by_email(user["email"])
            if not u or not check_password_hash(u["password_hash"], current):
                flash("Current password is incorrect.", "error")
                return redirect(url_for("account"))
            if len(new_pass) < 6:
                flash("New password must be at least 6 characters.", "error")
                return redirect(url_for("account"))
            password_hash = generate_password_hash(new_pass)
            conn = get_connection()
            try:
                with conn.cursor() as cur:
                    cur.execute("UPDATE users SET password_hash = %s, updated_at = now() WHERE id = %s", (password_hash, user["id"]))
                conn.commit()
            finally:
                conn.close()
            flash("Password updated.", "success")
            return redirect(url_for("account"))
        if action == "delete_account":
            conn = get_connection()
            try:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM users WHERE id = %s", (user["id"],))
                conn.commit()
            finally:
                conn.close()
            session.clear()
            flash("Account deleted.", "info")
            return redirect(url_for("index"))
    return render_template("account.html", user=user)


@app.route("/account/preferences", methods=["POST"])
@login_required
def account_preferences():
    uid = session["user_id"]
    if uid == 0:
        flash("Preferences are not saved for the demo login.", "info")
        return redirect(url_for("account"))
    lunch = (request.form.get("default_lunch") or "").strip().upper()
    if lunch not in ("A", "B", ""):
        lunch = None
    floor_raw = (request.form.get("default_floor") or "").strip()
    floor_v = None
    if floor_raw in ("1", "2"):
        floor_v = int(floor_raw)
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE users SET default_lunch = %s, default_floor = %s, updated_at = now() WHERE id = %s",
                (lunch, floor_v, uid),
            )
        conn.commit()
        flash("Preferences saved.", "success")
    except Exception as e:
        app.logger.exception("account_preferences: %s", e)
        flash("Could not save preferences.", "error")
    finally:
        conn.close()
    return redirect(url_for("account"))


@app.route("/resend-verification", methods=["GET", "POST"])
def resend_verification():
    flash("Use the verification link from your signup email, or create a new account on the teacher signup page.", "info")
    return redirect(url_for("register_teacher"))


@app.route("/teacher", methods=["GET", "POST"])
def teacher():
    if "user_id" not in session:
        if request.method == "POST":
            flash("Please log in to save availability.", "error")
            return redirect(url_for("login"))
        return render_template(
            "teacher.html",
            view="auth",
            user=None,
            user_room=None,
            rooms=[],
            departments=[],
            confirm_overwrite=None,
            pending_list=[],
        )

    role = session.get("role")
    if role not in ("teacher", "admin"):
        return redirect(url_for("index"))

    if role == "teacher":
        user_chk = get_user_by_id(session["user_id"])
        if not user_chk:
            session.clear()
            return redirect(url_for("login"))
        if not user_chk.get("email_verified", False):
            session["unverified_teacher_email"] = user_chk.get("email", "")
            flash(
                "Your teacher account is not active yet. Ask an administrator to verify your account.",
                "error",
            )
            return redirect(url_for("login"))

    user = get_user_by_id(session["user_id"])

    if request.method == "POST":
        if session.get("dev_user"):
            flash("Dev mode: availability not saved to DB.", "success")
            return redirect(url_for("teacher"))
        room_number = request.form.get("room", "").strip()
        confirm_overwrite = request.form.get("confirm_overwrite") == "1"
        office_hours = request.form.get("office_hours", "").strip() or None
        lunch_duty = request.form.get("lunch_duty", "").strip() or None
        club_meeting = request.form.get("club_meeting", "").strip() or None
        department = request.form.get("department", "").strip() or None
        if not room_number:
            flash("Room number is required.", "error")
            return redirect(url_for("teacher"))

        fk_teacher = _db_user_fk(user["id"])
        try:
            existing_teacher_id = get_room_teacher_id(room_number)
        except Exception as e:
            app.logger.warning("get_room_teacher_id: %s", e)
            _flash_db_connection_failed()
            return redirect(url_for("teacher"))
        if existing_teacher_id and existing_teacher_id != user["id"] and not confirm_overwrite:
            flash(
                f"Room {room_number} is already assigned to another teacher. Check the box to confirm overwrite.",
                "error",
            )
            return redirect(url_for("teacher", confirm_overwrite=room_number))

        try:
            conn = get_connection()
            try:
                with conn:
                    with conn.cursor() as cur:
                        cur.execute("SELECT id FROM rooms WHERE number = %s", (room_number,))
                        r = cur.fetchone()
                        if r:
                            room_id = r[0]
                            cur.execute(
                                """UPDATE rooms SET teacher_id=%s, office_hours=%s, lunch_duty=%s, club_meeting=%s, department=%s, updated_at=now()
                                   WHERE id=%s""",
                                (fk_teacher, office_hours, lunch_duty, club_meeting, department, room_id),
                            )
                            if user["id"] != 0:
                                log_room_audit(room_id, user["id"], "update")
                        else:
                            room_id = insert_returning_id(
                                cur,
                                """INSERT INTO rooms (number, teacher_id, office_hours, lunch_duty, club_meeting, department, floor, status)
                                   VALUES (%s,%s,%s,%s,%s,%s,1,'open') RETURNING id""",
                                (room_number, fk_teacher, office_hours, lunch_duty, club_meeting, department),
                            )
                            if user["id"] != 0:
                                log_room_audit(room_id, user["id"], "create")

                        cur.execute("DELETE FROM availabilities WHERE room_id = %s", (room_id,))
                        for d in ["M", "T", "W", "R", "F"]:
                            val = request.form.get(f"day_{d}", "N")
                            if val not in ("A", "B", "N", "AB"):
                                val = "N"
                            cur.execute("INSERT INTO availabilities (room_id, day, lunch) VALUES (%s,%s,%s)", (room_id, d, val))

                        note_text = (request.form.get("availability_note") or "").strip()
                        if note_text:
                            if len(note_text) > 200:
                                note_text = note_text[:200]
                            cur.execute("SELECT teacher_id FROM rooms WHERE id = %s", (room_id,))
                            rt = cur.fetchone()
                            room_tid = rt[0] if rt else None
                            uid = user["id"]
                            can_note = session.get("role") == "admin" or (
                                session.get("role") == "teacher" and room_tid == uid
                            )
                            if can_note:
                                cur.execute(
                                    "UPDATE rooms SET todays_note = %s, note_set_date = CURRENT_DATE, updated_at = now() WHERE id = %s",
                                    (note_text, room_id),
                                )
            finally:
                conn.close()
        except Exception as e:
            app.logger.exception("teacher save availability: %s", e)
            _flash_db_connection_failed()
            return redirect(url_for("teacher"))

        flash("Availability saved.", "success")
        return redirect(url_for("teacher"))

    if session.get("dev_user"):
        confirm_overwrite = request.args.get("confirm_overwrite")
        return render_template(
            "teacher.html",
            view="dashboard",
            user=user,
            user_room=None,
            rooms=[],
            departments=[],
            confirm_overwrite=confirm_overwrite,
            pending_list=[],
        )

    user_room = None
    if user["id"] != 0:
        try:
            user_room = query_room_by_teacher(user["id"])
        except Exception as e:
            app.logger.warning("query_room_by_teacher: %s", e)

    rooms = []
    db_unreachable = False
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT r.id, r.number, r.office_hours, r.lunch_duty, r.club_meeting, u.name, r.floor, r.status, r.label
                       FROM rooms r LEFT JOIN users u ON r.teacher_id = u.id ORDER BY r.floor, r.number"""
                )
                for r in cur.fetchall():
                    num = r[1]
                    lbl = r[8] if len(r) > 8 else None
                    rooms.append({
                        "id": r[0],
                        "number": num,
                        "office_hours": r[2],
                        "lunch_duty": r[3],
                        "club_meeting": r[4],
                        "teacher_name": r[5],
                        "floor": r[6] if len(r) > 6 and r[6] is not None else 1,
                        "status": r[7] if len(r) > 7 and r[7] else "open",
                        "label": lbl,
                        "dropdown_caption": room_dropdown_caption(num, lbl),
                    })
        finally:
            conn.close()
    except Exception as e:
        app.logger.warning("teacher dashboard (rooms): %s", e)
        db_unreachable = True

    departments = []
    if not db_unreachable:
        try:
            conn = get_connection()
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT department, office_hours, lunch_duty, default_avail FROM department_defaults")
                    for row in cur.fetchall():
                        departments.append({
                            "department": row[0],
                            "office_hours": row[1],
                            "lunch_duty": row[2],
                            "default_avail": _parse_default_avail(row[3] if len(row) > 3 else None),
                        })
            finally:
                conn.close()
        except Exception:
            pass

    confirm_overwrite = request.args.get("confirm_overwrite")

    pending_list = []
    if not db_unreachable:
        if role == "teacher":
            uid = session["user_id"]
            try:
                conn = get_connection()
                try:
                    with conn.cursor() as cur:
                        cur.execute(
                            """SELECT cr.id, cr.club_name, r.number,
                                      COALESCE(cr.requester_name, u.name, ''), cr.day, cr.lunch,
                                      COALESCE(cr.half, 'full'), COALESCE(cr.notes, '')
                               FROM club_requests cr
                               JOIN rooms r ON cr.room_id = r.id
                               LEFT JOIN users u ON cr.requested_by = u.id
                               WHERE r.teacher_id = %s AND cr.status = 'pending' ORDER BY cr.created_at DESC""",
                            (uid,),
                        )
                        for pr in cur.fetchall():
                            pending_list.append({
                                "id": pr[0],
                                "club_name": pr[1],
                                "room_number": pr[2],
                                "requester": pr[3],
                                "day": pr[4],
                                "lunch": pr[5],
                                "half": _half_label(pr[6]),
                                "notes": pr[7],
                            })
                finally:
                    conn.close()
            except Exception as e:
                app.logger.warning("teacher pending_list: %s", e)
        elif role == "admin":
            try:
                conn = get_connection()
                try:
                    with conn.cursor() as cur:
                        cur.execute(
                            """SELECT cr.id, cr.club_name, r.number,
                                      COALESCE(cr.requester_name, u.name, ''), cr.day, cr.lunch,
                                      COALESCE(cr.half, 'full'), COALESCE(cr.notes, '')
                               FROM club_requests cr
                               JOIN rooms r ON cr.room_id = r.id
                               LEFT JOIN users u ON cr.requested_by = u.id
                               WHERE cr.status = 'pending' ORDER BY cr.created_at DESC"""
                        )
                        for pr in cur.fetchall():
                            pending_list.append({
                                "id": pr[0],
                                "club_name": pr[1],
                                "room_number": pr[2],
                                "requester": pr[3],
                                "day": pr[4],
                                "lunch": pr[5],
                                "half": _half_label(pr[6]),
                                "notes": pr[7],
                            })
                finally:
                    conn.close()
            except Exception as e:
                app.logger.warning("admin pending_list: %s", e)

    if db_unreachable:
        _flash_db_connection_failed()

    return render_template(
        "teacher.html",
        view="dashboard",
        user=user,
        user_room=user_room,
        rooms=rooms,
        departments=departments,
        confirm_overwrite=confirm_overwrite,
        pending_list=pending_list,
    )


@app.route("/teacher/note", methods=["POST"])
@login_required
@require_verified_teacher
def teacher_note():
    if session.get("role") not in ("teacher", "admin"):
        flash("Teachers only.", "error")
        return redirect(url_for("index"))
    if session.get("dev_user"):
        flash("Dev mode: notes are not saved to the database.", "info")
        return redirect(url_for("teacher"))
    note = (request.form.get("todays_note") or "").strip()
    if len(note) > 200:
        note = note[:200]
    uid = session["user_id"]
    role = session.get("role")
    note_room_raw = (request.form.get("note_room_id") or "").strip()
    note_rid = None
    if note_room_raw:
        try:
            note_rid = int(note_room_raw)
        except ValueError:
            note_rid = None

    conn = get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                if note_rid is not None:
                    cur.execute("SELECT teacher_id FROM rooms WHERE id = %s", (note_rid,))
                    rr = cur.fetchone()
                    if not rr:
                        flash("Room not found.", "error")
                        return redirect(url_for("teacher"))
                    room_tid = rr[0]
                    if role == "admin" or session.get("dev_user"):
                        pass
                    elif role == "teacher" and room_tid == uid:
                        pass
                    else:
                        flash("You can't edit that room's note.", "error")
                        return redirect(url_for("teacher"))
                    cur.execute(
                        "UPDATE rooms SET todays_note = %s, note_set_date = CURRENT_DATE, updated_at = now() WHERE id = %s",
                        (note or None, note_rid),
                    )
                elif uid and uid != 0:
                    cur.execute(
                        "UPDATE rooms SET todays_note = %s, note_set_date = CURRENT_DATE, updated_at = now() "
                        "WHERE teacher_id = %s",
                        (note or None, uid),
                    )
                else:
                    flash("Choose which room gets this note (required for the demo login).", "error")
                    return redirect(url_for("teacher"))
        flash("Today's note saved.", "success")
    except Exception as e:
        app.logger.exception("teacher_note: %s", e)
        flash("Could not save note.", "error")
    finally:
        conn.close()
    return redirect(url_for("teacher"))


def _json_unauthorized():
    return jsonify({"error": "Authentication required"}), 401


def _json_forbidden(msg="Forbidden"):
    return jsonify({"error": msg}), 403


def _room_api_dict_from_row(row):
    if not row:
        return None
    d = {
        "id": row[0],
        "number": row[1],
        "floor": row[2],
        "department": row[3],
        "teacher_id": row[4],
        "status": row[5],
        "office_hours": row[6],
        "lunch_duty": row[7],
        "club_meeting": row[8],
        "teacher_name": row[9],
    }
    if len(row) > 10:
        d["todays_note"] = row[10]
    return d


def _fetch_room_api_row(cur, room_id):
    cur.execute(
        """SELECT r.id, r.number, r.floor, r.department, r.teacher_id, r.status, r.office_hours, r.lunch_duty, r.club_meeting, u.name, r.todays_note
           FROM rooms r LEFT JOIN users u ON r.teacher_id = u.id WHERE r.id = %s""",
        (room_id,),
    )
    return cur.fetchone()


def _teacher_or_admin_may_set_status(user, room_row_teacher_id):
    role = session.get("role")
    if role == "admin":
        return True
    if role == "teacher" and room_row_teacher_id == user["id"]:
        return True
    return False


@app.route("/api/rooms/available", methods=["GET"])
def api_rooms_available():
    if _dev_user_active():
        return jsonify({"rooms": []})
    floor_q = request.args.get("floor", type=int)
    conn = get_connection()
    rooms = []
    try:
        with conn.cursor() as cur:
            if floor_q is not None:
                cur.execute(
                    """SELECT r.id, r.number, r.floor, r.department, r.teacher_id, r.status, r.office_hours, r.lunch_duty, r.club_meeting, u.name, r.todays_note
                       FROM rooms r LEFT JOIN users u ON r.teacher_id = u.id
                       WHERE r.status = 'open' AND r.floor = %s ORDER BY r.number""",
                    (floor_q,),
                )
            else:
                cur.execute(
                    """SELECT r.id, r.number, r.floor, r.department, r.teacher_id, r.status, r.office_hours, r.lunch_duty, r.club_meeting, u.name, r.todays_note
                       FROM rooms r LEFT JOIN users u ON r.teacher_id = u.id
                       WHERE r.status = 'open' ORDER BY r.floor, r.number"""
                )
            rooms = [_room_api_dict_from_row(r) for r in cur.fetchall()]
    except Exception as e:
        app.logger.exception("api_rooms_available: %s", e)
        return jsonify({"error": "Database error"}), 500
    finally:
        conn.close()
    return jsonify({"rooms": rooms})


@app.route("/api/rooms/<int:room_id>/status", methods=["PATCH"])
def api_room_status(room_id):
    if "user_id" not in session:
        return _json_unauthorized()
    user = get_user_by_id(session["user_id"])
    if not user:
        return _json_unauthorized()
    role = session.get("role")
    if role not in ("teacher", "admin"):
        return _json_forbidden("Teachers or admins only")
    if role == "teacher" and not user.get("email_verified", False):
        return _json_teacher_unverified()
    if _dev_user_active():
        return _json_dev_no_db()

    data = request.get_json(silent=True) or {}
    status = data.get("status")
    if status not in ("open", "quiet_study", "closed"):
        return jsonify({"error": 'status must be "open", "quiet_study", or "closed"'}), 400

    conn = get_connection()
    row = None
    try:
        with conn.cursor() as cur:
            row = _fetch_room_api_row(cur, room_id)
            if not row:
                return jsonify({"error": "Room not found"}), 404
            if not _teacher_or_admin_may_set_status(user, row[4]):
                return _json_forbidden("You can only update your assigned room")
            cur.execute(
                "UPDATE rooms SET status = %s, updated_at = now() WHERE id = %s",
                (status, room_id),
            )
            log_room_audit(room_id, user["id"], f"status:{status}")
        conn.commit()
        with conn.cursor() as cur:
            row = _fetch_room_api_row(cur, room_id)
    except Exception as e:
        app.logger.exception("api_room_status: %s", e)
        return jsonify({"error": "Database error"}), 500
    finally:
        conn.close()
    return jsonify({"room": _room_api_dict_from_row(row)})


@app.route("/api/rooms/<int:room_id>", methods=["GET", "PATCH"])
def api_room_by_id(room_id):
    if _dev_user_active():
        return _json_dev_no_db()
    conn = get_connection()
    try:
        if request.method == "GET":
            with conn.cursor() as cur:
                row = _fetch_room_api_row(cur, room_id)
            if not row:
                return jsonify({"error": "Room not found"}), 404
            d = _room_api_dict_from_row(row)
            with conn.cursor() as cur:
                cur.execute("SELECT day, lunch FROM availabilities WHERE room_id = %s", (room_id,))
                d["availability_map"] = {r[0]: r[1] for r in cur.fetchall()}
            return jsonify({"room": d})

        if request.method == "PATCH":
            if "user_id" not in session:
                return _json_unauthorized()
            if session.get("role") != "admin":
                return _json_forbidden("Admins only")
            data = request.get_json(silent=True) or {}
            allowed = (
                "number",
                "floor",
                "department",
                "teacher_id",
                "office_hours",
                "lunch_duty",
                "club_meeting",
                "status",
            )
            updates = {k: data[k] for k in allowed if k in data}
            if not updates:
                return jsonify({"error": "No valid fields to update"}), 400
            if "status" in updates and updates["status"] not in ("open", "quiet_study", "closed"):
                return jsonify({"error": "Invalid status"}), 400
            sets = []
            vals = []
            for k, v in updates.items():
                sets.append(f"{k} = %s")
                vals.append(v)
            vals.append(room_id)
            uid = session["user_id"]
            with conn.cursor() as cur:
                cur.execute(
                    f"UPDATE rooms SET {', '.join(sets)}, updated_at = now() WHERE id = %s",
                    vals,
                )
                if cur.rowcount == 0:
                    return jsonify({"error": "Room not found"}), 404
            log_room_audit(room_id, uid, "admin_patch")
            conn.commit()
            with conn.cursor() as cur:
                row = _fetch_room_api_row(cur, room_id)
            return jsonify({"room": _room_api_dict_from_row(row)})
    except Exception as e:
        app.logger.exception("api_room_by_id: %s", e)
        return jsonify({"error": "Database error"}), 500
    finally:
        conn.close()


def _api_rooms_create():
    if "user_id" not in session:
        return _json_unauthorized()
    if session.get("role") != "admin":
        return _json_forbidden("Admins only")
    if _dev_user_active():
        return _json_dev_no_db()
    data = request.get_json(silent=True) or {}
    number = (data.get("number") or "").strip()
    if not number:
        return jsonify({"error": "number is required"}), 400
    floor = data.get("floor", 1)
    try:
        floor = int(floor)
    except (TypeError, ValueError):
        return jsonify({"error": "floor must be an integer"}), 400
    department = (data.get("department") or "").strip() or None
    teacher_id = data.get("teacher_id")
    if teacher_id is not None:
        try:
            teacher_id = int(teacher_id)
        except (TypeError, ValueError):
            return jsonify({"error": "teacher_id must be integer or null"}), 400
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            new_id = insert_returning_id(
                cur,
                """INSERT INTO rooms (number, floor, department, teacher_id, status)
                   VALUES (%s,%s,%s,%s,'open') RETURNING id""",
                (number, floor, department, teacher_id),
            )
            log_room_audit(new_id, session["user_id"], "admin_create")
        conn.commit()
        with conn.cursor() as cur:
            row = _fetch_room_api_row(cur, new_id)
        return jsonify({"room": _room_api_dict_from_row(row)}), 201
    except Exception as e:
        app.logger.exception("_api_rooms_create: %s", e)
        if "unique" in str(e).lower() or "duplicate" in str(e).lower():
            return jsonify({"error": "Room number already exists"}), 400
        return jsonify({"error": "Database error"}), 500
    finally:
        conn.close()


@app.route("/api/rooms", methods=["GET", "POST"])
def api_rooms():
    if request.method == "POST":
        return _api_rooms_create()

    if _dev_user_active():
        current_day, current_lunch = get_current_day_lunch()
        return jsonify({
            "rooms": [],
            "rooms_by_number": {},
            "current_day": current_day,
            "current_lunch": current_lunch,
        })

    floor_q = request.args.get("floor", type=int)
    conn = None
    rooms_list = []
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            if floor_q is not None:
                cur.execute(
                    """SELECT r.id, r.number, r.floor, r.department, r.teacher_id, r.status, r.office_hours, r.lunch_duty, r.club_meeting, u.name, r.todays_note
                       FROM rooms r LEFT JOIN users u ON r.teacher_id = u.id WHERE r.floor = %s ORDER BY r.number""",
                    (floor_q,),
                )
            else:
                cur.execute(
                    """SELECT r.id, r.number, r.floor, r.department, r.teacher_id, r.status, r.office_hours, r.lunch_duty, r.club_meeting, u.name, r.todays_note
                       FROM rooms r LEFT JOIN users u ON r.teacher_id = u.id ORDER BY r.floor, r.number"""
                )
            rooms_list = [_room_api_dict_from_row(r) for r in cur.fetchall()]
    except Exception as e:
        app.logger.warning("api_rooms list: %s", e)
        rooms_list = []
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    rooms_legacy = build_rooms_for_display(include_availability_map=True)
    current_day, current_lunch = get_current_day_lunch()
    return jsonify({
        "rooms": rooms_list,
        "rooms_by_number": rooms_legacy,
        "current_day": current_day,
        "current_lunch": current_lunch,
    })


@app.route("/api/favorites", methods=["GET", "POST", "DELETE"])
@login_required
def api_favorites():
    if session.get("role") != "student":
        return jsonify({"error": "Students only"}), 403
    user_id = session["user_id"]
    if request.method == "GET":
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT room_id FROM student_favorites WHERE user_id = %s", (user_id,))
                room_ids = [r[0] for r in cur.fetchall()]
            return jsonify({"room_ids": room_ids})
        except Exception:
            return jsonify({"room_ids": []})
        finally:
            conn.close()
    room_id = request.form.get("room_id") or (request.json.get("room_id") if request.is_json else None)
    if request.method == "DELETE":
        room_id = request.form.get("room_id") or (request.json.get("room_id") if request.is_json else None)
    if not room_id:
        return jsonify({"error": "room_id required"}), 400
    try:
        room_id_int = int(room_id)
    except (TypeError, ValueError):
        return jsonify({"error": "invalid room_id"}), 400
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            if request.method == "POST":
                cur.execute(
                    "INSERT INTO student_favorites (user_id, room_id) VALUES (%s,%s) ON CONFLICT (user_id, room_id) DO NOTHING",
                    (user_id, room_id_int),
                )
            else:
                cur.execute("DELETE FROM student_favorites WHERE user_id = %s AND room_id = %s", (user_id, room_id_int))
        conn.commit()
    finally:
        conn.close()
    return jsonify({"ok": True})


@app.route("/api/notifications", methods=["GET"])
@login_required
def api_notifications():
    uid = session["user_id"]
    if uid == 0:
        return jsonify({"unread_count": 0, "notifications": []})
    conn = get_connection()
    rows = []
    unread = 0
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id, message, link, is_read, created_at FROM notifications
                   WHERE user_id = %s ORDER BY created_at DESC LIMIT 20""",
                (uid,),
            )
            for r in cur.fetchall():
                rows.append({
                    "id": r[0],
                    "message": r[1],
                    "link": r[2],
                    "is_read": bool(r[3]),
                    "created_at": (
                        r[4].isoformat()
                        if r[4] and hasattr(r[4], "isoformat")
                        else (str(r[4]) if r[4] else None)
                    ),
                })
            cur.execute(
                "SELECT COUNT(*) FROM notifications WHERE user_id = %s AND is_read = FALSE",
                (uid,),
            )
            unread = cur.fetchone()[0] or 0
    except Exception as e:
        app.logger.exception("api_notifications: %s", e)
        return jsonify({"error": "Database error"}), 500
    finally:
        conn.close()
    return jsonify({"unread_count": unread, "notifications": rows})


@app.route("/api/notifications/read-all", methods=["POST"])
@login_required
def api_notifications_read_all():
    uid = session["user_id"]
    if uid == 0:
        return jsonify({"ok": True})
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE notifications SET is_read = TRUE WHERE user_id = %s", (uid,))
        conn.commit()
    except Exception as e:
        app.logger.exception("api_notifications_read_all: %s", e)
        return jsonify({"error": "Database error"}), 500
    finally:
        conn.close()
    return jsonify({"ok": True})


def _club_review_redirect():
    if session.get("role") == "admin":
        return redirect(url_for("admin") + "#club-requests")
    return redirect(url_for("teacher"))


def _handle_club_request_review(req_id, approve):
    if "user_id" not in session:
        flash("Please log in.", "error")
        return redirect(url_for("login"))
    uid = session["user_id"]
    role = session.get("role")
    if role not in ("teacher", "admin"):
        flash("You can't review requests.", "error")
        return redirect(url_for("index"))
    if role == "teacher":
        uchk = get_user_by_id(uid)
        if not uchk or not uchk.get("email_verified", False):
            flash("Your teacher account must be verified.", "error")
            return redirect(url_for("login"))

    if session.get("dev_user"):
        flash("Dev mode: club request actions are not saved to the database.", "info")
        return _club_review_redirect()

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT cr.room_id, cr.club_name, cr.requester_email, r.teacher_id
                   FROM club_requests cr JOIN rooms r ON cr.room_id = r.id
                   WHERE cr.id = %s AND cr.status = 'pending'""",
                (req_id,),
            )
            row = cur.fetchone()
            if not row:
                flash("Request not found or already reviewed.", "error")
                return _club_review_redirect()
            room_id, club_name, requester_email, room_teacher_id = row[0], row[1], row[2], row[3]
            if role == "teacher" and room_teacher_id != uid:
                flash("This request is not for your room.", "error")
                return _club_review_redirect()
            new_status = "approved" if approve else "rejected"
            cur.execute(
                """UPDATE club_requests SET status = %s, reviewed_by = %s, reviewed_at = now() WHERE id = %s""",
                (new_status, _db_user_fk(uid), req_id),
            )
            if approve:
                cur.execute("SELECT club_meeting FROM rooms WHERE id = %s", (room_id,))
                cm = cur.fetchone()
                existing = (cm[0] or "") + ";" if cm and cm[0] else ""
                cur.execute(
                    """SELECT club_name, day, lunch, COALESCE(half, 'full') FROM club_requests WHERE id = %s""",
                    (req_id,),
                )
                cr = cur.fetchone()
                if cr:
                    hl = {"first_half": "1st half", "second_half": "2nd half", "full": "full lunch"}.get(cr[3], cr[3])
                    new_entry = f"{cr[0]} {cr[1]} {cr[2]} lunch ({hl})"
                    cur.execute(
                        "UPDATE rooms SET club_meeting = %s WHERE id = %s",
                        ((existing + new_entry).strip(";"), room_id),
                    )
        conn.commit()
    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        app.logger.exception("club_request review: %s", e)
        flash("Could not update request.", "error")
        return _club_review_redirect()
    finally:
        conn.close()

    flash("Request " + ("approved" if approve else "declined") + ".", "success")
    return _club_review_redirect()


@app.route("/api/club-request/<int:req_id>/approve", methods=["POST"])
@login_required
def api_club_request_approve(req_id):
    return _handle_club_request_review(req_id, True)


@app.route("/api/club-request/<int:req_id>/decline", methods=["POST"])
@login_required
def api_club_request_decline(req_id):
    return _handle_club_request_review(req_id, False)


@app.route("/club-request", methods=["GET", "POST"])
def club_request():
    if request.method == "GET" and session.get("dev_user"):
        return render_template("club_request.html", rooms=[])
    if request.method == "POST":
        if session.get("dev_user"):
            flash("Dev mode: club requests are not submitted to the database.", "info")
            return redirect(url_for("club_request"))
        room_id_raw = request.form.get("room_id")
        club_name = (request.form.get("club_name") or "").strip()
        day = (request.form.get("day") or "").strip()
        lunch = (request.form.get("lunch") or "").strip().upper()
        half = (request.form.get("half") or "").strip()
        notes = (request.form.get("notes") or "").strip()
        if len(notes) > 300:
            notes = notes[:300]
        requester_name = (request.form.get("requester_name") or "").strip()
        requester_email = (request.form.get("requester_email") or "").strip() or None
        if not room_id_raw or not club_name or day not in "MTWRF" or lunch not in ("A", "B"):
            flash("Please fill in club name, room, day, and lunch.", "error")
            return redirect(url_for("club_request"))
        if half not in ("first_half", "second_half", "full"):
            flash("Please choose how much of lunch you need.", "error")
            return redirect(url_for("club_request"))
        if not requester_name:
            flash("Requester name is required.", "error")
            return redirect(url_for("club_request"))
        try:
            room_id = int(room_id_raw)
        except ValueError:
            flash("Invalid room.", "error")
            return redirect(url_for("club_request"))

        conn = get_connection()
        room_number = None
        teacher_id = None
        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """INSERT INTO club_requests (room_id, requested_by, club_name, day, lunch, status, half, notes, requester_name, requester_email)
                           VALUES (%s, NULL, %s, %s, %s, 'pending', %s, %s, %s, %s)""",
                        (room_id, club_name, day, lunch, half, notes or None, requester_name, requester_email),
                    )
                    cur.execute("SELECT number, teacher_id FROM rooms WHERE id = %s", (room_id,))
                    rr = cur.fetchone()
                    if rr:
                        room_number, teacher_id = rr[0], rr[1]
                    if teacher_id:
                        cur.execute(
                            """INSERT INTO notifications (user_id, message, link) VALUES (%s,%s,%s)""",
                            (teacher_id, f"New club request: {club_name} (Room {room_number})", "/teacher"),
                        )
                    else:
                        cur.execute("SELECT id FROM users WHERE role = 'admin'")
                        for (admin_id,) in cur.fetchall():
                            cur.execute(
                                """INSERT INTO notifications (user_id, message, link) VALUES (%s,%s,%s)""",
                                (admin_id, f"New club request: {club_name} (Room {room_number or room_id})", "/admin"),
                            )
        except Exception as e:
            app.logger.exception("club_request insert: %s", e)
            flash("Could not submit request. Please try again.", "error")
            return redirect(url_for("club_request"))
        finally:
            conn.close()
        flash("Your request has been submitted! The teacher will review it.", "success")
        return redirect(url_for("club_request"))

    conn = get_connection()
    rooms = []
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT r.id, r.number, u.name, r.label FROM rooms r
                   LEFT JOIN users u ON r.teacher_id = u.id
                   ORDER BY r.floor, r.number"""
            )
            rooms = []
            for r in cur.fetchall():
                num = r[1]
                rooms.append({
                    "id": r[0],
                    "number": num,
                    "teacher_name": r[2],
                    "label": r[3] if len(r) > 3 else None,
                    "dropdown_caption": room_dropdown_caption(num, r[3] if len(r) > 3 else None),
                })
    finally:
        conn.close()
    return render_template("club_request.html", rooms=rooms)


@app.route("/admin")
@admin_required
def admin():
    if session.get("dev_user"):
        return render_template(
            "admin.html",
            users=[],
            rooms=[],
            club_requests=[],
            club_status="all",
        )
    conn = get_connection()
    users_list = []
    rooms_list = []
    club_requests_list = []
    club_status = (request.args.get("club_status") or "all").lower()
    if club_status not in ("all", "pending", "approved", "rejected"):
        club_status = "all"
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id, name, email, role, department, created_at, email_verified, club_name
                   FROM users ORDER BY name"""
            )
            for r in cur.fetchall():
                users_list.append({
                    "id": r[0],
                    "name": r[1],
                    "email": r[2],
                    "role": r[3],
                    "department": r[4],
                    "created_at": str(r[5])[:19] if r[5] else None,
                    "email_verified": r[6] if len(r) > 6 and r[6] is not None else True,
                    "club_name": (r[7] or "") if len(r) > 7 else "",
                })
            cur.execute(
                """SELECT r.id, r.number, r.office_hours, r.updated_at, u.name, r.floor, r.status,
                          r.teacher_id, r.department, r.lunch_duty, r.club_meeting, r.label
                   FROM rooms r LEFT JOIN users u ON r.teacher_id = u.id ORDER BY r.floor, r.number"""
            )
            for r in cur.fetchall():
                num = r[1]
                lbl = r[11] if len(r) > 11 else None
                rooms_list.append({
                    "id": r[0],
                    "number": num,
                    "office_hours": r[2],
                    "updated_at": str(r[3])[:19] if r[3] else None,
                    "teacher_name": r[4],
                    "floor": r[5] if len(r) > 5 and r[5] is not None else 1,
                    "status": r[6] if len(r) > 6 else "open",
                    "teacher_id": r[7] if len(r) > 7 else None,
                    "department": r[8] if len(r) > 8 else None,
                    "lunch_duty": r[9] if len(r) > 9 else None,
                    "club_meeting": r[10] if len(r) > 10 else None,
                    "label": lbl,
                    "dropdown_caption": room_dropdown_caption(num, lbl),
                })
            q = """SELECT cr.id, cr.club_name, r.number, cr.day, cr.lunch, COALESCE(cr.half, 'full'),
                          COALESCE(cr.notes, ''), COALESCE(cr.requester_name, ''), cr.requester_email,
                          cr.status, cr.created_at, t.name
                   FROM club_requests cr
                   JOIN rooms r ON cr.room_id = r.id
                   LEFT JOIN users t ON r.teacher_id = t.id"""
            params = []
            if club_status != "all":
                q += " WHERE cr.status = %s"
                params.append(club_status)
            q += " ORDER BY cr.created_at DESC"
            cur.execute(q, params)
            for row in cur.fetchall():
                club_requests_list.append({
                    "id": row[0],
                    "club_name": row[1],
                    "room_number": row[2],
                    "day": row[3],
                    "lunch": row[4],
                    "half": _half_label(row[5]),
                    "notes": row[6],
                    "requester_name": row[7],
                    "requester_email": row[8],
                    "status": row[9],
                    "created_at": str(row[10])[:19] if row[10] else None,
                    "room_teacher_name": row[11],
                })
    finally:
        conn.close()
    return render_template(
        "admin.html",
        users=users_list,
        rooms=rooms_list,
        club_requests=club_requests_list,
        club_status=club_status,
    )


@app.route("/admin/room/<int:room_id>/update", methods=["POST"])
@admin_required
def admin_update_room(room_id):
    if _dev_user_active():
        flash("Dev mode: changes not saved to the database.", "info")
        return redirect(url_for("admin") + "#rooms")
    number = (request.form.get("number") or "").strip()
    if not number:
        flash("Room number is required.", "error")
        return redirect(url_for("admin") + "#rooms")
    try:
        floor = int(request.form.get("floor") or 1)
    except (TypeError, ValueError):
        floor = 1
    status = (request.form.get("status") or "open").strip()
    if status not in ("open", "quiet_study", "closed"):
        status = "open"
    department = (request.form.get("department") or "").strip() or None
    office_hours = (request.form.get("office_hours") or "").strip() or None
    lunch_duty = (request.form.get("lunch_duty") or "").strip() or None
    club_meeting = (request.form.get("club_meeting") or "").strip() or None
    teacher_raw = (request.form.get("teacher_id") or "").strip()
    teacher_id = None
    if teacher_raw:
        try:
            teacher_id = int(teacher_raw)
        except ValueError:
            teacher_id = None

    uid = session["user_id"]
    conn = get_connection()
    err = None
    try:
        with conn.cursor() as cur:
            if teacher_id is not None:
                cur.execute(
                    "SELECT id FROM users WHERE id = %s AND role IN ('teacher','admin')",
                    (teacher_id,),
                )
                if not cur.fetchone():
                    err = "Invalid teacher selection."
            if not err:
                cur.execute(
                    "SELECT id FROM rooms WHERE number = %s AND id != %s",
                    (number, room_id),
                )
                if cur.fetchone():
                    err = "Another room already uses that number."
            if not err:
                cur.execute(
                    """UPDATE rooms SET number=%s, floor=%s, status=%s, department=%s, teacher_id=%s,
                       office_hours=%s, lunch_duty=%s, club_meeting=%s, updated_at=now() WHERE id=%s""",
                    (number, floor, status, department, teacher_id, office_hours, lunch_duty, club_meeting, room_id),
                )
                if cur.rowcount == 0:
                    err = "Room not found."
        if err:
            conn.rollback()
            flash(err, "error")
        else:
            conn.commit()
            log_room_audit(room_id, uid, "admin_form_update")
            flash("Room updated.", "success")
    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        app.logger.exception("admin_update_room: %s", e)
        flash("Could not update room.", "error")
    finally:
        conn.close()
    return redirect(url_for("admin") + "#rooms")


@app.route("/admin/room/new", methods=["POST"])
@admin_required
def admin_create_room():
    if _dev_user_active():
        flash("Dev mode: changes not saved to the database.", "info")
        return redirect(url_for("admin") + "#rooms")
    number = (request.form.get("number") or "").strip()
    if not number:
        flash("Room number is required.", "error")
        return redirect(url_for("admin") + "#rooms")
    try:
        floor = int(request.form.get("floor") or 1)
    except (TypeError, ValueError):
        floor = 1
    status = (request.form.get("status") or "open").strip()
    if status not in ("open", "quiet_study", "closed"):
        status = "open"
    department = (request.form.get("department") or "").strip() or None
    teacher_raw = (request.form.get("teacher_id") or "").strip()
    teacher_id = None
    if teacher_raw:
        try:
            teacher_id = int(teacher_raw)
        except ValueError:
            teacher_id = None

    uid = session["user_id"]
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            if teacher_id is not None:
                cur.execute(
                    "SELECT id FROM users WHERE id = %s AND role IN ('teacher','admin')",
                    (teacher_id,),
                )
                if not cur.fetchone():
                    flash("Invalid teacher selection.", "error")
                    return redirect(url_for("admin") + "#rooms")
            new_id = insert_returning_id(
                cur,
                """INSERT INTO rooms (number, floor, department, teacher_id, status)
                   VALUES (%s,%s,%s,%s,%s) RETURNING id""",
                (number, floor, department, teacher_id, status),
            )
        conn.commit()
        log_room_audit(new_id, uid, "admin_form_create")
        flash("Room created.", "success")
    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        app.logger.exception("admin_create_room: %s", e)
        if "unique" in str(e).lower() or "duplicate" in str(e).lower():
            flash("That room number already exists.", "error")
        else:
            flash("Could not create room.", "error")
    finally:
        conn.close()
    return redirect(url_for("admin") + "#rooms")


@app.route("/admin/user/<int:user_id>/verify", methods=["POST"])
@admin_required
def admin_verify_teacher_email(user_id):
    if _dev_user_active():
        return jsonify({"ok": False, "error": "Dev mode: database disabled"}), 503
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT role FROM users WHERE id = %s", (user_id,))
            row = cur.fetchone()
            if not row or row[0] != "teacher":
                return jsonify({"ok": False, "error": "User is not a teacher."}), 400
            cur.execute(
                """UPDATE users SET email_verified = TRUE, email_verification_token = NULL,
                   email_verification_expires = NULL, updated_at = now() WHERE id = %s""",
                (user_id,),
            )
        conn.commit()
    finally:
        conn.close()
    return jsonify({"ok": True, "email_verified": True})


@app.route("/admin/user/<int:user_id>/role", methods=["POST"])
@admin_required
def admin_set_role(user_id):
    role = request.form.get("role")
    if role not in ("teacher", "student", "admin", "club_president"):
        return redirect(url_for("admin"))
    if _dev_user_active():
        flash("Dev mode: changes not saved to the database.", "info")
        return redirect(url_for("admin"))
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE users SET role = %s, updated_at = now() WHERE id = %s", (role, user_id))
        conn.commit()
    finally:
        conn.close()
    flash("Role updated.", "success")
    return redirect(url_for("admin"))


@app.route("/admin/user/<int:user_id>/department", methods=["POST"])
@admin_required
def admin_set_department(user_id):
    department = request.form.get("department", "").strip() or None
    if _dev_user_active():
        flash("Dev mode: changes not saved to the database.", "info")
        return redirect(url_for("admin"))
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE users SET department = %s, updated_at = now() WHERE id = %s", (department, user_id))
        conn.commit()
    finally:
        conn.close()
    flash("Department updated.", "success")
    return redirect(url_for("admin"))


@app.route("/admin/user/<int:user_id>/delete", methods=["POST"])
@admin_required
def admin_delete_user(user_id):
    if user_id == session["user_id"]:
        flash("Cannot delete your own account here. Use Account settings.", "error")
        return redirect(url_for("admin"))
    if _dev_user_active():
        flash("Dev mode: changes not saved to the database.", "info")
        return redirect(url_for("admin"))
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM users WHERE id = %s", (user_id,))
        conn.commit()
    finally:
        conn.close()
    flash("User deleted.", "success")
    return redirect(url_for("admin"))


def _audit_log_filter_clause():
    user_id = request.args.get("user_id", type=int)
    room_id = request.args.get("room_id", type=int)
    date_from = request.args.get("from")
    date_to = request.args.get("to")
    where = ["1=1"]
    params = []
    if user_id is not None:
        where.append("ral.user_id = %s")
        params.append(user_id)
    if room_id is not None:
        where.append("ral.room_id = %s")
        params.append(room_id)
    if date_from:
        if USE_SQLITE:
            where.append("date(ral.created_at) >= date(%s)")
        else:
            where.append("ral.created_at >= %s::date")
        params.append(date_from)
    if date_to:
        if USE_SQLITE:
            where.append("date(ral.created_at) < date(%s, '+1 day')")
        else:
            where.append("ral.created_at < (%s::date + interval '1 day')")
        params.append(date_to)
    return " AND ".join(where), params


@app.route("/admin/user/<int:user_id>/club-name", methods=["POST"])
@admin_required
def admin_set_club_name(user_id):
    data = request.get_json(silent=True) or {}
    raw = (data.get("club_name") or "").strip()
    name = raw[:100] if raw else None
    if _dev_user_active():
        return _json_dev_no_db()
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE users SET club_name = %s, updated_at = now() WHERE id = %s", (name, user_id))
        conn.commit()
    finally:
        conn.close()
    return jsonify({"ok": True})


@app.route("/admin/audit-log")
@admin_required
def admin_audit_log():
    page = request.args.get("page", 1, type=int) or 1
    if page < 1:
        page = 1
    per_page = 50
    offset = (page - 1) * per_page
    where_sql, params = _audit_log_filter_clause()
    if _dev_user_active():
        filters = {
            "user_id": request.args.get("user_id", type=int),
            "room_id": request.args.get("room_id", type=int),
            "from": request.args.get("from"),
            "to": request.args.get("to"),
        }
        return render_template(
            "admin_audit_log.html",
            logs=[],
            total=0,
            page=1,
            total_pages=1,
            filters=filters,
            all_users=[],
            all_rooms=[],
        )
    conn = get_connection()
    logs = []
    total = 0
    all_users = []
    all_rooms = []
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""SELECT COUNT(*) FROM room_audit_log ral WHERE {where_sql}""",
                tuple(params),
            )
            total = cur.fetchone()[0]
            cur.execute(
                f"""SELECT ral.id, ral.created_at, ral.action, u.name AS user_name, r.number AS room_number
                   FROM room_audit_log ral
                   LEFT JOIN users u ON ral.user_id = u.id
                   LEFT JOIN rooms r ON ral.room_id = r.id
                   WHERE {where_sql}
                   ORDER BY ral.created_at DESC LIMIT %s OFFSET %s""",
                tuple(params) + (per_page, offset),
            )
            for row in cur.fetchall():
                logs.append({
                    "id": row[0],
                    "created_at": row[1],
                    "action": row[2],
                    "user_name": row[3] or "",
                    "room_number": row[4] or "",
                })
            cur.execute("SELECT id, name FROM users ORDER BY name")
            all_users = [{"id": r[0], "name": r[1]} for r in cur.fetchall()]
            cur.execute("SELECT id, number FROM rooms ORDER BY floor, number")
            all_rooms = [{"id": r[0], "number": r[1]} for r in cur.fetchall()]
    finally:
        conn.close()
    total_pages = max(1, (total + per_page - 1) // per_page)
    filters = {
        "user_id": request.args.get("user_id", type=int),
        "room_id": request.args.get("room_id", type=int),
        "from": request.args.get("from"),
        "to": request.args.get("to"),
    }
    return render_template(
        "admin_audit_log.html",
        logs=logs,
        total=total,
        page=page,
        total_pages=total_pages,
        filters=filters,
        all_users=all_users,
        all_rooms=all_rooms,
    )


@app.route("/admin/audit-log/export")
@admin_required
def admin_audit_export():
    where_sql, params = _audit_log_filter_clause()
    if _dev_user_active():

        def generate_empty():
            yield "Timestamp,User,Room,Action\n"

        return Response(
            stream_with_context(generate_empty()),
            mimetype="text/csv",
            headers={"Content-Disposition": "attachment; filename=audit-log.csv"},
        )
    conn = get_connection()
    rows_out = []
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""SELECT ral.created_at, u.name, r.number, ral.action
                   FROM room_audit_log ral
                   LEFT JOIN users u ON ral.user_id = u.id
                   LEFT JOIN rooms r ON ral.room_id = r.id
                   WHERE {where_sql}
                   ORDER BY ral.created_at DESC""",
                tuple(params),
            )
            rows_out = cur.fetchall()
    finally:
        conn.close()

    def generate():
        yield "Timestamp,User,Room,Action\n"
        for row in rows_out:
            ts0 = row[0]
            if ts0 is None:
                ts = ""
            elif hasattr(ts0, "isoformat"):
                ts = ts0.isoformat()
            else:
                ts = str(ts0)
            un = (row[1] or "").replace(",", ";")
            rn = (row[2] or "").replace(",", ";")
            act = (row[3] or "").replace(",", ";")
            yield f"{ts},{un},{rn},{act}\n"

    return Response(
        stream_with_context(generate()),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=audit-log.csv"},
    )


@app.route("/admin/department-default", methods=["GET", "POST"])
@admin_required
def admin_department_default():
    if _dev_user_active():
        if request.method == "POST":
            flash("Dev mode: department defaults not saved to the database.", "info")
            return redirect(url_for("admin_department_default"))
        return render_template("admin_department_default.html", defaults=[])
    if request.method == "POST":
        department = request.form.get("department", "").strip()
        office_hours = request.form.get("office_hours", "").strip() or None
        lunch_duty = request.form.get("lunch_duty", "").strip() or None
        default_avail = {}
        for d in "MTRWF":
            default_avail[d] = request.form.get(f"def_{d}", "N") if request.form.get(f"def_{d}") in ("A", "B", "N") else "N"
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO department_defaults (department, office_hours, lunch_duty, default_avail)
                       VALUES (%s,%s,%s,%s)
                       ON CONFLICT (department) DO UPDATE SET office_hours=%s, lunch_duty=%s, default_avail=%s""",
                    (department, office_hours, lunch_duty, json.dumps(default_avail), office_hours, lunch_duty, json.dumps(default_avail)),
                )
            conn.commit()
        finally:
            conn.close()
        flash("Department default saved.", "success")
        return redirect(url_for("admin_department_default"))
    conn = get_connection()
    defaults = []
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT department, office_hours, lunch_duty, default_avail FROM department_defaults")
            for r in cur.fetchall():
                defaults.append({
                    "department": r[0],
                    "office_hours": r[1],
                    "lunch_duty": r[2],
                    "default_avail": _parse_default_avail(r[3] if len(r) > 3 else None),
                })
    finally:
        conn.close()
    return render_template("admin_department_default.html", defaults=defaults)


if __name__ == "__main__":
    app.run(
        debug=True,
        host="127.0.0.1",
        port=5000,
        threaded=True,
        use_reloader=False,
    )
