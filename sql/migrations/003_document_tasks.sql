BEGIN;

CREATE TABLE IF NOT EXISTS document_tasks (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id uuid NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    task_type varchar(24) NOT NULL CHECK (task_type IN ('ingest', 'reindex', 'delete')),
    status varchar(16) NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'running', 'completed', 'failed')),
    progress smallint NOT NULL DEFAULT 0 CHECK (progress BETWEEN 0 AND 100),
    error_code varchar(64),
    error_message text,
    started_at timestamptz,
    completed_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_document_tasks_document_created
    ON document_tasks (document_id, created_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS uq_document_tasks_active
    ON document_tasks (document_id) WHERE status IN ('pending', 'running');

COMMIT;
