import os
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent / ".env")
except ImportError:
    pass

import secrets
from datetime import datetime, timedelta

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
)
from flask_mail import Mail, Message
from werkzeug.security import generate_password_hash, check_password_hash

from db import get_connection, init_db
import config


app = Flask(__name__)


app.config.from_object(config)
app.secret_key = getattr(config, "SECRET_KEY", os.environ.get("SECRET_KEY", "dev-secret"))

# Initialize Flask-Mail
mail = Mail(app)

# Map weekday to our day code: Monday=0 -> M, Tuesday=1 -> T, ...
WEEKDAY_TO_DAY = {"0": "M", "1": "T", "2": "W", "3": "R", "4": "F"}

# Development mode: also log verification codes to console (emails still send if mail is configured)
DEV_MODE = os.environ.get("FLASK_ENV") == "development" or os.environ.get("DEV_MODE", "").lower() == "true"
DEV_VERIFICATION_CODES = {}


def _mail_configured():
    """True if SMTP is configured so we can send real emails."""
    return bool(app.config.get("MAIL_USERNAME") and app.config.get("MAIL_PASSWORD"))

try:
    init_db()
except Exception as e:
    try:
        app.logger.error("Database init failed: %s", e)
    except Exception:
        pass


def get_user_by_email(email):
    # Try with new columns first
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
                    "email_verified": row[6] if len(row) > 6 else True,
                }
    except Exception:
        pass
    finally:
        conn.close()
    
    # Fall back to old schema with separate connection
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, name, email, password_hash, role, department FROM users WHERE email = %s",
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
                    "email_verified": True,
                }
    except Exception:
        pass
    finally:
        conn.close()
    return None


def get_user_by_id(user_id):
    # Try with new columns first
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, name, email, role, department, email_verified FROM users WHERE id = %s",
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
                    "email_verified": row[5] if len(row) > 5 else True,
                }
    except Exception:
        pass
    finally:
        conn.close()
    
    # Fall back to old schema with separate connection
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, name, email, role, department FROM users WHERE id = %s",
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
                    "email_verified": True,
                }
    except Exception:
        pass
    finally:
        conn.close()
    return None


def send_verification_email(user_email, code):
    """Send email verification code. Sends real email when MAIL_USERNAME/MAIL_PASSWORD are set."""
    if DEV_MODE:
        DEV_VERIFICATION_CODES[user_email] = code
        print(f"\n[DEV] Verification code for {user_email}: {code}\n")
        app.logger.info(f"DEV: Verification code for {user_email} is {code}")

    if _mail_configured():
        try:
            msg = Message(
                "Verify your Onelunch account",
                sender=app.config.get("MAIL_DEFAULT_SENDER") or app.config.get("MAIL_USERNAME"),
                recipients=[user_email],
            )
            msg.body = f"""Welcome to Onelunch!

Your verification code is: {code}

Enter this code on the verification page to activate your account.
This code expires in 24 hours.

If you didn't create an account, you can ignore this email.

— Onelunch"""
            mail.send(msg)
            return True
        except Exception as e:
            app.logger.error("Failed to send verification email: %s", e)
            return False
    return True  # No mail config: dev fallback (code in console) still allows verification


def send_password_reset_email(user_email, reset_url):
    """Send password reset link by email. Returns True if sent or not configured (fallback to show link)."""
    if not _mail_configured():
        return False
    try:
        msg = Message(
            "Reset your Onelunch password",
            sender=app.config.get("MAIL_DEFAULT_SENDER") or app.config.get("MAIL_USERNAME"),
            recipients=[user_email],
        )
        msg.body = f"""You asked to reset your Onelunch password.

Click this link to set a new password (valid for 1 hour):

{reset_url}

If you didn't request this, you can ignore this email.

— Onelunch"""
        mail.send(msg)
        return True
    except Exception as e:
        app.logger.error("Failed to send password reset email: %s", e)
        return False


