-- 阶段 1：把发布门禁从调用方手里搬进数据库。
--
-- 此前 switch_to_version 检查的是**调用方传进来的内存对象** RetrievalEvaluationReport：
-- 报告没有落库，谁调用谁提供，库里查不到任何一次验证发生过。指纹比对能挡住「用 A 配置
-- 的报告放行 B 配置的索引」，但挡不住「由谁决定要不要验证」——换个调用方，未验证的
-- 版本照样能上线。
--
-- 而且三层门禁只实现了第三层（检索质量）。完整性与技术两层完全没有：
-- 索引漏了一半文档、向量维度对不上、分块跨版本混写，都能一路通过放行。
CREATE TABLE validation_reports (
    validation_report_id text PRIMARY KEY,
    index_version_id text NOT NULL
        REFERENCES index_versions(index_version_id) ON DELETE CASCADE,
    -- 绑定被验证的那次构建。允许为空：存量版本没有对应的 build 记录。
    index_build_id text REFERENCES index_builds(index_build_id) ON DELETE SET NULL,
    status text NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'running', 'pass', 'failed', 'cancelled')),
    -- 阈值与判定规则的版本。写进报告是为了让历史结论可解释：门禁改严之后，
    -- 旧报告仍能说明它当时是按哪套规则通过的。
    policy_version text NOT NULL,
    evaluation_set_version text,
    baseline_version_id text REFERENCES index_versions(index_version_id) ON DELETE SET NULL,
    integrity_result jsonb NOT NULL DEFAULT '{}'::jsonb,
    technical_result jsonb NOT NULL DEFAULT '{}'::jsonb,
    retrieval_result jsonb NOT NULL DEFAULT '{}'::jsonb,
    summary text,
    failure_items jsonb NOT NULL DEFAULT '[]'::jsonb,
    -- legacy_backfill 的报告不是正式验证通过，只是把历史事实记下来。
    -- 两者必须能区分，否则回填会伪装成质量门禁通过。
    report_source text NOT NULL DEFAULT 'standard'
        CHECK (report_source IN ('standard', 'legacy_backfill')),
    started_at timestamptz,
    finished_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX validation_reports_version_idx
    ON validation_reports (index_version_id, created_at DESC);

-- 重新验证会新建报告，不覆盖历史：一个版本可以有多份报告，最新那份决定它能否激活。
-- 同一版本同时只允许一次进行中的验证，理由与构建的并发保护相同。
CREATE UNIQUE INDEX validation_reports_one_active_idx
    ON validation_reports (index_version_id)
    WHERE status IN ('pending', 'running');
