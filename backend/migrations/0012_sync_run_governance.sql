-- V5-7：同步批次、进度、统计与游标治理。
CREATE TABLE sync_runs (
    sync_run_id text PRIMARY KEY,
    data_source_id text NOT NULL REFERENCES data_sources(data_source_id) ON DELETE CASCADE,
    knowledge_base_id text NOT NULL REFERENCES knowledge_bases(knowledge_base_id) ON DELETE CASCADE,
    status text NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued','discovering','syncing','indexing','succeeded','partial_failed','aborted','failed')),
    stage text NOT NULL DEFAULT 'discover',
    cursor text,
    next_cursor text,
    added_count integer NOT NULL DEFAULT 0 CHECK (added_count >= 0),
    updated_count integer NOT NULL DEFAULT 0 CHECK (updated_count >= 0),
    deleted_count integer NOT NULL DEFAULT 0 CHECK (deleted_count >= 0),
    skipped_count integer NOT NULL DEFAULT 0 CHECK (skipped_count >= 0),
    failed_count integer NOT NULL DEFAULT 0 CHECK (failed_count >= 0),
    retry_count integer NOT NULL DEFAULT 0 CHECK (retry_count >= 0),
    error_code text,
    failure_reason text,
    started_at timestamptz,
    finished_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX sync_runs_source_created_idx
    ON sync_runs (data_source_id, created_at DESC);

CREATE UNIQUE INDEX sync_runs_one_active_source_idx
    ON sync_runs (data_source_id)
    WHERE status IN ('queued','discovering','syncing','indexing');

ALTER TABLE index_jobs
    ADD COLUMN sync_run_id text REFERENCES sync_runs(sync_run_id) ON DELETE SET NULL;

CREATE INDEX index_jobs_sync_run_idx ON index_jobs (sync_run_id);