def get_user_by_verification_token(token):
    """Get user by verification token."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, name, email FROM users WHERE email_verification_token = %s AND email_verification_expires > now()",
                (token,),
            )
            row = cur.fetchone()
            if row:
                return {
                    "id": row[0],
                    "name": row[1],
                    "email": row[2],
                }
    except Exception:
        # If columns don't exist, return None (no verification possible)
        pass
    finally:
        conn.close()
    return None


def get_current_day_lunch():
    """Return (day_code, lunch) for 'today'. Override via ?day=M&lunch=B for testing."""
    day_param = request.args.get("day")
    lunch_param = request.args.get("lunch")
    if day_param and day_param in "MTWRF":
        day = day_param
    else:
        w = datetime.utcnow().weekday()  # 0=Mon .. 4=Fri
        day = WEEKDAY_TO_DAY.get(str(w), "M")
    lunch = lunch_param if lunch_param in ("A", "B") else "B"
    return day, lunch


def build_rooms_for_display(include_availability_map=True):
    """Build rooms dict keyed by room number; each room has teacher_name, office_hours, etc.
    If include_availability_map, each room has availability_map: { day: 'A'|'B'|'N' }.
    """
    out = {}
    conn = None
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute(
                """SELECT r.id, r.number, r.office_hours, r.lunch_duty, r.club_meeting, u.name
                   FROM rooms r LEFT JOIN users u ON r.teacher_id = u.id ORDER BY r.number"""
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
                out[number] = {
                    "room_id": room_id,
                    "teacher_name": row[5],
                    "room": number,
                    "office_hours": row[2],
                    "lunch_duty": row[3],
                    "club_meeting": row[4],
                    "available_days": available_days,
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
    """True if room is open for given day and lunch (A or B)."""
    am = room_data.get("availability_map") or {}
    return am.get(day) == lunch


def query_room_by_teacher(user_id):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id, number, office_hours, lunch_duty, club_meeting, department
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
            }
            cur.execute("SELECT day, lunch FROM availabilities WHERE room_id = %s", (room["id"],))
            room["avail_map"] = {row[0]: row[1] for row in cur.fetchall()}
            return room
    finally:
        conn.close()


def get_room_teacher_id(room_number):
    """Return teacher_id for room number, or None."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT teacher_id FROM rooms WHERE number = %s", (room_number,))
            r = cur.fetchone()
            return r[0] if r and r[0] else None
    finally:
        conn.close()


def log_room_audit(room_id, user_id, action):
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


# ---------- Decorators ----------


def login_required(f):
    """Require user to be logged in."""
    def wrapped(*args, **kwargs):
        if "user_id" not in session:
            flash("Please log in.", "error")
            return redirect(url_for("login", next=request.url))
        return f(*args, **kwargs)
    wrapped.__name__ = f.__name__
    return wrapped


def admin_required(f):
    """Require user to be an admin."""
    def wrapped(*args, **kwargs):
        if "user_id" not in session or session.get("role") != "admin":
            flash("Admin access required.", "error")
            return redirect(url_for("index"))
        return f(*args, **kwargs)
    wrapped.__name__ = f.__name__
    return wrapped


# ---------- Routes ----------


@app.route("/")
def index():
    user = None
    if "user_id" in session:
        user = get_user_by_id(session["user_id"])
    return render_template("index.html", user=user)


@app.route("/dashboard")
@login_required
def dashboard():
    """Redirect to role-specific dashboard."""
    role = session.get("role")
    if role == "student":
        return redirect(url_for("student_dashboard"))
    elif role == "teacher":
        return redirect(url_for("teacher_dashboard"))
    elif role == "admin":
        return redirect(url_for("admin"))
    else:
        return redirect(url_for("index"))


@app.route("/dashboard/student")
@login_required
def student_dashboard():
    """Student dashboard with room finder and club request overview."""
    if session.get("role") != "student":
        flash("Students only.", "error")
        return redirect(url_for("index"))
    user = get_user_by_id(session["user_id"])
    return render_template("student_dashboard.html", user=user)


@app.route("/dashboard/teacher")
@login_required
def teacher_dashboard():
    """Teacher dashboard with request review and availability management."""
    if session.get("role") != "teacher":
        flash("Teachers only.", "error")
        return redirect(url_for("index"))
    user = get_user_by_id(session["user_id"])
    return render_template("teacher_dashboard.html", user=user)


