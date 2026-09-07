-- 阶段 1：一个索引版本允许多次构建尝试。
--
-- 此前 ensure_index_build 查到同 index_version_id 的既有行就直接返回，一个版本
-- 永远只有一次 Build。于是「重试构建」没有落点：失败的那次要么被原地覆盖、丢掉失败
-- 现场，要么根本无法重来。而生产链里 build_failed 明确有一条「重新构建」的恢复路径，
-- 它需要的正是第二次尝试。
--
-- 存量数据一律记为第 1 次尝试。
ALTER TABLE index_builds ADD COLUMN attempt_no integer NOT NULL DEFAULT 1
    CHECK (attempt_no > 0);
ALTER TABLE index_builds ADD CONSTRAINT index_builds_attempt_unique
    UNIQUE (index_version_id, attempt_no);

-- 同一版本同时只允许一次进行中的构建。放在数据库而不是应用层：重试入口可能来自
-- API、CLI 与 Worker 三处，并发调用要拿到 UniqueViolation 而不是两个并行的 attempt
-- 互相覆盖 document_index_states。约束方式与 0003 的 index_jobs_one_active_version_idx
-- 同源。
CREATE UNIQUE INDEX index_builds_one_active_idx
    ON index_builds (index_version_id)
    WHERE status NOT IN ('succeeded', 'partial_failed', 'failed', 'cancelled');
