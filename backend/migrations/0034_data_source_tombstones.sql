-- 阶段 2：软删除要留下墓碑，而不是抹掉记录。
--
-- 现状的软删除做两件事：把 documents.metadata.retrieval_status 置为 deleted，然后
-- **从 data_source_objects 里删掉那一行**。删行是为了不让已删对象永久污染熔断的比例
-- 分母（否则它每次同步都被重新算进「待删除」），理由成立，但代价是删除这件事本身不留痕：
--
--   - 查不到某份资料是什么时候、被哪次同步删掉的
--   - 历史文档快照里的成员，无从解释它为什么不在当前清单里
--   - 对象重新出现时只能当全新对象重新解析索引一遍，即使内容一个字都没变
--
-- 墓碑把「已删除」与「从未见过」分开。熔断分母仍然只数 data_source_objects，
-- 因此不受影响。
CREATE TABLE data_source_tombstones (
    data_source_id text NOT NULL
        REFERENCES data_sources(data_source_id) ON DELETE CASCADE,
    object_key text NOT NULL,
    -- 删除时该对象的内容版本。对象重现且 version 未变时，据此可以判定内容没动过。
    version text NOT NULL,
    document_id text,
    -- 哪一次同步删的。人工触发的删除这里为空。
    sync_run_id text REFERENCES sync_runs(sync_run_id) ON DELETE SET NULL,
    deleted_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (data_source_id, object_key)
);
CREATE INDEX data_source_tombstones_recent_idx
    ON data_source_tombstones (data_source_id, deleted_at DESC);
