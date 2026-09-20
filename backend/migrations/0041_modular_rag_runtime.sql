-- Modular RAG 运行事实、受控策略和会话关系化。
-- 旧 data/conversations/records.json 不在 schema 迁移期间自动读取：数据导入必须通过
-- scripts.migrate_conversations_to_postgres 显式执行、核对并保留原文件。

ALTER TABLE evaluation_runs DROP CONSTRAINT evaluation_runs_evaluation_type_check;
ALTER TABLE evaluation_runs ADD CONSTRAINT evaluation_runs_evaluation_type_check
    CHECK (evaluation_type IN (
        'retrieval','answer','pipeline','security','acceptance','intent_routing'
    ));

CREATE TABLE conversations (
    conversation_id text PRIMARY KEY CHECK (conversation_id ~ '^conv_[a-f0-9]{16}$'),
    knowledge_base_id text NOT NULL REFERENCES knowledge_bases(knowledge_base_id) ON DELETE CASCADE,
    owner_id text NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    title text NOT NULL,
    legacy_content_sha256 text
        CHECK (legacy_content_sha256 IS NULL OR legacy_content_sha256 ~ '^[a-f0-9]{64}$'),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX conversations_owner_updated_idx
    ON conversations (knowledge_base_id, owner_id, updated_at DESC);

CREATE TABLE query_executions (
    execution_id text PRIMARY KEY CHECK (execution_id ~ '^qex_[a-f0-9]{20}$'),
    conversation_id text NOT NULL REFERENCES conversations(conversation_id) ON DELETE CASCADE,
    knowledge_base_id text NOT NULL REFERENCES knowledge_bases(knowledge_base_id) ON DELETE CASCADE,
    owner_id text NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    intent text CHECK (intent IN ('fact_lookup','summarize','compare','procedure')),
    intent_confidence double precision NOT NULL DEFAULT 0 CHECK (intent_confidence BETWEEN 0 AND 1),
    routing_reason text,
    original_question text NOT NULL DEFAULT '',
    effective_question text NOT NULL DEFAULT '',
    follow_up_rewritten boolean NOT NULL DEFAULT false,
    requires_web boolean NOT NULL DEFAULT false,
    classifier_model text,
    control_outcome text NOT NULL DEFAULT 'route'
        CHECK (control_outcome IN ('route','clarify','out_of_scope')),
    pipeline_profile text,
    profile_version text,
    policy_snapshot jsonb NOT NULL DEFAULT '{}'::jsonb,
    active_index_version_id text REFERENCES index_versions(index_version_id) ON DELETE SET NULL,
    status text NOT NULL CHECK (status IN ('succeeded','failed')),
    fallback_used boolean NOT NULL DEFAULT false,
    total_latency_ms double precision NOT NULL DEFAULT 0 CHECK (total_latency_ms >= 0),
    error_code text,
    error_message text,
    started_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz NOT NULL DEFAULT now(),
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX query_executions_kb_created_idx
    ON query_executions (knowledge_base_id, created_at DESC);
CREATE INDEX query_executions_profile_created_idx
    ON query_executions (pipeline_profile, created_at DESC);

CREATE TABLE answer_records (
    record_id text PRIMARY KEY CHECK (record_id ~ '^answer_[a-f0-9]{16}$'),
    conversation_id text NOT NULL REFERENCES conversations(conversation_id) ON DELETE CASCADE,
    knowledge_base_id text NOT NULL REFERENCES knowledge_bases(knowledge_base_id) ON DELETE CASCADE,
    execution_id text UNIQUE REFERENCES query_executions(execution_id) ON DELETE SET NULL,
    question text NOT NULL,
    status text NOT NULL CHECK (status IN ('success','failed')),
    answer text,
    sources jsonb NOT NULL DEFAULT '[]'::jsonb,
    latency_ms jsonb NOT NULL DEFAULT '{}'::jsonb,
    models jsonb NOT NULL DEFAULT '{}'::jsonb,
    model_metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    prompt_version text,
    prompt_hash text,
    answer_status text,
    generation_governance jsonb,
    query_metadata jsonb,
    routing jsonb,
    pipeline_profile text,
    profile_version text,
    module_summary jsonb NOT NULL DEFAULT '[]'::jsonb,
    legacy_content_sha256 text
        CHECK (legacy_content_sha256 IS NULL OR legacy_content_sha256 ~ '^[a-f0-9]{64}$'),
    bad_case_category text,
    error_code text,
    error_message text,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX answer_records_conversation_created_idx
    ON answer_records (conversation_id, created_at);
CREATE INDEX answer_records_failed_idx
    ON answer_records (knowledge_base_id, created_at DESC) WHERE status='failed';

CREATE TABLE module_executions (
    module_execution_id text PRIMARY KEY CHECK (module_execution_id ~ '^mex_[a-f0-9]{20}$'),
    execution_id text NOT NULL REFERENCES query_executions(execution_id) ON DELETE CASCADE,
    sequence integer NOT NULL CHECK (sequence > 0),
    module_key text NOT NULL,
    module_version text NOT NULL,
    status text NOT NULL CHECK (status IN ('succeeded','failed','skipped','degraded')),
    attempt integer NOT NULL DEFAULT 1 CHECK (attempt > 0),
    input_hash text NOT NULL CHECK (input_hash ~ '^[a-f0-9]{64}$'),
    output_hash text CHECK (output_hash IS NULL OR output_hash ~ '^[a-f0-9]{64}$'),
    metrics jsonb NOT NULL DEFAULT '{}'::jsonb,
    error_code text,
    error_message text,
    fallback_reason text,
    duration_ms double precision NOT NULL DEFAULT 0 CHECK (duration_ms >= 0),
    started_at timestamptz NOT NULL,
    finished_at timestamptz NOT NULL,
    UNIQUE (execution_id, sequence)
);

CREATE TABLE query_evidence (
    execution_id text NOT NULL REFERENCES query_executions(execution_id) ON DELETE CASCADE,
    evidence_id text NOT NULL,
    source_type text NOT NULL CHECK (source_type IN ('knowledge_base','web')),
    chunk_id text,
    source_url text,
    title text NOT NULL,
    locator jsonb NOT NULL DEFAULT '{}'::jsonb,
    content_excerpt text NOT NULL DEFAULT '',
    content_sha256 text CHECK (content_sha256 IS NULL OR content_sha256 ~ '^[a-f0-9]{64}$'),
    retrieval_score double precision NOT NULL DEFAULT 0,
    rerank_score double precision NOT NULL DEFAULT 0,
    selected boolean NOT NULL DEFAULT true,
    citation_index integer CHECK (citation_index IS NULL OR citation_index > 0),
    retrieved_at timestamptz,
    PRIMARY KEY (execution_id, evidence_id)
);

CREATE TABLE knowledge_base_rag_policies (
    knowledge_base_id text PRIMARY KEY REFERENCES knowledge_bases(knowledge_base_id) ON DELETE CASCADE,
    rollout_stage text NOT NULL DEFAULT 'shadow'
        CHECK (rollout_stage IN ('shadow','canary','full')),
    web_search_enabled boolean NOT NULL DEFAULT false,
    allowed_domains text[] NOT NULL DEFAULT '{}',
    intent_confidence_threshold double precision NOT NULL DEFAULT 0.8
        CHECK (intent_confidence_threshold BETWEEN 0.5 AND 1),
    minimum_evidence_count integer NOT NULL DEFAULT 1 CHECK (minimum_evidence_count BETWEEN 1 AND 10),
    max_web_results integer NOT NULL DEFAULT 5 CHECK (max_web_results BETWEEN 1 AND 5),
    profile_versions jsonb NOT NULL DEFAULT '{
      "fact_lookup":"1",
      "summarize":"1",
      "compare":"1",
      "procedure":"1"
    }'::jsonb,
    updated_by text REFERENCES users(user_id) ON DELETE SET NULL,
    stage_started_at timestamptz NOT NULL DEFAULT now(),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE conversation_migration_runs (
    source_sha256 text PRIMARY KEY CHECK (source_sha256 ~ '^[a-f0-9]{64}$'),
    conversation_count integer NOT NULL CHECK (conversation_count >= 0),
    answer_count integer NOT NULL CHECK (answer_count >= 0),
    imported_at timestamptz NOT NULL DEFAULT now()
);

INSERT INTO knowledge_base_rag_policies (knowledge_base_id)
SELECT knowledge_base_id FROM knowledge_bases
ON CONFLICT (knowledge_base_id) DO NOTHING;
