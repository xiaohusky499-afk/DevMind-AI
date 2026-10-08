BEGIN;

ALTER TABLE knowledge_bases
    DROP CONSTRAINT IF EXISTS knowledge_bases_status_check;

ALTER TABLE knowledge_bases
    ADD CONSTRAINT knowledge_bases_status_check
    CHECK (status IN ('creating', 'ready', 'indexing', 'failed', 'archived', 'disabled', 'deleting'));

COMMIT;
