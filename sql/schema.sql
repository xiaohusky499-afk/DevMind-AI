-- DevMind AI PostgreSQL 16 schema
-- 业务事实存 PostgreSQL，原始文件存 MinIO，向量索引存 Milvus。

BEGIN;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE OR REPLACE FUNCTION set_updated_at() RETURNS trigger AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TABLE IF NOT EXISTS organizations (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name varchar(128) NOT NULL,
    slug varchar(64) NOT NULL UNIQUE,
    sso_provider varchar(64),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

INSERT INTO organizations (id, name, slug)
VALUES ('00000000-0000-0000-0000-000000000001', 'DevMind AI 默认组织', 'default')
ON CONFLICT (id) DO NOTHING;

CREATE TABLE IF NOT EXISTS users (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id uuid NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001' REFERENCES organizations(id),
    username varchar(64) NOT NULL UNIQUE,
    password_hash text NOT NULL,
    nickname varchar(100),
    email varchar(320),
    phone varchar(20) UNIQUE,
    team varchar(100) NOT NULL DEFAULT 'default',
    security_level varchar(20) NOT NULL DEFAULT 'team' CHECK (security_level IN ('public', 'team', 'confidential')),
    status varchar(16) NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'disabled', 'locked')),
    is_active boolean NOT NULL DEFAULT true,
    failed_login_count smallint NOT NULL DEFAULT 0 CHECK (failed_login_count >= 0),
    locked_until timestamptz,
    last_failed_login_at timestamptz,
    last_login_at timestamptz,
    email_verified_at timestamptz,
    phone_verified_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (organization_id, email)
);
CREATE INDEX IF NOT EXISTS idx_users_org_status ON users (organization_id, status);
CREATE INDEX IF NOT EXISTS idx_users_locked_until ON users (locked_until) WHERE status='locked';
CREATE INDEX IF NOT EXISTS idx_users_team ON users (organization_id, team);

CREATE TABLE IF NOT EXISTS user_profiles (
    user_id uuid PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    organization_id uuid NOT NULL REFERENCES organizations(id),
    display_name varchar(64) NOT NULL,
    avatar_url text,
    gender varchar(16) NOT NULL DEFAULT 'unspecified' CHECK (gender IN ('male', 'female', 'unspecified')),
    birth_date date,
    department_name varchar(128),
    job_title varchar(128),
    employee_no varchar(64),
    joined_at date,
    bio varchar(500),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (organization_id, employee_no)
);

