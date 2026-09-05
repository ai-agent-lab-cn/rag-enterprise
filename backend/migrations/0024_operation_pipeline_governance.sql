-- V5：统一任务进度、索引构建、单资源同步与文件处理治理。
CREATE TABLE operations (
    operation_id text PRIMARY KEY,
    operation_type text NOT NULL CHECK (operation_type IN ('index_build','sync_run','file_update','document_reprocess','index_validation','index_activation')),
    knowledge_base_id text NOT NULL REFERENCES knowledge_bases(knowledge_base_id) ON DELETE CASCADE,
    data_source_id text REFERENCES data_sources(data_source_id) ON DELETE SET NULL,
    document_id text,
    document_version_id text REFERENCES document_versions(document_version_id) ON DELETE SET NULL,
    status text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','preparing','running','validating','ready','activating','succeeded','partial_failed','failed','cancel_requested','cancelled','aborted')),
    current_stage text NOT NULL DEFAULT 'queued',
    progress_mode text NOT NULL DEFAULT 'indeterminate' CHECK (progress_mode IN ('indeterminate','bytes','resources','documents','stages')),
    progress_percent numeric(5,2),
    total_count integer NOT NULL DEFAULT 0 CHECK (total_count >= 0),
    completed_count integer NOT NULL DEFAULT 0 CHECK (completed_count >= 0),
    processing_count integer NOT NULL DEFAULT 0 CHECK (processing_count >= 0),
    failed_count integer NOT NULL DEFAULT 0 CHECK (failed_count >= 0),
    error_code text, error_message text,
    idempotency_key text NOT NULL UNIQUE,
    started_at timestamptz, finished_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX operations_scope_created_idx ON operations (knowledge_base_id, created_at DESC);
CREATE INDEX operations_active_idx ON operations (status) WHERE status IN ('queued','preparing','running','validating','ready','activating','cancel_requested');

CREATE TABLE index_definitions (
    index_definition_id text PRIMARY KEY,
    knowledge_base_id text NOT NULL REFERENCES knowledge_bases(knowledge_base_id) ON DELETE CASCADE,
    name text NOT NULL, vector_config jsonb NOT NULL DEFAULT '{}'::jsonb,
    keyword_config jsonb NOT NULL DEFAULT '{}'::jsonb,
    metadata_schema jsonb NOT NULL DEFAULT '{}'::jsonb,
    parser_schema_version text NOT NULL, chunking_policy jsonb NOT NULL DEFAULT '{}'::jsonb,
    embedding_model text NOT NULL, embedding_dimension integer NOT NULL CHECK (embedding_dimension > 0),
    reranker_config jsonb NOT NULL DEFAULT '{}'::jsonb, config_fingerprint text NOT NULL,
    active boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (knowledge_base_id, name)
);

CREATE TABLE index_builds (
    index_build_id text PRIMARY KEY,
    operation_id text NOT NULL UNIQUE REFERENCES operations(operation_id) ON DELETE CASCADE,
    index_version_id text NOT NULL REFERENCES index_versions(index_version_id) ON DELETE CASCADE,
    index_definition_id text REFERENCES index_definitions(index_definition_id) ON DELETE SET NULL,
    build_type text NOT NULL CHECK (build_type IN ('initial','full_rebuild','incremental','repair')),
    status text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','preparing','building','validating','ready','activating','succeeded','partial_failed','failed','cancel_requested','cancelled')),
    total_documents integer NOT NULL DEFAULT 0, queued_documents integer NOT NULL DEFAULT 0,
    processing_documents integer NOT NULL DEFAULT 0, succeeded_documents integer NOT NULL DEFAULT 0,
    failed_documents integer NOT NULL DEFAULT 0, failure_code text, failure_reason text,
    started_at timestamptz, finished_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now()
);

-- 为升级前已有的 Active Index 补齐 Definition；配置采用版本冻结值，不重建分块。
INSERT INTO index_definitions
    (index_definition_id, knowledge_base_id, name, vector_config, keyword_config,
     metadata_schema, parser_schema_version, chunking_policy, embedding_model,
     embedding_dimension, reranker_config, config_fingerprint)
SELECT 'idef_migration_' || substr(md5(iv.index_version_id), 1, 12), iv.knowledge_base_id,
       'definition-' || substr(iv.config_fingerprint, 1, 12),
       '{"engine":"pgvector"}'::jsonb, '{"engine":"pg_trgm"}'::jsonb,
       '{"category":true,"acl":true}'::jsonb, iv.parser_version,
       iv.processing_options, iv.embedding_model, iv.embedding_dimension,
       '{}'::jsonb, iv.config_fingerprint
FROM index_versions iv
ON CONFLICT (knowledge_base_id, name) DO NOTHING;

CREATE TABLE document_index_states (
    index_build_id text NOT NULL REFERENCES index_builds(index_build_id) ON DELETE CASCADE,
    index_version_id text NOT NULL REFERENCES index_versions(index_version_id) ON DELETE CASCADE,
    document_id text NOT NULL,
    document_version_id text NOT NULL REFERENCES document_versions(document_version_id) ON DELETE CASCADE,
    vector_status text NOT NULL DEFAULT 'pending' CHECK (vector_status IN ('pending','building','ready','failed')),
    keyword_status text NOT NULL DEFAULT 'pending' CHECK (keyword_status IN ('pending','building','ready','failed')),
    metadata_status text NOT NULL DEFAULT 'pending' CHECK (metadata_status IN ('pending','building','ready','failed')),
    overall_status text NOT NULL DEFAULT 'pending' CHECK (overall_status IN ('pending','building','validating','ready','failed','cancelled')),
    chunk_count integer NOT NULL DEFAULT 0, failure_stage text, failure_code text, failure_reason text,
    updated_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY (index_build_id, document_id)
);

CREATE TABLE sync_resource_runs (
    sync_resource_run_id text PRIMARY KEY,
    sync_run_id text NOT NULL REFERENCES sync_runs(sync_run_id) ON DELETE CASCADE,
    external_resource_id text NOT NULL,
    operation text NOT NULL CHECK (operation IN ('add','update','delete','acl_update','metadata_update','unchanged','skip','retry')),
    status text NOT NULL DEFAULT 'discovered' CHECK (status IN ('discovered','fetching','normalizing','parsing','chunking','enriching','building','validating','activated','succeeded','unchanged','skipped','deleted','failed','dead_letter','cancelled')),
    current_stage text NOT NULL DEFAULT 'discover', document_id text,
    document_version_id text REFERENCES document_versions(document_version_id) ON DELETE SET NULL,
    index_build_id text REFERENCES index_builds(index_build_id) ON DELETE SET NULL,
    attempt_count integer NOT NULL DEFAULT 0, max_attempts integer NOT NULL DEFAULT 3,
    error_code text, error_message text, started_at timestamptz, finished_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (sync_run_id, external_resource_id)
);
CREATE INDEX sync_resource_runs_status_idx ON sync_resource_runs (sync_run_id, status);

CREATE TABLE document_processing_runs (
    processing_run_id text PRIMARY KEY,
    operation_id text NOT NULL UNIQUE REFERENCES operations(operation_id) ON DELETE CASCADE,
    document_id text NOT NULL,
    document_version_id text NOT NULL REFERENCES document_versions(document_version_id) ON DELETE CASCADE,
    processing_type text NOT NULL CHECK (processing_type IN ('file_update','reparse','reindex','restore')),
    status text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','uploading','parsing','chunking','classifying','building','validating','ready','activating','succeeded','ready_with_warning','failed','cancelled')),
    uploaded_bytes bigint NOT NULL DEFAULT 0, total_bytes bigint NOT NULL DEFAULT 0,
    failure_stage text, failure_code text, failure_reason text,
    created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE sync_runs ADD COLUMN operation_id text REFERENCES operations(operation_id) ON DELETE SET NULL;
ALTER TABLE sync_runs ADD COLUMN input_cursor text;
ALTER TABLE sync_runs ADD COLUMN discovered_cursor text;
ALTER TABLE sync_runs ADD COLUMN committed_cursor text;
ALTER TABLE sync_runs ADD COLUMN total_count integer NOT NULL DEFAULT 0;
ALTER TABLE sync_runs ADD COLUMN completed_count integer NOT NULL DEFAULT 0;
ALTER TABLE sync_runs ADD COLUMN processing_count integer NOT NULL DEFAULT 0;
ALTER TABLE sync_runs ADD COLUMN dead_letter_count integer NOT NULL DEFAULT 0;
ALTER TABLE sync_runs ADD COLUMN index_build_id text REFERENCES index_builds(index_build_id) ON DELETE SET NULL;
ALTER TABLE index_jobs ADD COLUMN operation_id text REFERENCES operations(operation_id) ON DELETE SET NULL;
ALTER TABLE data_sources ADD COLUMN sync_enabled boolean NOT NULL DEFAULT true;
ALTER TABLE data_sources ADD COLUMN retrieval_enabled boolean NOT NULL DEFAULT true;
UPDATE data_sources SET sync_enabled=enabled;
ALTER TABLE data_sources DROP CONSTRAINT data_sources_last_sync_status_check;
ALTER TABLE data_sources ADD CONSTRAINT data_sources_last_sync_status_check
    CHECK (last_sync_status IN ('idle','queued','running','succeeded','failed','aborted'));
UPDATE sync_runs SET stage='legacy_complete' WHERE status='succeeded' AND operation_id IS NULL AND stage='complete';
