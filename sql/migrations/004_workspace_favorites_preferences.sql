BEGIN;

CREATE TABLE IF NOT EXISTS message_favorites (
    user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    message_id uuid NOT NULL REFERENCES chat_messages(id) ON DELETE CASCADE,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, message_id)
);
CREATE INDEX IF NOT EXISTS idx_message_favorites_user_recent
    ON message_favorites (user_id, created_at DESC, message_id DESC);

CREATE TABLE IF NOT EXISTS user_preferences (
    user_id uuid PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    default_knowledge_base_ids uuid[] NOT NULL DEFAULT '{}',
    default_mode varchar(24) NOT NULL DEFAULT 'tech'
        CHECK (default_mode IN ('tech', 'troubleshoot', 'summarize')),
    locale varchar(16) NOT NULL DEFAULT 'zh-CN',
    timezone varchar(64) NOT NULL DEFAULT 'Asia/Shanghai',
    answer_style varchar(16) NOT NULL DEFAULT 'balanced'
        CHECK (answer_style IN ('concise', 'balanced', 'detailed')),
    updated_at timestamptz NOT NULL DEFAULT now()
);

COMMIT;
