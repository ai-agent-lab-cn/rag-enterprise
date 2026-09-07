-- 阶段 2：让文档版本记得自己从哪来。
--
-- document_versions 已经承载了 Document Revision 的绝大部分语义——版本号、内容哈希、
-- 五条唯一约束、完整的状态机。缺的只是来源追踪：一个版本是哪次同步、从哪个 URI、
-- 以远端的哪个 etag 拉回来的，目前一概查不到。
--
-- 出事的时候这几列就是全部线索：远端改了一份文件、同步却没更新，要判断是连接器没发现
-- 变化还是索引没跑成，得先知道当时拿到的 etag 是什么。现在只能猜。
--
-- 不新建 document_revisions 表：那意味着全量数据迁移、双写期、chunks 外键改指向，
-- 风险远大于收益，而现有表本就是同一个概念。
ALTER TABLE document_versions ADD COLUMN source_uri text;
ALTER TABLE document_versions ADD COLUMN source_etag text;
ALTER TABLE document_versions ADD COLUMN source_modified_at timestamptz;
-- 哪一次同步产生了这个版本。API 上传的版本这里为空，那本身就是有用的区分。
ALTER TABLE document_versions ADD COLUMN sync_run_id text
    REFERENCES sync_runs(sync_run_id) ON DELETE SET NULL;

CREATE INDEX document_versions_sync_run_idx
    ON document_versions (sync_run_id) WHERE sync_run_id IS NOT NULL;