CREATE TABLE IF NOT EXISTS auth_refresh_tokens (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash text NOT NULL UNIQUE,
    device_name varchar(160),
    ip_hash varchar(128),
    expires_at timestamptz NOT NULL,
    revoked_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_refresh_tokens_user_active ON auth_refresh_tokens (user_id, expires_at) WHERE revoked_at IS NULL;

CREATE TABLE IF NOT EXISTS knowledge_bases (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id uuid NOT NULL REFERENCES organizations(id),
    name varchar(160) NOT NULL,
    description text NOT NULL DEFAULT '',
    status varchar(16) NOT NULL DEFAULT 'ready' CHECK (status IN ('creating', 'ready', 'indexing', 'failed', 'archived', 'disabled', 'deleting')),
    document_count integer NOT NULL DEFAULT 0 CHECK (document_count >= 0),
    created_by uuid REFERENCES users(id),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    deleted_at timestamptz,
    UNIQUE (organization_id, name)
);

INSERT INTO knowledge_bases (id, organization_id, name, description)
VALUES ('00000000-0000-0000-0000-000000000101', '00000000-0000-0000-0000-000000000001', '默认知识库', '本地开发与迁移兼容知识库')
ON CONFLICT (id) DO NOTHING;

CREATE TABLE IF NOT EXISTS knowledge_base_members (
    knowledge_base_id uuid NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
    user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role varchar(16) NOT NULL CHECK (role IN ('viewer', 'editor', 'admin')),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (knowledge_base_id, user_id)
);

CREATE TABLE IF NOT EXISTS documents (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    knowledge_base_id uuid NOT NULL DEFAULT '00000000-0000-0000-0000-000000000101' REFERENCES knowledge_bases(id),
    name varchar(500) NOT NULL DEFAULT '',
    doc_source text NOT NULL,
    source_type varchar(32) NOT NULL DEFAULT 'file',
    doc_type varchar(50) NOT NULL DEFAULT 'document',
    storage_key text,
    mime_type varchar(160),
    size_bytes bigint CHECK (size_bytes IS NULL OR size_bytes >= 0),
    team varchar(100),
    system_name varchar(100),
    version varchar(50),
    security_level varchar(20) NOT NULL DEFAULT 'team' CHECK (security_level IN ('public', 'team', 'confidential')),
    status varchar(24) NOT NULL DEFAULT 'uploaded' CHECK (status IN ('uploaded', 'parsing', 'chunking', 'embedding', 'indexing', 'ready', 'failed', 'active', 'deprecated', 'deleted')),
    checksum varchar(128),
    chunk_count integer NOT NULL DEFAULT 0 CHECK (chunk_count >= 0),
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    error_code varchar(64),
    error_message text,
    last_updated timestamptz,
    created_by uuid REFERENCES users(id),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    deleted_at timestamptz,
    UNIQUE (knowledge_base_id, doc_source, doc_type)
);
CREATE INDEX IF NOT EXISTS idx_documents_kb_status ON documents (knowledge_base_id, status, created_at DESC) WHERE deleted_at IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_documents_checksum ON documents (knowledge_base_id, checksum) WHERE checksum IS NOT NULL AND deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_documents_metadata ON documents USING gin (metadata);

CREATE TABLE IF NOT EXISTS document_tasks (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id uuid NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    task_type varchar(24) NOT NULL CHECK (task_type IN ('ingest', 'reindex', 'delete')),
    status varchar(16) NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'running', 'retry_wait', 'completed', 'failed', 'dead')),
    progress smallint NOT NULL DEFAULT 0 CHECK (progress BETWEEN 0 AND 100),
    attempt_count smallint NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    max_attempts smallint NOT NULL DEFAULT 3 CHECK (max_attempts > 0),
    available_at timestamptz NOT NULL DEFAULT now(),
    heartbeat_at timestamptz,
    lease_expires_at timestamptz,
    worker_id varchar(128),
    timeout_seconds integer NOT NULL DEFAULT 1800 CHECK (timeout_seconds > 0),
    error_code varchar(64),
    error_message text,
    started_at timestamptz,
    completed_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_document_tasks_document_created ON document_tasks (document_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_document_tasks_ready ON document_tasks (available_at, created_at) WHERE status IN ('pending', 'retry_wait');
CREATE INDEX IF NOT EXISTS idx_document_tasks_lease ON document_tasks (lease_expires_at) WHERE status='running';
CREATE UNIQUE INDEX IF NOT EXISTS uq_document_tasks_active ON document_tasks (document_id) WHERE status IN ('pending', 'running', 'retry_wait');

CREATE TABLE IF NOT EXISTS document_chunks (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id uuid NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    chunk_no integer NOT NULL CHECK (chunk_no >= 0),
    content text NOT NULL,
    content_hash varchar(128) NOT NULL,
    location_label varchar(160),
    token_count integer CHECK (token_count IS NULL OR token_count >= 0),
    milvus_pk varchar(128),
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (document_id, chunk_no)
);
CREATE INDEX IF NOT EXISTS idx_chunks_document ON document_chunks (document_id, chunk_no);

CREATE TABLE IF NOT EXISTS faq (
    id bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    organization_id uuid NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001' REFERENCES organizations(id),
    question varchar(1000) NOT NULL,
    keywords text,
    answer text NOT NULL,
    doc_source text,
    category varchar(100),
    team varchar(100),
    system_name varchar(100),
    security_level varchar(20) NOT NULL DEFAULT 'team' CHECK (security_level IN ('public', 'team', 'confidential')),
    version varchar(50),
    is_active boolean NOT NULL DEFAULT true,
    last_updated timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (organization_id, question)
);
CREATE INDEX IF NOT EXISTS idx_faq_filters ON faq (organization_id, team, category) WHERE is_active = true;

CREATE TABLE IF NOT EXISTS chat_sessions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id uuid NOT NULL REFERENCES organizations(id),
    user_id uuid NOT NULL REFERENCES users(id),
    title varchar(200) NOT NULL DEFAULT '',
    knowledge_base_ids uuid[] NOT NULL DEFAULT '{}',
    last_message_at timestamptz NOT NULL DEFAULT now(),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    deleted_at timestamptz
);
CREATE INDEX IF NOT EXISTS idx_sessions_user_recent ON chat_sessions (user_id, last_message_at DESC) WHERE deleted_at IS NULL;

