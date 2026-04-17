# floor plan ids for both svg maps, label is what humans read
# skips 204 and 237, band areas are two rows
MAP_ROOMS = []

for _n in range(101, 131):
    MAP_ROOMS.append((1, str(_n), f"Room {_n}"))

_floor2 = [201, 202, 203] + [n for n in range(205, 244) if n != 237]
for _n in _floor2:
    MAP_ROOMS.append((2, str(_n), f"Room {_n}"))

for _code in ("A11", "A12", "A13", "A14"):
    MAP_ROOMS.append((1, _code, _code))
for _code in ("H11", "H12", "H13"):
    MAP_ROOMS.append((1, _code, _code))

MAP_ROOMS.extend(
    [
        (1, "Auditorium", "Auditorium"),
        (1, "Chorus", "Chorus"),
        (1, "Band 1", "Band 1"),
        (1, "Band 2", "Band 2"),
        (1, "Ensemble", "Ensemble"),
        (1, "Gym", "Gym"),
    ]
)

MAP_ROOMS.extend(
    [
        (1, "Media 1", "Media 1"),
        (1, "Media 2", "Media 2"),
        (1, "Help Desk", "Help Desk"),
    ]
)

MAP_ROOMS.extend(
    [
        (1, "Career Info Center", "Career Info Center"),
        (1, "Counselor Office", "Counselor Office"),
    ]
)

for _sc in ("SC1", "SC2", "SC3", "SC4"):
    MAP_ROOMS.append((1, _sc, _sc))

MAP_ROOMS.append((1, "42", "Room 42"))


# upsert into rooms

def seed_map_rooms():
    import db as db_mod

    conn = db_mod.get_connection()
    try:
        if db_mod.USE_SQLITE:
            raw = conn._conn
            cols = [r[1] for r in raw.execute("PRAGMA table_info(rooms)").fetchall()]
            if "label" not in cols:
                raw.execute("ALTER TABLE rooms ADD COLUMN label TEXT")
                raw.commit()
        with conn.cursor() as cur:
            for floor, number, label in MAP_ROOMS:
                if db_mod.USE_SQLITE:
                    cur.execute(
                        """INSERT INTO rooms (number, floor, status, label) VALUES (?, ?, 'open', ?)
                           ON CONFLICT(number) DO UPDATE SET
                             floor = excluded.floor,
                             label = excluded.label""",
                        (number, floor, label),
                    )
                else:
                    cur.execute(
                        """INSERT INTO rooms (number, floor, status, label)
                           VALUES (%s, %s, 'open', %s)
                           ON CONFLICT (number) DO UPDATE SET
                             floor = EXCLUDED.floor,
                             label = EXCLUDED.label""",
                        (number, floor, label),
                    )
            for obsolete in ("Simu", "Band"):
                try:
                    if db_mod.USE_SQLITE:
                        cur.execute(
                            "DELETE FROM rooms WHERE number = ? AND teacher_id IS NULL",
                            (obsolete,),
                        )
                    else:
                        cur.execute(
                            "DELETE FROM rooms WHERE number = %s AND teacher_id IS NULL",
                            (obsolete,),
                        )
                except Exception:
                    pass
        conn.commit()
    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        print("[WARN] seed_map_rooms:", e)
    finally:
        try:
            conn.close()
        except Exception:
            pass
