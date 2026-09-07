-- 阶段 4 收尾：删掉声明了却从来没有产生者的枚举值。
--
-- 这类值不会出错，只会让人以为系统支持这些状态：读 schema 的人会据此设计前端、写监控
-- 告警、给运维写手册，而它们一次都不会出现。本轮已经因此栽过四次——前端为同步流水线画
-- 了五个后端从不写的格子、operations 的两个 operation_type 从未被创建、index_definitions
-- 整张表是空壳、index_lifecycle_events 声明 11 种事件只接了 4 种。
--
-- 判据是「有没有自然的产生者」，不是「将来会不会用上」：
--   · index_builds.build_type 只有 full_rebuild——本仓不做增量构建
--   · operations 的 index_validation / index_activation：验证与激活都是单事务动作，
--     没有进度可跟踪，不需要 operation 行
--   · cancel_requested：取消是一步写 cancelled，没有两阶段
--   · uploading / classifying / enriching 等：真实阶段现在由 mark_stage 记录，
--     用的是 parsing / chunking / vector / keyword / metadata / validating
--   · snapshot_completeness 的 partial：需要「同步部分成功 → 部分快照」这条编排，
--     而那条编排在规格评审时被判定没有触发条件，未实现
--
-- 收窄 CHECK 是安全的：这些值没有任何代码写过，因此不会有存量行违反新约束。
-- 万一有，ALTER 会直接失败——那是明确的失败，不是静默损坏。
--
-- `document_snapshot_members.inclusion_status` 的 excluded 不在此列：它有真实语义
-- （看到了但这次建不了的资料），下面把它接上，而不是删掉。

ALTER TABLE document_processing_runs DROP CONSTRAINT document_processing_runs_processing_type_check;
ALTER TABLE document_processing_runs ADD CONSTRAINT document_processing_runs_processing_type_check
    CHECK (processing_type IN ('file_upload', 'file_update', 'reparse', 'restore'));
ALTER TABLE document_processing_runs DROP CONSTRAINT document_processing_runs_status_check;
ALTER TABLE document_processing_runs ADD CONSTRAINT document_processing_runs_status_check
    CHECK (status IN ('queued', 'parsing', 'chunking', 'building', 'validating', 'ready', 'succeeded', 'failed', 'cancelled'));
ALTER TABLE document_snapshots DROP CONSTRAINT document_snapshots_snapshot_completeness_check;
ALTER TABLE document_snapshots ADD CONSTRAINT document_snapshots_snapshot_completeness_check
    CHECK (snapshot_completeness IN ('complete', 'unknown'));
ALTER TABLE index_builds DROP CONSTRAINT index_builds_build_type_check;
ALTER TABLE index_builds ADD CONSTRAINT index_builds_build_type_check
    CHECK (build_type IN ('full_rebuild'));
ALTER TABLE index_builds DROP CONSTRAINT index_builds_status_check;
ALTER TABLE index_builds ADD CONSTRAINT index_builds_status_check
    CHECK (status IN ('queued', 'building', 'validating', 'ready', 'succeeded', 'partial_failed', 'failed', 'cancelled'));
ALTER TABLE operations DROP CONSTRAINT operations_operation_type_check;
ALTER TABLE operations ADD CONSTRAINT operations_operation_type_check
    CHECK (operation_type IN ('index_build', 'sync_run', 'file_upload', 'file_update', 'document_reprocess'));
ALTER TABLE operations DROP CONSTRAINT operations_progress_mode_check;
ALTER TABLE operations ADD CONSTRAINT operations_progress_mode_check
    CHECK (progress_mode IN ('resources', 'documents', 'stages'));
ALTER TABLE operations DROP CONSTRAINT operations_status_check;
ALTER TABLE operations ADD CONSTRAINT operations_status_check
    CHECK (status IN ('queued', 'running', 'validating', 'ready', 'succeeded', 'partial_failed', 'failed', 'cancelled', 'aborted'));
ALTER TABLE sync_resource_runs DROP CONSTRAINT sync_resource_runs_operation_check;
ALTER TABLE sync_resource_runs ADD CONSTRAINT sync_resource_runs_operation_check
    CHECK (operation IN ('add', 'update', 'delete', 'unchanged', 'skip', 'retry'));
ALTER TABLE sync_resource_runs DROP CONSTRAINT sync_resource_runs_status_check;
ALTER TABLE sync_resource_runs ADD CONSTRAINT sync_resource_runs_status_check
    CHECK (status IN ('discovered', 'fetching', 'normalizing', 'parsing', 'chunking', 'building', 'validating', 'activated', 'succeeded', 'unchanged', 'skipped', 'deleted', 'failed', 'dead_letter', 'cancelled'));

-- excluded 成员此前只有计数、没有名字：document_version_id 是 NOT NULL，而被排除的资料
-- 恰恰是「没有当前版本」的那批，填不进去。放宽这一列，让排除项也能逐条留名——
-- 否则「这份资料为什么没进这次索引」在任何地方都查不到，只有一个 excluded_count 数字。
ALTER TABLE document_snapshot_members ALTER COLUMN document_version_id DROP NOT NULL;
ALTER TABLE document_snapshot_members ADD CONSTRAINT document_snapshot_members_version_presence
    CHECK ((inclusion_status = 'included') = (document_version_id IS NOT NULL));