CREATE TABLE IF NOT EXISTS chat_messages (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id uuid NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
    parent_message_id uuid REFERENCES chat_messages(id),
    role varchar(16) NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
    content text NOT NULL DEFAULT '',
    mode varchar(24) NOT NULL DEFAULT 'tech' CHECK (mode IN ('tech', 'troubleshoot', 'summarize')),
    status varchar(16) NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'running', 'completed', 'failed', 'cancelled')),
    confidence varchar(8) CHECK (confidence IN ('low', 'medium', 'high')),
    citation_count integer NOT NULL DEFAULT 0 CHECK (citation_count >= 0),
    model varchar(128),
    usage jsonb NOT NULL DEFAULT '{}'::jsonb,
    error_code varchar(64),
    created_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz
);
CREATE INDEX IF NOT EXISTS idx_messages_session_created ON chat_messages (session_id, created_at);

CREATE TABLE IF NOT EXISTS message_citations (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    message_id uuid NOT NULL REFERENCES chat_messages(id) ON DELETE CASCADE,
    chunk_id uuid NOT NULL REFERENCES document_chunks(id),
    rank smallint NOT NULL CHECK (rank > 0),
    score numeric(6,5) NOT NULL CHECK (score BETWEEN 0 AND 1),
    excerpt text NOT NULL,
    claim text,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (message_id, rank)
);

CREATE TABLE IF NOT EXISTS message_favorites (
    user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    message_id uuid NOT NULL REFERENCES chat_messages(id) ON DELETE CASCADE,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, message_id)
);
CREATE INDEX IF NOT EXISTS idx_message_favorites_user_recent
    ON message_favorites (user_id, created_at DESC, message_id DESC);

CREATE TABLE IF NOT EXISTS support_tickets (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    ticket_no varchar(64) NOT NULL UNIQUE,
    session_id uuid REFERENCES chat_sessions(id),
    user_id uuid REFERENCES users(id),
    reason varchar(50) NOT NULL,
    priority varchar(20) NOT NULL DEFAULT 'normal' CHECK (priority IN ('low', 'normal', 'high', 'urgent')),
    status varchar(20) NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'processing', 'done', 'closed')),
    assignee varchar(128),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    resolved_at timestamptz
);

CREATE TABLE IF NOT EXISTS message_feedback (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    message_id uuid NOT NULL REFERENCES chat_messages(id) ON DELETE CASCADE,
    user_id uuid NOT NULL REFERENCES users(id),
    rating varchar(16) NOT NULL CHECK (rating IN ('up', 'down', 'handoff')),
    reason varchar(100),
    comment text,
    ticket_id uuid REFERENCES support_tickets(id),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (message_id, user_id)
);

