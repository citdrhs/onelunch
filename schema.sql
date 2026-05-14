CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL,
    department TEXT,
    email_verified BOOLEAN DEFAULT FALSE,
    email_verification_token TEXT,
    email_verification_expires TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now()
);

CREATE TABLE IF NOT EXISTS rooms (
    id SERIAL PRIMARY KEY,
    number TEXT NOT NULL UNIQUE,
    teacher_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    department TEXT,
    office_hours TEXT,
    lunch_duty TEXT,
    club_meeting TEXT,
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'quiet_study', 'closed')),
    floor INTEGER NOT NULL DEFAULT 1,
    todays_note TEXT,
    note_set_date DATE,
    label TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now()
);

CREATE TABLE IF NOT EXISTS availabilities (
    id SERIAL PRIMARY KEY,
    room_id INTEGER NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
    day CHAR(1) NOT NULL,
    lunch CHAR(2) NOT NULL CHECK (lunch IN ('A','B','N','AB')),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
    UNIQUE (room_id, day)
);

CREATE TABLE IF NOT EXISTS club_requests (
    id SERIAL PRIMARY KEY,
    room_id INTEGER NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
    requested_by INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    club_name TEXT NOT NULL,
    day CHAR(1) NOT NULL,
    lunch CHAR(1) NOT NULL CHECK (lunch IN ('A','B')),
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected')),
    reviewed_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    reviewed_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now()
);

CREATE TABLE IF NOT EXISTS password_reset_tokens (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token TEXT NOT NULL UNIQUE,
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
    used_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now()
);

CREATE TABLE IF NOT EXISTS student_favorites (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    room_id INTEGER NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
    UNIQUE (user_id, room_id)
);

CREATE TABLE IF NOT EXISTS department_defaults (
    id SERIAL PRIMARY KEY,
    department TEXT NOT NULL UNIQUE,
    office_hours TEXT,
    lunch_duty TEXT,
    default_avail JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now()
);

CREATE TABLE IF NOT EXISTS room_audit_log (
    id SERIAL PRIMARY KEY,
    room_id INTEGER NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
    user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    action TEXT NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now()
);

CREATE TABLE IF NOT EXISTS notifications (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    message TEXT NOT NULL,
    link TEXT,
    is_read BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now()
);

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'users' AND column_name = 'department') THEN
    ALTER TABLE users ADD COLUMN department TEXT;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'users' AND column_name = 'updated_at') THEN
    ALTER TABLE users ADD COLUMN updated_at TIMESTAMP WITH TIME ZONE DEFAULT now();
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'users' AND column_name = 'club_name') THEN
    ALTER TABLE users ADD COLUMN club_name VARCHAR(100);
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'users' AND column_name = 'default_lunch') THEN
    ALTER TABLE users ADD COLUMN default_lunch CHAR(1);
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'users' AND column_name = 'default_floor') THEN
    ALTER TABLE users ADD COLUMN default_floor INTEGER;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'rooms' AND column_name = 'department') THEN
    ALTER TABLE rooms ADD COLUMN department TEXT;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'rooms' AND column_name = 'updated_at') THEN
    ALTER TABLE rooms ADD COLUMN updated_at TIMESTAMP WITH TIME ZONE DEFAULT now();
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'rooms' AND column_name = 'status') THEN
    ALTER TABLE rooms ADD COLUMN status TEXT NOT NULL DEFAULT 'open';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'rooms' AND column_name = 'floor') THEN
    ALTER TABLE rooms ADD COLUMN floor INTEGER NOT NULL DEFAULT 1;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'rooms' AND column_name = 'todays_note') THEN
    ALTER TABLE rooms ADD COLUMN todays_note TEXT;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'rooms' AND column_name = 'note_set_date') THEN
    ALTER TABLE rooms ADD COLUMN note_set_date DATE;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'rooms' AND column_name = 'label') THEN
    ALTER TABLE rooms ADD COLUMN label TEXT;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'rooms_status_check') THEN
    BEGIN
      ALTER TABLE rooms ADD CONSTRAINT rooms_status_check CHECK (status IN ('open', 'quiet_study', 'closed'));
    EXCEPTION WHEN OTHERS THEN NULL;
    END;
  END IF;
  -- public club form can omit logged in user
  IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'club_requests') THEN
    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'club_requests' AND column_name = 'requested_by' AND is_nullable = 'NO') THEN
      ALTER TABLE club_requests ALTER COLUMN requested_by DROP NOT NULL;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'club_requests' AND column_name = 'half') THEN
      ALTER TABLE club_requests ADD COLUMN half TEXT NOT NULL DEFAULT 'full';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'club_requests' AND column_name = 'notes') THEN
      ALTER TABLE club_requests ADD COLUMN notes TEXT;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'club_requests' AND column_name = 'requester_name') THEN
      ALTER TABLE club_requests ADD COLUMN requester_name TEXT;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'club_requests' AND column_name = 'requester_email') THEN
      ALTER TABLE club_requests ADD COLUMN requester_email TEXT;
    END IF;
    UPDATE club_requests SET requester_name = COALESCE(requester_name, 'Student') WHERE requester_name IS NULL;
 END IF;
END $$;
