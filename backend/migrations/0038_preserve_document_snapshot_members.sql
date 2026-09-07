-- Document Snapshot 是构建输入事实，不能随在线 Document / Revision 删除而变化。
-- 旧外键使用 ON DELETE CASCADE，删除资料会把历史成员一并删掉，导致版本差异、覆盖分母
-- 和回滚内容时间点都被改写。把审计所需字段固化后解除级联依赖，成员记录由 Snapshot 自身
-- 的 ON DELETE CASCADE 管理。

ALTER TABLE document_snapshot_members ADD COLUMN content_sha256 text;
ALTER TABLE document_snapshot_members ADD COLUMN filename text;

UPDATE document_snapshot_members m
SET content_sha256=COALESCE(v.content_sha256, ''),
    filename=COALESCE(d.filename, m.document_id)
FROM document_versions v
LEFT JOIN documents d
  ON d.knowledge_base_id=v.knowledge_base_id AND d.document_id=v.document_id
WHERE m.document_version_id=v.document_version_id;

UPDATE document_snapshot_members
SET content_sha256=COALESCE(content_sha256, ''),
    filename=COALESCE(filename, document_id);

ALTER TABLE document_snapshot_members ALTER COLUMN content_sha256 SET NOT NULL;
ALTER TABLE document_snapshot_members ALTER COLUMN filename SET NOT NULL;
ALTER TABLE document_snapshot_members
    DROP CONSTRAINT IF EXISTS document_snapshot_members_document_version_id_fkey;

CREATE INDEX document_snapshot_members_version_idx
    ON document_snapshot_members (document_version_id)
    WHERE document_version_id IS NOT NULL;