@app.route("/register")
def register():
    """Show role selection page."""
    return render_template("register_choose_role.html")


def _do_register(name, email, password, role, department=None):
    """Shared registration logic for all role-specific routes."""
    # Validate inputs
    if not name or not email or not password:
        flash("Name, email, and password are required.", "error")
        return False
    
    if role not in ("teacher", "student", "admin"):
        flash("Invalid role selected.", "error")
        return False
    
    # Validate teacher email domain
    if role == "teacher" and not email.endswith("@henrico.k12.va.us"):
        flash("Teacher accounts must use a @henrico.k12.va.us email address.", "error")
        return False
    
    if get_user_by_email(email):
        flash("Email already registered.", "error")
        return False
    
    password_hash = generate_password_hash(password)
    # Generate 6-digit verification code
    verification_code = str(secrets.randbelow(1000000)).zfill(6)
    verification_expires = datetime.utcnow() + timedelta(hours=24)

    conn = get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                try:
                    cur.execute(
                        """INSERT INTO users (name, email, password_hash, role, department, email_verification_token, email_verification_expires)
                           VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                        (name, email, password_hash, role, department, verification_code, verification_expires),
                    )
                    user_id = cur.fetchone()[0]
                except Exception:
                    conn.rollback()
                    cur.execute(
                        """INSERT INTO users (name, email, password_hash, role, department)
                           VALUES (%s,%s,%s,%s,%s) RETURNING id""",
                        (name, email, password_hash, role, department),
                    )
                    user_id = cur.fetchone()[0]
    finally:
        conn.close()

    # Always set the pending verification email in session
    session['pending_verification_email'] = email
    session.modified = True
    
    if send_verification_email(email, verification_code):
        flash("Account created! Please check your email for a verification code.", "success")
        return True
    else:
        flash("Account created! If email didn't arrive, check your code in the terminal or try resending.", "warning")
        return True


@app.route("/register/teacher", methods=["GET", "POST"])
def register_teacher():
    """Teacher registration form."""
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        department = request.form.get("department", "").strip() or None
        
        if _do_register(name, email, password, "teacher", department):
            return redirect(url_for("verify_email_form"))
        return redirect(url_for("register_teacher"))
    
    return render_template("register_teacher.html")


@app.route("/register/student", methods=["GET", "POST"])
def register_student():
    """Student registration form."""
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        
        if _do_register(name, email, password, "student"):
            return redirect(url_for("verify_email_form"))
        return redirect(url_for("register_student"))
    
    return render_template("register_student.html")


@app.route("/register/admin", methods=["GET", "POST"])
def register_admin():
    """Admin registration form."""
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        
        if _do_register(name, email, password, "admin"):
            return redirect(url_for("verify_email_form"))
        return redirect(url_for("register_admin"))
    
    return render_template("register_admin.html")


@app.route("/verify-email", methods=["GET", "POST"])
def verify_email_form():
    """Verify email with 6-digit code."""
    email = session.get('pending_verification_email')
    
    if not email:
        flash("No pending verification. Please register first.", "error")
        return redirect(url_for("login"))
    
    if request.method == "POST":
        code = request.form.get("code", "").strip()
        
        if not code or len(code) != 6 or not code.isdigit():
            flash("Please enter a valid 6-digit code.", "error")
            return render_template("verify_email_form.html", email=email)
        
        # Find user by email and verify the code
        user = get_user_by_email(email)
        if not user:
            flash("Account not found.", "error")
            return redirect(url_for("login"))
        
        # Check code in database or dev mode
        code_valid = False
        
        # In dev mode, also check the in-memory codes
        if DEV_MODE and DEV_VERIFICATION_CODES.get(email) == code:
            code_valid = True
        else:
            # Check database for code
            conn = get_connection()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        """SELECT email_verification_token FROM users 
                           WHERE id = %s AND email_verification_expires > now()""",
                        (user["id"],),
                    )
                    row = cur.fetchone()
                    if row and row[0] == code:
                        code_valid = True
            finally:
                conn.close()
        
        if not code_valid:
            flash("Invalid or expired verification code.", "error")
            return render_template("verify_email_form.html", email=email)
        
        # Mark email as verified
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                try:
                    cur.execute(
                        """UPDATE users SET email_verified = TRUE, email_verification_token = NULL, 
                           email_verification_expires = NULL WHERE id = %s""",
                        (user["id"],),
                    )
                except Exception:
                    # If columns don't exist, just continue
                    pass
                conn.commit()
        finally:
            conn.close()
        
        # Clean up dev codes
        if DEV_MODE and email in DEV_VERIFICATION_CODES:
            del DEV_VERIFICATION_CODES[email]
        
        session.pop('pending_verification_email', None)
        flash("Email verified successfully! You can now log in.", "success")
        return redirect(url_for("login"))
    
    return render_template("verify_email_form.html", email=email)


@app.route("/resend-verification", methods=["GET", "POST"])
def resend_verification():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        user = get_user_by_email(email)
        if not user:
            flash("No account found with that email address.", "error")
            return redirect(url_for("resend_verification"))
        
        if user.get("email_verified", False):
            flash("This email address is already verified.", "info")
            return redirect(url_for("login"))

        # Generate new verification code
        verification_code = str(secrets.randbelow(1000000)).zfill(6)
        verification_expires = datetime.utcnow() + timedelta(hours=24)

        conn = get_connection()
        try:
            with conn:
                with conn.cursor() as cur:
                    try:
                        cur.execute(
                            "UPDATE users SET email_verification_token = %s, email_verification_expires = %s WHERE id = %s",
                            (verification_code, verification_expires, user["id"]),
                        )
                    except Exception:
                        # If columns don't exist, just continue
                        conn.rollback()
        finally:
            conn.close()

        # Always set the pending verification email
        session['pending_verification_email'] = email
        session.modified = True

        # Send verification email
        if send_verification_email(email, verification_code):
            flash("Verification code sent! Please check your email.", "success")
            return redirect(url_for("verify_email_form"))
        else:
            flash("If email didn't arrive, check your code in the terminal or enter it manually.", "warning")
            return redirect(url_for("verify_email_form"))
    
    return render_template("resend_verification.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        user = get_user_by_email(email)
        if user and check_password_hash(user["password_hash"], password):
            if not user.get("email_verified", False):
                flash("Please verify your email address before logging in. Check your email for the verification link.", "error")
                return redirect(url_for("login"))
            session.clear()
            session["user_id"] = user["id"]
            session["role"] = user["role"]
            flash("Logged in", "success")
            if user["role"] == "teacher":
                return redirect(url_for("teacher"))
            if user["role"] == "admin":
                return redirect(url_for("admin"))
            return redirect(url_for("student"))
        flash("Invalid credentials", "error")
        return redirect(url_for("login"))
    return render_template("login.html")


@app.route("/map")
def map():
    """Display the school map with room locations."""
    return render_template("map.html")


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
        email = request.form.get("email", "").strip().lower()
        user = get_user_by_email(email) if email else None
        if user:
            token = secrets.token_urlsafe(32)
            expires = datetime.utcnow() + timedelta(hours=1)
            conn = get_connection()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        """INSERT INTO password_reset_tokens (user_id, token, expires_at)
                           VALUES (%s,%s,%s)""",
                        (user["id"], token, expires),
                    )
                conn.commit()
            finally:
                conn.close()
            reset_url = url_for("reset_password", token=token, _external=True)
            if send_password_reset_email(user["email"], reset_url):
                flash("Check your email for the reset link. It may take a minute to arrive.", "success")
            else:
                flash(f"Email not configured. Use this link to reset: {reset_url}", "info")
        else:
            flash("If that email exists, we sent a reset link.", "info")
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
                (token, datetime.utcnow()),
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


@app.route("/teacher", methods=["GET", "POST"])
def teacher():
    if "user_id" not in session:
        flash("Please log in as a teacher to edit availability.", "error")
        return redirect(url_for("login"))
    if session.get("role") not in ("teacher", "admin"):
        flash("Only teacher accounts can edit availability.", "error")
        return redirect(url_for("index"))

    user = get_user_by_id(session["user_id"])

    if request.method == "POST":
        room_number = request.form.get("room", "").strip()
        confirm_overwrite = request.form.get("confirm_overwrite") == "1"
        office_hours = request.form.get("office_hours", "").strip() or None
        lunch_duty = request.form.get("lunch_duty", "").strip() or None
        club_meeting = request.form.get("club_meeting", "").strip() or None
        department = request.form.get("department", "").strip() or None
        if not room_number:
            flash("Room number is required.", "error")
            return redirect(url_for("teacher"))

        existing_teacher_id = get_room_teacher_id(room_number)
        if existing_teacher_id and existing_teacher_id != user["id"] and not confirm_overwrite:
            flash(
                f"Room {room_number} is already assigned to another teacher. Check the box to confirm overwrite.",
                "error",
            )
            return redirect(url_for("teacher", confirm_overwrite=room_number))

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
                            (user["id"], office_hours, lunch_duty, club_meeting, department, room_id),
                        )
                        log_room_audit(room_id, user["id"], "update")
                    else:
                        cur.execute(
                            """INSERT INTO rooms (number, teacher_id, office_hours, lunch_duty, club_meeting, department)
                               VALUES (%s,%s,%s,%s,%s,%s) RETURNING id""",
                            (room_number, user["id"], office_hours, lunch_duty, club_meeting, department),
                        )
                        room_id = cur.fetchone()[0]
                        log_room_audit(room_id, user["id"], "create")

                    cur.execute("DELETE FROM availabilities WHERE room_id = %s", (room_id,))
                    for d in ["M", "T", "W", "R", "F"]:
                        val = request.form.get(f"day_{d}", "N")
                        if val not in ("A", "B", "N", "AB"):
                            val = "N"
                        cur.execute("INSERT INTO availabilities (room_id, day, lunch) VALUES (%s,%s,%s)", (room_id, d, val))
        finally:
            conn.close()

        flash("Availability saved.", "success")
        return redirect(url_for("teacher"))

    user_room = query_room_by_teacher(user["id"])
    conn = get_connection()
    rooms = []
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT r.id, r.number, r.office_hours, r.lunch_duty, r.club_meeting, u.name
                   FROM rooms r LEFT JOIN users u ON r.teacher_id = u.id ORDER BY r.number"""
            )
            for r in cur.fetchall():
                rooms.append({
                    "id": r[0],
                    "number": r[1],
                    "office_hours": r[2],
                    "lunch_duty": r[3],
                    "club_meeting": r[4],
                    "teacher_name": r[5],
                })
    finally:
        conn.close()

    departments = []
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute("SELECT department, office_hours, lunch_duty, default_avail FROM department_defaults")
            for row in cur.fetchall():
                departments.append({
                    "department": row[0],
                    "office_hours": row[1],
                    "lunch_duty": row[2],
                    "default_avail": row[3] or {},
                })
        conn.close()
    except Exception:
        pass

    confirm_overwrite = request.args.get("confirm_overwrite")
    return render_template(
        "teacher.html",
        user=user,
        user_room=user_room,
        rooms=rooms,
        departments=departments,
        confirm_overwrite=confirm_overwrite,
    )


