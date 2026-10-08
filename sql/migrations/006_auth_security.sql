BEGIN;

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS failed_login_count smallint NOT NULL DEFAULT 0 CHECK (failed_login_count >= 0),
    ADD COLUMN IF NOT EXISTS locked_until timestamptz,
    ADD COLUMN IF NOT EXISTS last_failed_login_at timestamptz,
    ADD COLUMN IF NOT EXISTS last_login_at timestamptz;

CREATE INDEX IF NOT EXISTS idx_users_locked_until
    ON users (locked_until) WHERE status='locked';

COMMIT;
