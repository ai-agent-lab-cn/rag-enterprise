-- 本地目录作为数据源。不复用 'file'：那个取值的语义已被"API 上传"占用，
-- 两者同步行为相反（上传是推、目录是拉）。
ALTER TABLE data_sources DROP CONSTRAINT data_sources_source_type_check;
ALTER TABLE data_sources ADD CONSTRAINT data_sources_source_type_check
    CHECK (source_type IN ('file', 'local_directory', 'object_storage', 'web', 'connector'));

-- 同步状态改为真实列。派生字段（index_jobs 的 finished_at/status）表达不了
-- "同步成功但没有任何变化"——那种情况不产生 index job，会显示为 idle，
-- 与"从未同步"无法区分。aborted 专门表示熔断中止。
ALTER TABLE data_sources
    ADD COLUMN last_sync_at timestamptz,
    ADD COLUMN last_sync_status text NOT NULL DEFAULT 'idle'
        CHECK (last_sync_status IN ('idle', 'running', 'succeeded', 'failed', 'aborted')),
    ADD COLUMN sync_failure_reason text;

-- 比对的基础：上次同步时看到的每个对象。
CREATE TABLE data_source_objects (
    data_source_id text NOT NULL REFERENCES data_sources(data_source_id) ON DELETE CASCADE,
    object_key text NOT NULL,
    version text NOT NULL,
    -- 首次发现时索引尚未完成，此时还没有文档记录，因此可空。
    -- 知识库归属不在这里重复记录：data_sources 已有 knowledge_base_id。
    document_id text,
    synced_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (data_source_id, object_key)
);

ALTER TABLE index_jobs DROP CONSTRAINT index_jobs_job_type_check;
ALTER TABLE index_jobs ADD CONSTRAINT index_jobs_job_type_check
    CHECK (job_type IN ('index', 'rebuild', 'sync'));

-- 0003 的原约束是 `job_type = 'index' OR (rebuild 字段非空)`，新增的 sync 会落进
-- 后半句而被要求提供 rebuild 字段。改成只约束 rebuild 自己。
ALTER TABLE index_jobs DROP CONSTRAINT index_jobs_rebuild_requires_batch;
ALTER TABLE index_jobs ADD CONSTRAINT index_jobs_rebuild_requires_batch
    CHECK (job_type <> 'rebuild'
           OR (rebuild_batch_id IS NOT NULL AND target_chunking_version IS NOT NULL));

-- 两个 sync 任务并发跑同一数据源会重复入队索引任务，并互相覆盖 data_source_objects。
-- 放数据库而不是应用层：CLI 可能被并发调用，0003 也用同样手法处理版本级并发。
CREATE UNIQUE INDEX index_jobs_one_active_sync_idx
    ON index_jobs (data_source_id)
    WHERE job_type = 'sync' AND status IN ('queued', 'running');