@app.route("/student")
def student():
    rooms = build_rooms_for_display(include_availability_map=True)
    if not rooms:
        flash("Database is temporarily unavailable. Showing limited data.", "warning")
    current_day, current_lunch = get_current_day_lunch()
    user = None
    if "user_id" in session:
        try:
            user = get_user_by_id(session["user_id"])
        except Exception:
            user = None
    favorites = []
    if user and user.get("role") == "student":
        conn = None
        try:
            conn = get_connection()
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT room_id FROM student_favorites WHERE user_id = %s",
                    (user["id"],),
                )
                favorites = [r[0] for r in cur.fetchall()]
        except Exception:
            favorites = []
        finally:
            if conn is not None:
                conn.close()
    return render_template(
        "student.html",
        rooms=rooms,
        current_day=current_day,
        current_lunch=current_lunch,
        user=user,
        favorites=favorites,
    )


@app.route("/student/print")
def student_print():
    rooms = build_rooms_for_display(include_availability_map=True)
    current_day, current_lunch = get_current_day_lunch()
    rooms_sorted = sorted(rooms.items(), key=lambda x: (int(x[0]) if str(x[0]).isdigit() else 999, x[0]))
    return render_template(
        "student_print.html",
        rooms=rooms,
        rooms_sorted=rooms_sorted,
        current_day=current_day,
        current_lunch=current_lunch,
    )


