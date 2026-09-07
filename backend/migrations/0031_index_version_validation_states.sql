-- 阶段 1：把验证阶段放进状态机。
--
-- 生产链要求 ready 只有一个入口：validating --pass--> ready。此前 ready 同时表示两件
-- 互不相干的事——finalize_building_version 在「构建覆盖完整」时置 ready（**没有经过任何
-- 验证**），rollback_to_previous 又把已验证并上线过的原 active 降为 ready。两种版本在库里
-- 完全同形，而 switch_to_version 只检查 status='ready' 就进入放行流程。
--
-- 新增两个状态把这条路补上：
--   validating          构建成功、等待或正在执行三层门禁
--   validation_failed   构建成功但没通过门禁，可重新验证或重新构建
--
-- 同时把 failed 拆开。此前构建失败与验证失败都是 failed，而生产链里它们有两条不同的
-- 恢复路径（重新构建 / 重新验证），页面无从区分该给哪个按钮。
ALTER TABLE index_versions DROP CONSTRAINT IF EXISTS index_versions_status_check;
ALTER TABLE index_versions ADD CONSTRAINT index_versions_status_check
    CHECK (status IN ('building', 'validating', 'ready', 'active', 'previous',
                      'retired', 'cleaned', 'build_failed', 'validation_failed'));

-- 存量 failed 一律是构建失败：此前唯一写 failed 的地方是 finalize_building_version
-- 的覆盖完整性判定，验证失败在旧实现里根本没有落点。
UPDATE index_versions SET status = 'build_failed' WHERE status = 'failed';

-- 记录版本进入 ready 所依据的那份验证报告。active_requires_report 约束此前认的是
-- evaluation_report_id（一个自由文本），现在有了真实的报告实体。
ALTER TABLE index_versions ADD COLUMN validation_report_id text
    REFERENCES validation_reports(validation_report_id) ON DELETE SET NULL;
