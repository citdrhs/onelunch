"""Seed script to add sample users, rooms and availabilities to the PostgreSQL database."""
from werkzeug.security import generate_password_hash
from db import get_connection, init_db

SAMPLE_USERS = [
    {'name': 'Mr. Campbell', 'email': 'campbell@school.edu', 'password': 'password123', 'role': 'teacher'},
    {'name': 'Ms. Diaz', 'email': 'diaz@school.edu', 'password': 'password123', 'role': 'teacher'},
    {'name': 'Student One', 'email': 'student1@school.edu', 'password': 'studentpass', 'role': 'student'},
]

SAMPLE_ROOMS = [
    {'number': '230', 'teacher_email': 'campbell@school.edu', 'office_hours': 'Wed B; Thu B', 'lunch_duty': 'Fri A', 'club_meeting': 'Debate Thu B', 'avail': {'M':'A','T':'A','W':'B','R':'A','F':'N'}},
    {'number': '112', 'teacher_email': 'diaz@school.edu', 'office_hours': 'Mon & Wed B', 'lunch_duty': 'Tue A', 'club_meeting': '', 'avail': {'M':'B','T':'N','W':'B','R':'A','F':'A'}},
]


def seed():
    init_db()
    conn = get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                # insert users
                for u in SAMPLE_USERS:
                    cur.execute('SELECT id FROM users WHERE email = %s', (u['email'],))
                    if cur.fetchone():
                        continue
                    cur.execute('INSERT INTO users (name, email, password_hash, role) VALUES (%s,%s,%s,%s) RETURNING id',
                                (u['name'], u['email'], generate_password_hash(u['password']), u['role']))
                    cur.fetchone()

                # insert rooms and availabilities
                for r in SAMPLE_ROOMS:
                    cur.execute('SELECT id FROM users WHERE email = %s', (r['teacher_email'],))
                    t = cur.fetchone()
                    teacher_id = t[0] if t else None
                    cur.execute('SELECT id FROM rooms WHERE number = %s', (r['number'],))
                    existing = cur.fetchone()
                    if existing:
                        room_id = existing[0]
                        cur.execute('UPDATE rooms SET teacher_id=%s, office_hours=%s, lunch_duty=%s, club_meeting=%s WHERE id=%s',
                                    (teacher_id, r['office_hours'], r['lunch_duty'], r['club_meeting'], room_id))
                    else:
                        cur.execute('INSERT INTO rooms (number, teacher_id, office_hours, lunch_duty, club_meeting) VALUES (%s,%s,%s,%s,%s) RETURNING id',
                                    (r['number'], teacher_id, r['office_hours'], r['lunch_duty'], r['club_meeting']))
                        result = cur.fetchone()
                        room_id = result[0] if result else None
                    # replace availabilities
                    cur.execute('DELETE FROM availabilities WHERE room_id = %s', (room_id,))
                    for day, lunch in r['avail'].items():
                        cur.execute('INSERT INTO availabilities (room_id, day, lunch) VALUES (%s,%s,%s)', (room_id, day, lunch))
    finally:
        conn.close()


if __name__ == '__main__':
    print('Seeding database...')
    seed()
    print('Done.')
