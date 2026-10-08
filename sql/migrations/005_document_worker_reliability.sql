BEGIN;

ALTER TABLE document_tasks DROP CONSTRAINT IF EXISTS document_tasks_status_check;
ALTER TABLE document_tasks
    ADD CONSTRAINT document_tasks_status_check
    CHECK (status IN ('pending', 'running', 'retry_wait', 'completed', 'failed', 'dead'));

ALTER TABLE document_tasks
    ADD COLUMN IF NOT EXISTS attempt_count smallint NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    ADD COLUMN IF NOT EXISTS max_attempts smallint NOT NULL DEFAULT 3 CHECK (max_attempts > 0),
    ADD COLUMN IF NOT EXISTS available_at timestamptz NOT NULL DEFAULT now(),
    ADD COLUMN IF NOT EXISTS heartbeat_at timestamptz,
    ADD COLUMN IF NOT EXISTS lease_expires_at timestamptz,
    ADD COLUMN IF NOT EXISTS worker_id varchar(128),
    ADD COLUMN IF NOT EXISTS timeout_seconds integer NOT NULL DEFAULT 1800 CHECK (timeout_seconds > 0);

DROP INDEX IF EXISTS uq_document_tasks_active;
CREATE UNIQUE INDEX uq_document_tasks_active
    ON document_tasks (document_id)
    WHERE status IN ('pending', 'running', 'retry_wait');
CREATE INDEX IF NOT EXISTS idx_document_tasks_ready
    ON document_tasks (available_at, created_at)
    WHERE status IN ('pending', 'retry_wait');
CREATE INDEX IF NOT EXISTS idx_document_tasks_lease
    ON document_tasks (lease_expires_at)
    WHERE status='running';

COMMIT;
