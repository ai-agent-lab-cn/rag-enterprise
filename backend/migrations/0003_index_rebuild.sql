-- 记录每个版本当前 chunks 由什么切分配置产出；空值代表迁移前的历史数据，
-- 首次重建会把它们一并纳入目标配置。
ALTER TABLE document_versions ADD COLUMN chunking_version text;

ALTER TABLE index_jobs
    ADD COLUMN job_type text NOT NULL DEFAULT 'index'
        CHECK (job_type IN ('index', 'rebuild')),
    ADD COLUMN rebuild_batch_id text,
    ADD COLUMN target_chunking_version text;

-- 重建任务必须携带目标配置，普通索引任务沿用入队时的配置即可。
ALTER TABLE index_jobs ADD CONSTRAINT index_jobs_rebuild_requires_batch
    CHECK (job_type = 'index' OR (rebuild_batch_id IS NOT NULL AND target_chunking_version IS NOT NULL));

CREATE INDEX index_jobs_rebuild_batch_idx
    ON index_jobs (rebuild_batch_id, status)
    WHERE rebuild_batch_id IS NOT NULL;

-- NOT EXISTS 只能优化常规路径；并发入队必须由数据库约束保证同一版本只有一个活动任务。
CREATE UNIQUE INDEX index_jobs_one_active_version_idx
    ON index_jobs (document_version_id)
    WHERE document_version_id IS NOT NULL AND status IN ('queued', 'running');
