-- 索引版本创建入口、完整配置快照与状态收口。

-- 先修复 V36 聚合逻辑留下的矛盾：文档全部成功、Version 已进入后续生命周期的 Build
-- 不是失败。该更新只命中有完整计数证据的行，不猜测真正失败的历史任务。
UPDATE index_builds ib
SET status='succeeded', updated_at=now()
FROM index_versions iv
WHERE iv.index_version_id=ib.index_version_id
  AND ib.status IN ('ready','validating','failed')
  AND ib.total_documents > 0
  AND ib.succeeded_documents=ib.total_documents
  AND ib.failed_documents=0
  AND iv.status IN ('validating','validation_failed','ready','active','previous','retired');

-- V36 允许把 Validate 阶段写进 Build.status。只要 Build 已经到达 validating / ready，
-- 构建动作本身就已经结束；计数缺失属于旧观测字段不完整，不能让状态约束迁移因此失败。
UPDATE index_builds
SET status='succeeded', finished_at=COALESCE(finished_at, now()), updated_at=now()
WHERE status IN ('validating','ready');

UPDATE operations o
SET status='succeeded', current_stage='complete', progress_percent=100,
    error_code=NULL, error_message=NULL,
    finished_at=COALESCE(o.finished_at, now()), updated_at=now()
FROM index_builds ib
WHERE ib.operation_id=o.operation_id AND ib.status='succeeded'
  AND o.operation_type='index_build';

-- Operation 只表达任务是否仍在运行；validating/ready 是 Version 或 stage 语义。
UPDATE operations SET status='running' WHERE status='validating';
UPDATE operations
SET status='succeeded', finished_at=COALESCE(finished_at, now())
WHERE status='ready';
DROP INDEX IF EXISTS operations_active_idx;
ALTER TABLE operations DROP CONSTRAINT operations_status_check;
ALTER TABLE operations ADD CONSTRAINT operations_status_check
    CHECK (status IN (
        'queued','running','succeeded','partial_failed','failed','cancelled','aborted'
    ));
CREATE INDEX operations_active_idx ON operations (status)
    WHERE status IN ('queued','running');

-- Build 在构建结束时即终止；validating / ready / activating 属于 Version 生命周期。
ALTER TABLE index_builds DROP CONSTRAINT index_builds_status_check;
ALTER TABLE index_builds ADD CONSTRAINT index_builds_status_check
    CHECK (status IN ('queued','building','succeeded','partial_failed','failed','cancelled'));

-- 同一 Version 的新 Build attempt 是独立生命周期事实，不能再伪装成第二次 created。
ALTER TABLE index_lifecycle_events DROP CONSTRAINT index_lifecycle_events_event_type_check;
ALTER TABLE index_lifecycle_events ADD CONSTRAINT index_lifecycle_events_event_type_check
    CHECK (event_type IN (
        'created', 'build_retried', 'build_succeeded', 'build_failed',
        'validation_passed', 'validation_failed',
        'activated', 'deactivated', 'rolled_back', 'retired', 'cleaned'
    ));

-- 可读版本号只在知识库内递增，ID 仍用于全局引用。
ALTER TABLE index_versions ADD COLUMN version_no integer;
WITH numbered AS (
    SELECT index_version_id,
           row_number() OVER (
               PARTITION BY knowledge_base_id ORDER BY created_at, index_version_id
           ) AS value
    FROM index_versions
)
UPDATE index_versions iv SET version_no=numbered.value
FROM numbered WHERE numbered.index_version_id=iv.index_version_id;
ALTER TABLE index_versions ADD CONSTRAINT index_versions_scope_version_no_unique
    UNIQUE (knowledge_base_id, version_no);

ALTER TABLE index_versions ADD COLUMN creation_reason text NOT NULL DEFAULT 'legacy';
ALTER TABLE index_versions ADD CONSTRAINT index_versions_creation_reason_check
    CHECK (creation_reason IN (
        'legacy','initial_build','config_changed','document_snapshot_changed',
        'component_upgraded','consistency_repair','manual_rebuild'
    ));
ALTER TABLE index_versions ADD COLUMN force_reason text;
ALTER TABLE index_versions ADD CONSTRAINT index_versions_force_reason_check
    CHECK (
        creation_reason NOT IN ('manual_rebuild','consistency_repair')
        OR length(btrim(force_reason)) > 0
    );
ALTER TABLE index_versions ADD COLUMN requested_by text;
ALTER TABLE index_versions ADD COLUMN creation_idempotency_key text;
CREATE UNIQUE INDEX index_versions_creation_idempotency_idx
    ON index_versions (knowledge_base_id, creation_idempotency_key)
    WHERE creation_idempotency_key IS NOT NULL;

-- config_snapshot / component_manifest 是不可变发布事实；存量版本没有足够证据可回填，
-- 用 unknown 明确表达，而不是拼一份看似完整的假配置。
ALTER TABLE index_versions ADD COLUMN config_snapshot jsonb NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE index_versions ADD COLUMN component_manifest jsonb NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE index_versions ADD COLUMN release_fingerprint text;
ALTER TABLE index_versions ADD CONSTRAINT index_versions_release_fingerprint_check
    CHECK (release_fingerprint IS NULL OR release_fingerprint ~ '^[a-f0-9]{64}$');
ALTER TABLE index_versions ADD COLUMN config_completeness text NOT NULL DEFAULT 'unknown';
ALTER TABLE index_versions ADD CONSTRAINT index_versions_config_completeness_check
    CHECK (config_completeness IN ('complete','unknown'));
ALTER TABLE index_versions ADD COLUMN legacy_migrated boolean NOT NULL DEFAULT false;
UPDATE index_versions
SET legacy_migrated=true, config_completeness='unknown'
WHERE creation_reason='legacy';

-- active 的运行时放行仍由 switch_to_version 对持久化 Validation Report 做强校验。
-- 这里暂不把旧 CHECK 扩成 validation_report_id：bootstrap 首版需要在同一事务内先建
-- Version、再建引用它的报告，普通 CHECK 不是可延迟约束，会把这条合法事务拦在中间状态。