CREATE TABLE IF NOT EXISTS user_preferences (
    user_id uuid PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    default_knowledge_base_ids uuid[] NOT NULL DEFAULT '{}',
    default_mode varchar(24) NOT NULL DEFAULT 'tech' CHECK (default_mode IN ('tech', 'troubleshoot', 'summarize')),
    locale varchar(16) NOT NULL DEFAULT 'zh-CN',
    timezone varchar(64) NOT NULL DEFAULT 'Asia/Shanghai',
    answer_style varchar(16) NOT NULL DEFAULT 'balanced' CHECK (answer_style IN ('concise', 'balanced', 'detailed')),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS retrieval_runs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    message_id uuid REFERENCES chat_messages(id) ON DELETE CASCADE,
    user_id uuid REFERENCES users(id),
    query text NOT NULL,
    rewritten_query text,
    top_k integer NOT NULL CHECK (top_k > 0),
    rerank_top_n integer NOT NULL CHECK (rerank_top_n > 0),
    retrieved_count integer NOT NULL DEFAULT 0 CHECK (retrieved_count >= 0),
    reranked_count integer NOT NULL DEFAULT 0 CHECK (reranked_count >= 0),
    latency_ms integer CHECK (latency_ms IS NULL OR latency_ms >= 0),
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS retrieval_results (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    retrieval_run_id uuid NOT NULL REFERENCES retrieval_runs(id) ON DELETE CASCADE,
    chunk_id uuid NOT NULL REFERENCES document_chunks(id),
    stage varchar(16) NOT NULL CHECK (stage IN ('retrieve', 'rerank')),
    rank integer NOT NULL CHECK (rank > 0),
    score numeric(8,7) NOT NULL,
    UNIQUE (retrieval_run_id, stage, rank)
);

CREATE TABLE IF NOT EXISTS audit_logs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id uuid NOT NULL REFERENCES organizations(id),
    user_id uuid REFERENCES users(id),
    action varchar(128) NOT NULL,
    resource_type varchar(64) NOT NULL,
    resource_id varchar(128),
    request_id varchar(128),
    ip_hash varchar(128),
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_audit_org_created ON audit_logs (organization_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_metadata ON audit_logs USING gin (metadata);

DROP TRIGGER IF EXISTS trg_organizations_updated_at ON organizations;
CREATE TRIGGER trg_organizations_updated_at BEFORE UPDATE ON organizations FOR EACH ROW EXECUTE FUNCTION set_updated_at();
DROP TRIGGER IF EXISTS trg_users_updated_at ON users;
CREATE TRIGGER trg_users_updated_at BEFORE UPDATE ON users FOR EACH ROW EXECUTE FUNCTION set_updated_at();
DROP TRIGGER IF EXISTS trg_knowledge_bases_updated_at ON knowledge_bases;
CREATE TRIGGER trg_knowledge_bases_updated_at BEFORE UPDATE ON knowledge_bases FOR EACH ROW EXECUTE FUNCTION set_updated_at();
DROP TRIGGER IF EXISTS trg_documents_updated_at ON documents;
CREATE TRIGGER trg_documents_updated_at BEFORE UPDATE ON documents FOR EACH ROW EXECUTE FUNCTION set_updated_at();
DROP TRIGGER IF EXISTS trg_faq_updated_at ON faq;
CREATE TRIGGER trg_faq_updated_at BEFORE UPDATE ON faq FOR EACH ROW EXECUTE FUNCTION set_updated_at();
DROP TRIGGER IF EXISTS trg_sessions_updated_at ON chat_sessions;
CREATE TRIGGER trg_sessions_updated_at BEFORE UPDATE ON chat_sessions FOR EACH ROW EXECUTE FUNCTION set_updated_at();
DROP TRIGGER IF EXISTS trg_tickets_updated_at ON support_tickets;
CREATE TRIGGER trg_tickets_updated_at BEFORE UPDATE ON support_tickets FOR EACH ROW EXECUTE FUNCTION set_updated_at();

COMMIT;

-- RLS 在应用层实现每请求租户上下文后启用。当前由 Repository 强制
-- organization_id 与 knowledge_base_members 过滤，避免未设置上下文时锁死 Worker。