@app.route("/api/rooms")
def api_rooms():
    """Rooms with per-day availability (availability_map: day -> A|B|N) for real A/B filtering."""
    rooms = build_rooms_for_display(include_availability_map=True)
    current_day, current_lunch = get_current_day_lunch()
    return jsonify({
        "rooms": rooms,
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
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            if request.method == "POST":
                cur.execute(
                    "INSERT INTO student_favorites (user_id, room_id) VALUES (%s,%s) ON CONFLICT (user_id, room_id) DO NOTHING",
                    (user_id, int(room_id)),
                )
            else:
                cur.execute("DELETE FROM student_favorites WHERE user_id = %s AND room_id = %s", (user_id, int(room_id)))
        conn.commit()
    finally:
        conn.close()
    return jsonify({"ok": True})


# ---------- Club requests ----------


@app.route("/club-request", methods=["GET", "POST"])
@login_required
def club_request():
    role = session.get("role")
    # Only students can create requests, teachers/admins can view and approve/reject
    if request.method == "POST" and role != "student":
        flash("Only students can request club meetings.", "error")
        return redirect(url_for("club_request"))
    user = get_user_by_id(session["user_id"])
    if request.method == "POST":
        room_id = request.form.get("room_id")
        club_name = request.form.get("club_name", "").strip()
        day = request.form.get("day", "M")
        lunch = request.form.get("lunch", "B")
        if not room_id or not club_name or day not in "MTRWF" or lunch not in ("A", "B"):
            flash("Room, club name, day, and lunch required.", "error")
            return redirect(url_for("club_request"))
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO club_requests (room_id, requested_by, club_name, day, lunch, status)
                       VALUES (%s,%s,%s,%s,%s,'pending')""",
                    (int(room_id), user["id"], club_name, day, lunch),
                )
            conn.commit()
        finally:
            conn.close()
        flash("Request submitted. Teacher will review.", "success")
        return redirect(url_for("club_request"))

    conn = get_connection()
    rooms = []
    requests_list = []
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, number FROM rooms ORDER BY number")
            rooms = [{"id": r[0], "number": r[1]} for r in cur.fetchall()]
            if session.get("role") in ("teacher", "admin"):
                cur.execute(
                    """SELECT cr.id, cr.room_id, cr.club_name, cr.day, cr.lunch, cr.status, r.number, u.name
                       FROM club_requests cr JOIN rooms r ON cr.room_id = r.id
                       LEFT JOIN users u ON cr.requested_by = u.id
                       ORDER BY cr.created_at DESC"""
                )
                for r in cur.fetchall():
                    requests_list.append({
                        "id": r[0],
                        "room_id": r[1],
                        "club_name": r[2],
                        "day": r[3],
                        "lunch": r[4],
                        "status": r[5],
                        "room_number": r[6],
                        "requested_by": r[7],
                    })
            else:
                cur.execute(
                    """SELECT cr.id, cr.room_id, cr.club_name, cr.day, cr.lunch, cr.status, r.number
                       FROM club_requests cr JOIN rooms r ON cr.room_id = r.id
                       WHERE cr.requested_by = %s ORDER BY cr.created_at DESC""",
                    (user["id"],),
                )
                for r in cur.fetchall():
                    requests_list.append({
                        "id": r[0],
                        "room_id": r[1],
                        "club_name": r[2],
                        "day": r[3],
                        "lunch": r[4],
                        "status": r[5],
                        "room_number": r[6],
                    })
    finally:
        conn.close()
    return render_template("club_request.html", user=user, rooms=rooms, requests_list=requests_list)


@app.route("/club-request/<int:req_id>/<action>", methods=["POST"])
@login_required
def club_request_review(req_id, action):
    # Only teachers and admins can approve/reject requests
    if session.get("role") not in ("teacher", "admin"):
        flash("You don't have permission to review requests.", "error")
        return redirect(url_for("club_request"))
    
    if action not in ("approve", "reject"):
        return redirect(url_for("club_request"))
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT room_id FROM club_requests WHERE id = %s AND status = 'pending'", (req_id,))
            r = cur.fetchone()
            if not r:
                flash("Request not found or already reviewed.", "error")
                return redirect(url_for("club_request"))
            room_id = r[0]
            new_status = "approved" if action == "approve" else "rejected"
            cur.execute(
                "UPDATE club_requests SET status = %s, reviewed_by = %s, reviewed_at = now() WHERE id = %s",
                (new_status, session["user_id"], req_id),
            )
            if action == "approve":
                cur.execute(
                    "SELECT club_meeting FROM rooms WHERE id = %s",
                    (room_id,),
                )
                row = cur.fetchone()
                existing = (row[0] or "") + ";" if row and row[0] else ""
                cur.execute(
                    "SELECT club_name, day, lunch FROM club_requests WHERE id = %s",
                    (req_id,),
                )
                cr = cur.fetchone()
                if cr:
                    new_entry = f"{cr[0]} {cr[1]} {cr[2]} lunch"
                    cur.execute("UPDATE rooms SET club_meeting = %s WHERE id = %s", ((existing + new_entry).strip(";"), room_id))
        conn.commit()
    finally:
        conn.close()
    flash("Request " + ("approved" if action == "approve" else "rejected") + ".", "success")
    return redirect(url_for("club_request"))


# ---------- Admin ----------


@app.route("/admin")
@admin_required
def admin():
    conn = get_connection()
    users_list = []
    rooms_list = []
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, name, email, role, department, created_at FROM users ORDER BY name")
            for r in cur.fetchall():
                users_list.append({
                    "id": r[0],
                    "name": r[1],
                    "email": r[2],
                    "role": r[3],
                    "department": r[4],
                    "created_at": str(r[5])[:19] if r[5] else None,
                })
            cur.execute(
                """SELECT r.id, r.number, r.office_hours, r.updated_at, u.name
                   FROM rooms r LEFT JOIN users u ON r.teacher_id = u.id ORDER BY r.number"""
            )
            for r in cur.fetchall():
                rooms_list.append({
                    "id": r[0],
                    "number": r[1],
                    "office_hours": r[2],
                    "updated_at": str(r[3])[:19] if r[3] else None,
                    "teacher_name": r[4],
                })
    finally:
        conn.close()
    return render_template("admin.html", users=users_list, rooms=rooms_list)


@app.route("/admin/user/<int:user_id>/role", methods=["POST"])
@admin_required
def admin_set_role(user_id):
    role = request.form.get("role")
    if role not in ("teacher", "student", "admin", "club_president"):
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
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM users WHERE id = %s", (user_id,))
        conn.commit()
    finally:
        conn.close()
    flash("User deleted.", "success")
    return redirect(url_for("admin"))


@app.route("/admin/department-default", methods=["GET", "POST"])
@admin_required
def admin_department_default():
    if request.method == "POST":
        department = request.form.get("department", "").strip()
        office_hours = request.form.get("office_hours", "").strip() or None
        lunch_duty = request.form.get("lunch_duty", "").strip() or None
        default_avail = {}
        for d in "MTRWF":
            default_avail[d] = request.form.get(f"def_{d}", "N") if request.form.get(f"def_{d}") in ("A", "B", "N") else "N"
        import json
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
                    "default_avail": r[3] or {},
                })
    finally:
        conn.close()
    return render_template("admin_department_default.html", defaults=defaults)


if __name__ == "__main__":
    import threading
    import time
    import webbrowser

    _local_url = "http://127.0.0.1:5000/"

    def _open_browser():
        time.sleep(1.0)
        webbrowser.open(_local_url)

    threading.Thread(target=_open_browser, daemon=True).start()
    print(f" * Onelunch — opening {_local_url} in your browser")
    app.run(
        debug=True,
        host="127.0.0.1",
        port=5000,
        threaded=True,
        use_reloader=False,
    )
