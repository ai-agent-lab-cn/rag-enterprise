CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE users (
    user_id text PRIMARY KEY,
    username text NOT NULL,
    username_normalized text NOT NULL UNIQUE,
    display_name text NOT NULL,
    role text NOT NULL CHECK (role IN ('admin', 'member')),
    active boolean NOT NULL DEFAULT true,
    password_hash text NOT NULL,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL
);

CREATE TABLE knowledge_bases (
    knowledge_base_id text PRIMARY KEY,
    name text NOT NULL,
    name_normalized text NOT NULL UNIQUE,
    description text NOT NULL DEFAULT '',
    is_default boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL
);
CREATE UNIQUE INDEX knowledge_bases_one_default ON knowledge_bases (is_default) WHERE is_default;

CREATE TABLE knowledge_base_memberships (
    user_id text NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    knowledge_base_id text NOT NULL REFERENCES knowledge_bases(knowledge_base_id) ON DELETE CASCADE,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, knowledge_base_id)
);

CREATE TABLE sessions (
    session_id text PRIMARY KEY,
    user_id text NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    token_hash text NOT NULL UNIQUE,
    created_at timestamptz NOT NULL,
    expires_at timestamptz NOT NULL,
    revoked_at timestamptz
);

CREATE TABLE data_sources (
    data_source_id text PRIMARY KEY,
    knowledge_base_id text NOT NULL REFERENCES knowledge_bases(knowledge_base_id) ON DELETE RESTRICT,
    source_type text NOT NULL CHECK (source_type IN ('file', 'object_storage', 'web', 'connector')),
    name text NOT NULL,
    configuration jsonb NOT NULL DEFAULT '{}'::jsonb,
    enabled boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    UNIQUE (knowledge_base_id, name)
);

CREATE TABLE documents (
    document_id text NOT NULL,
    knowledge_base_id text NOT NULL REFERENCES knowledge_bases(knowledge_base_id) ON DELETE RESTRICT,
    data_source_id text NOT NULL REFERENCES data_sources(data_source_id) ON DELETE RESTRICT,
    filename text NOT NULL,
    current_version_id text,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    PRIMARY KEY (knowledge_base_id, document_id),
    UNIQUE (knowledge_base_id, data_source_id, filename)
);

CREATE TABLE document_versions (
    document_version_id text PRIMARY KEY,
    knowledge_base_id text NOT NULL,
    document_id text NOT NULL,
    version_number integer NOT NULL CHECK (version_number > 0),
    content_sha256 text NOT NULL CHECK (content_sha256 ~ '^[a-f0-9]{64}$'),
    source_file_bytes bigint NOT NULL CHECK (source_file_bytes >= 0),
    source_path text NOT NULL,
    status text NOT NULL CHECK (status IN ('pending', 'indexing', 'ready', 'failed', 'superseded')),
    failure_reason text,
    created_at timestamptz NOT NULL,
    indexed_at timestamptz,
    UNIQUE (document_version_id, knowledge_base_id, document_id),
    UNIQUE (knowledge_base_id, document_id, version_number),
    UNIQUE (knowledge_base_id, document_id, content_sha256),
    FOREIGN KEY (knowledge_base_id, document_id)
        REFERENCES documents(knowledge_base_id, document_id) ON DELETE RESTRICT
);
ALTER TABLE documents ADD CONSTRAINT documents_current_version_fk
    FOREIGN KEY (current_version_id, knowledge_base_id, document_id)
    REFERENCES document_versions(document_version_id, knowledge_base_id, document_id) ON DELETE RESTRICT;

CREATE TABLE chunks (
    chunk_id text PRIMARY KEY,
    document_version_id text NOT NULL REFERENCES document_versions(document_version_id) ON DELETE CASCADE,
    knowledge_base_id text NOT NULL REFERENCES knowledge_bases(knowledge_base_id) ON DELETE RESTRICT,
    chunk_index integer NOT NULL CHECK (chunk_index >= 0),
    content text NOT NULL,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    embedding vector NOT NULL,
    created_at timestamptz NOT NULL,
    UNIQUE (document_version_id, chunk_index)
);
CREATE INDEX chunks_knowledge_base_id_idx ON chunks (knowledge_base_id);

CREATE TABLE index_jobs (
    index_job_id text PRIMARY KEY,
    knowledge_base_id text NOT NULL REFERENCES knowledge_bases(knowledge_base_id) ON DELETE RESTRICT,
    data_source_id text REFERENCES data_sources(data_source_id) ON DELETE RESTRICT,
    document_version_id text REFERENCES document_versions(document_version_id) ON DELETE RESTRICT,
    idempotency_key text NOT NULL UNIQUE,
    status text NOT NULL CHECK (status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')),
    attempt_count integer NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    max_attempts integer NOT NULL DEFAULT 3 CHECK (max_attempts > 0),
    available_at timestamptz NOT NULL DEFAULT now(),
    locked_at timestamptz,
    locked_by text,
    failure_reason text,
    created_at timestamptz NOT NULL DEFAULT now(),
    started_at timestamptz,
    finished_at timestamptz,
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX index_jobs_claim_idx ON index_jobs (status, available_at, created_at);

CREATE TABLE legacy_migration_runs (
    migration_run_id text PRIMARY KEY,
    source_fingerprint text NOT NULL UNIQUE CHECK (source_fingerprint ~ '^[a-f0-9]{64}$'),
    source_manifest jsonb NOT NULL,
    imported_counts jsonb NOT NULL,
    status text NOT NULL CHECK (status IN ('completed')),
    completed_at timestamptz NOT NULL DEFAULT now()
);
