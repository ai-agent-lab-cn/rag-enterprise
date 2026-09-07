-- 阶段 1：把「已退役」与「已清理」分成两个状态。
--
-- 此前 retire_version() 一步做完两件事：校验状态是 retired/failed，然后删掉分块与
-- 物理索引——但**不改状态**。于是库里的 retired 有两种含义完全不同的版本：分块还在、
-- 可以回滚回去的，和分块已经删光、回滚过去就是空索引的。两者在页面上、在 API 里、
-- 在回滚校验里都长得一模一样。
--
-- 生产链把它们分开：retired（不承载流量，物理索引仍保留）→ cleaned（物理资源已清理，
-- 仅保留治理记录）。回滚只能回到前者。
ALTER TABLE index_versions DROP CONSTRAINT IF EXISTS index_versions_status_check;
ALTER TABLE index_versions ADD CONSTRAINT index_versions_status_check
    CHECK (status IN ('building', 'ready', 'active', 'previous', 'retired', 'failed', 'cleaned'));

ALTER TABLE index_versions ADD COLUMN cleaned_at timestamptz;

-- 存量数据不猜：升级前无法区分某个 retired 版本的分块是否还在，
-- 因此一律保持 retired，由下一次 cleanup 显式推进。
