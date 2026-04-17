# optional sample rows for manual seed runs
from werkzeug.security import generate_password_hash
from db import get_connection, init_db, insert_returning_id

SAMPLE_USERS = [
    {
        "name": "Admin",
        "email": "admin@admin.local",
        "password": "admin",
        "role": "admin",
        "email_verified": True,
    },
    {
        "name": "Mr. Campbell",
        "email": "campbell@school.edu",
        "password": "password123",
        "role": "teacher",
        "email_verified": True,
    },
    {
        "name": "Ms. Diaz",
        "email": "diaz@school.edu",
        "password": "password123",
        "role": "teacher",
        "email_verified": True,
    },
    {
        "name": "Student One",
        "email": "student1@school.edu",
        "password": "studentpass",
        "role": "student",
        "email_verified": True,
        "default_lunch": "A",
        "default_floor": 1,
    },
    {
        "name": "Student Two",
        "email": "student2@school.edu",
        "password": "studentpass",
        "role": "student",
        "email_verified": True,
        "default_lunch": "B",
        "default_floor": 2,
    },
    {
        "name": "Alex President",
        "email": "president@school.edu",
        "password": "studentpass",
        "role": "club_president",
        "email_verified": True,
        "club_name": "Chess Club",
    },
]

SAMPLE_ROOMS = [
    {
        "number": "230",
        "floor": 2,
        "teacher_email": "campbell@school.edu",
        "office_hours": "Wed B; Thu B",
        "lunch_duty": "Fri A",
        "club_meeting": "Debate Thu B",
        "todays_note": "Science help available at A lunch today.",
        "avail": {"M": "A", "T": "A", "W": "B", "R": "A", "F": "N"},
    },
    {
        "number": "112",
        "floor": 1,
        "teacher_email": "diaz@school.edu",
        "office_hours": "Mon & Wed B",
        "lunch_duty": "Tue A",
        "club_meeting": "",
        "todays_note": "Quiet study — headsets only please.",
        "avail": {"M": "B", "T": "N", "W": "B", "R": "A", "F": "A"},
    },
]


def seed():
    init_db()
    conn = get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                for u in SAMPLE_USERS:
                    cur.execute("SELECT id FROM users WHERE email = %s", (u["email"],))
                    if cur.fetchone():
                        continue
                    insert_returning_id(
                        cur,
                        """INSERT INTO users (name, email, password_hash, role, email_verified, club_name, default_lunch, default_floor)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                        (
                            u["name"],
                            u["email"],
                            generate_password_hash(u["password"]),
                            u["role"],
                            u.get("email_verified", False),
                            u.get("club_name"),
                            u.get("default_lunch"),
                            u.get("default_floor"),
                        ),
                    )

                for r in SAMPLE_ROOMS:
                    cur.execute("SELECT id FROM users WHERE email = %s", (r["teacher_email"],))
                    t = cur.fetchone()
                    teacher_id = t[0] if t else None
                    cur.execute("SELECT id FROM rooms WHERE number = %s", (r["number"],))
                    existing = cur.fetchone()
                    floor = r.get("floor", 1)
                    note = (r.get("todays_note") or "").strip() or None
                    if existing:
                        room_id = existing[0]
                        cur.execute(
                            """UPDATE rooms SET teacher_id=%s, office_hours=%s, lunch_duty=%s, club_meeting=%s, floor=%s,
                               todays_note=%s, note_set_date=CASE WHEN %s IS NULL THEN NULL ELSE CURRENT_DATE END
                               WHERE id=%s""",
                            (
                                teacher_id,
                                r["office_hours"],
                                r["lunch_duty"],
                                r["club_meeting"],
                                floor,
                                note,
                                note,
                                room_id,
                            ),
                        )
                    else:
                        room_id = insert_returning_id(
                            cur,
                            """INSERT INTO rooms (number, teacher_id, office_hours, lunch_duty, club_meeting, floor, todays_note, note_set_date)
                               VALUES (%s,%s,%s,%s,%s,%s,%s,CASE WHEN %s IS NULL THEN NULL ELSE CURRENT_DATE END) RETURNING id""",
                            (
                                r["number"],
                                teacher_id,
                                r["office_hours"],
                                r["lunch_duty"],
                                r["club_meeting"],
                                floor,
                                note,
                                note,
                            ),
                        )
                    cur.execute("DELETE FROM availabilities WHERE room_id = %s", (room_id,))
                    for day, lunch in r["avail"].items():
                        cur.execute(
                            "INSERT INTO availabilities (room_id, day, lunch) VALUES (%s,%s,%s)",
                            (room_id, day, lunch),
                        )
    finally:
        conn.close()


if __name__ == "__main__":
    print("Seeding database...")
    seed()
    print("Done.")
