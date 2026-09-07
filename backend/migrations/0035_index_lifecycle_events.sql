-- 阶段 4：索引版本的生命周期留痕。
--
-- 版本表只保留「现在是什么状态」，`activated_at` / `retired_at` / `cleaned_at` 三个时间戳
-- 各自只记最后一次。于是「这个版本被激活过几次、中间回滚过没有、谁做的」在任何一张表里
-- 都查不到——通用 audit 表倒是记了 activate 与 rollback 两个动作，但它
-- **actor_id 恒为 NULL**（`index_versions.py` 里两处硬编码），而且不含前后状态。
--
-- append-only：业务代码只插不改。回滚不是「撤销一条记录」，是再追加一条方向相反的事件。
--
-- 枚举里每一种都必须有代码会写入。曾经列了 build_started，但构建「开始」在本仓没有单一
-- 落点（任务是逐文档领取的），created 加 build_succeeded/build_failed 已经说清了全过程。
-- 声明一个没有产生者的事件，只会让人以为系统会记录它——
-- `test_every_declared_lifecycle_event_type_has_a_producer` 守着这条。
CREATE TABLE index_lifecycle_events (
    event_id text PRIMARY KEY,
    knowledge_base_id text NOT NULL
        REFERENCES knowledge_bases(knowledge_base_id) ON DELETE CASCADE,
    index_version_id text NOT NULL
        REFERENCES index_versions(index_version_id) ON DELETE CASCADE,
    event_type text NOT NULL CHECK (event_type IN (
        'created', 'build_succeeded', 'build_failed',
        'validation_passed', 'validation_failed',
        'activated', 'deactivated', 'rolled_back', 'retired', 'cleaned'
    )),
    from_status text,
    to_status text,
    -- 谁做的。系统自动触发（worker 收口构建、引导首版）时为空，那本身就是有用的区分：
    -- 空表示「没有人按下按钮」，不是「不知道是谁」。
    actor_id text,
    actor_role text,
    reason text,
    -- 关联到促成这次转换的证据：验证报告、构建尝试、评测报告 id。
    validation_report_id text
        REFERENCES validation_reports(validation_report_id) ON DELETE SET NULL,
    index_build_id text REFERENCES index_builds(index_build_id) ON DELETE SET NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX index_lifecycle_events_version_idx
    ON index_lifecycle_events (index_version_id, created_at DESC);
CREATE INDEX index_lifecycle_events_scope_idx
    ON index_lifecycle_events (knowledge_base_id, created_at DESC);
