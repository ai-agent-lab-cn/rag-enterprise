-- V16：允许资料没有分类，把「分类处理状态」从「分类归属」里拆出来。
--
-- V15 之前分类为空就写系统「未分类」，于是「模型超时了」和「管理员就是没给它分类」
-- 在库里长得一模一样，谁也没法按状态治理。本迁移让 category/category_id 可以是 null，
-- 并把失败原因、重试次数放进 metadata 独立字段。
--
-- 迁移只动 metadata，不碰正文、Embedding 与索引版本，不要求向量重建。

-- 「未分类」恢复成普通分类名：模板可以创建它，与其他分类遵循同一套治理规则。
ALTER TABLE category_template_items
    DROP CONSTRAINT IF EXISTS category_template_items_normalized_name_check;

-- 列默认值里写死了 "category": "未分类"。不改这里，任何不显式给 metadata 的插入
-- 都会重新造出伪分类，前面所有清理都会被它一条条撤销。
ALTER TABLE documents
    ALTER COLUMN metadata SET DEFAULT '{
        "tags": [], "category": null, "category_id": null, "valid_to": null,
        "department": null, "valid_from": null, "acl_version": 1,
        "sensitivity": "internal", "deny_user_ids": [], "owner_user_id": null,
        "source_system": "upload", "allow_user_ids": [],
        "retrieval_status": "searchable", "external_resource_id": null,
        "classification_status": "pending", "classification_confidence": null,
        "suggested_category_id": null, "classification_model": null,
        "classified_at": null, "classification_failure_code": null,
        "classification_failure_reason": null, "classification_failed_at": null,
        "classification_retry_count": 0, "classification_next_retry_at": null
    }'::jsonb;

-- 分类失败字段就位。JSONB 本身没有列约束，但显式补上默认 null 才能让「这份资料
-- 没失败过」与「这份资料是旧数据、字段还不存在」区分开。
UPDATE documents
SET metadata = jsonb_build_object(
        'classification_failure_code', NULL,
        'classification_failure_reason', NULL,
        'classification_failed_at', NULL,
        'classification_retry_count', 0,
        'classification_next_retry_at', NULL
    ) || metadata
WHERE NOT (metadata ? 'classification_failure_code');

UPDATE chunks
SET metadata = jsonb_build_object(
        'classification_failure_code', NULL,
        'classification_failure_reason', NULL,
        'classification_failed_at', NULL,
        'classification_retry_count', 0,
        'classification_next_retry_at', NULL
    ) || metadata
WHERE NOT (metadata ? 'classification_failure_code');

-- 拆掉系统「未分类」。
--
-- 判定依据是 is_system，不是名字：管理员完全可能自己建过一个同名的普通分类，那是他的
-- 业务分类，按名字删会把它一并误伤。下面每一处都带着 is_system 条件，正是为此。

-- 引用系统「未分类」的资料退回「没有分类」。原本就是 failed 的保留失败状态——那是排查
-- 线索，抹平成 pending 会让一批真实故障凭空消失；其余一律回到 pending 等待重新分类。
UPDATE documents d
SET metadata = d.metadata || jsonb_build_object(
        'category_id', NULL,
        'category', NULL,
        'classification_status',
        CASE WHEN d.metadata->>'classification_status' = 'failed' THEN 'failed' ELSE 'pending' END
    )
FROM document_categories c
WHERE c.category_id = d.metadata->>'category_id' AND c.is_system;

-- 当前活动版本的分块同步清空；历史版本保留当时的快照，它们受索引版本隔离、不进检索。
UPDATE chunks ch
SET metadata = ch.metadata || jsonb_build_object('category_id', NULL, 'category', NULL)
FROM documents d, document_categories c
WHERE ch.knowledge_base_id = d.knowledge_base_id
  AND ch.document_version_id = d.current_version_id
  AND ch.metadata->>'category_id' = c.category_id
  AND c.is_system;

DELETE FROM document_categories WHERE is_system;
