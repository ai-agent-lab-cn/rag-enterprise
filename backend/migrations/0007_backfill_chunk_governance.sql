-- 将 V5-3 之前建立的 Chunk 补齐文档与数据源治理字段。
-- 只更新 metadata，不修改内容与 embedding，因此不需要重建向量索引。
UPDATE chunks AS c
SET metadata = c.metadata || jsonb_build_object(
    'category', COALESCE(d.metadata->'category', '"未分类"'::jsonb),
    'tags', COALESCE(d.metadata->'tags', '[]'::jsonb),
    'source_type', to_jsonb(s.source_type),
    'source_system', COALESCE(d.metadata->'source_system', '"upload"'::jsonb),
    'external_resource_id', COALESCE(d.metadata->'external_resource_id', 'null'::jsonb),
    'owner_user_id', COALESCE(d.metadata->'owner_user_id', 'null'::jsonb),
    'department', COALESCE(d.metadata->'department', 'null'::jsonb),
    'sensitivity', COALESCE(d.metadata->'sensitivity', '"internal"'::jsonb),
    'valid_from', COALESCE(d.metadata->'valid_from', 'null'::jsonb),
    'valid_to', COALESCE(d.metadata->'valid_to', 'null'::jsonb),
    'retrieval_status', COALESCE(d.metadata->'retrieval_status', '"searchable"'::jsonb),
    'acl_version', COALESCE(d.metadata->'acl_version', '1'::jsonb),
    'allow_user_ids', COALESCE(d.metadata->'allow_user_ids', '[]'::jsonb),
    'deny_user_ids', COALESCE(d.metadata->'deny_user_ids', '[]'::jsonb),
    'data_source_acl', COALESCE(s.acl, '{"version":1,"allow_user_ids":[],"deny_user_ids":[]}'::jsonb)
)
FROM documents AS d
JOIN data_sources AS s ON s.data_source_id = d.data_source_id
WHERE c.knowledge_base_id = d.knowledge_base_id
  AND c.metadata->>'document_id' = d.document_id;
