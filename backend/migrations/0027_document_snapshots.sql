-- 阶段 1：冻结索引构建的输入文档集合。
--
-- 此前「这次构建包含哪些文档」由两条**不同时刻执行的即时查询**各自决定：
-- enqueue_rebuild 用 documents JOIN document_versions 列举待建清单，而
-- finalize_building_version 用 count(*) WHERE current_version_id IS NOT NULL 当覆盖完整性
-- 的分母。两次查询之间只要有文档新增或更新，分母就变了——同一个 index_version 重跑，
-- 输入集合会静默改变，构建结果无法复现。
--
-- Snapshot 一旦创建即不可修改：没有任何 UPDATE 路径写它，成员表也只在创建事务里一次写入。
CREATE TABLE document_snapshots (
    document_snapshot_id text PRIMARY KEY,
    knowledge_base_id text NOT NULL REFERENCES knowledge_bases(knowledge_base_id) ON DELETE CASCADE,
    -- 成员集合的指纹：相同的 (document_id, document_version_id) 集合必然得到相同值，
    -- 因此可以用来判断两次构建的输入是否真的一致，而不是依赖调用方声明。
    snapshot_fingerprint text NOT NULL,
    snapshot_completeness text NOT NULL DEFAULT 'complete'
        CHECK (snapshot_completeness IN ('complete', 'partial', 'unknown')),
    included_count integer NOT NULL DEFAULT 0 CHECK (included_count >= 0),
    excluded_count integer NOT NULL DEFAULT 0 CHECK (excluded_count >= 0),
    reason text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX document_snapshots_scope_idx
    ON document_snapshots (knowledge_base_id, created_at DESC);

CREATE TABLE document_snapshot_members (
    document_snapshot_id text NOT NULL
        REFERENCES document_snapshots(document_snapshot_id) ON DELETE CASCADE,
    document_id text NOT NULL,
    document_version_id text NOT NULL
        REFERENCES document_versions(document_version_id) ON DELETE CASCADE,
    -- excluded 用于记录「看到了但没纳入」，例如解析失败、无当前版本。
    -- 它们必须留痕：规格要求 Snapshot 里每个成员都有明确处理结果，静默丢弃等于
    -- 让完整性校验的分母凭空变小。
    inclusion_status text NOT NULL DEFAULT 'included'
        CHECK (inclusion_status IN ('included', 'excluded')),
    PRIMARY KEY (document_snapshot_id, document_id)
);

-- 可空，且**不为存量版本回填**。NULL 准确表达「该版本创建时尚无快照机制」；
-- 回填一条成员为空、completeness='unknown' 的记录反而是在造没有信息量的假事实，
-- 还会逼着每个读取点去区分「真快照」与「占位快照」。
ALTER TABLE index_versions ADD COLUMN document_snapshot_id text
    REFERENCES document_snapshots(document_snapshot_id) ON DELETE SET NULL;
