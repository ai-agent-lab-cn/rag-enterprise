-- V18：为升级前遗留且分类字典仍为空的默认知识库补齐默认模板。
--
-- V15 只在“新建知识库”时复制模板，因此升级前已经存在的 kb_default 会一直没有
-- 可选分类，资料编辑弹层也只能显示“无分类”。这里刻意只处理 is_default=true 且
-- 一条分类都没有的知识库：用户显式创建的空分类知识库和已有治理数据都不改动。

INSERT INTO document_categories
    (category_id, knowledge_base_id, name, normalized_name, description,
     sort_order, active, is_system)
SELECT 'cat_' || substr(md5(kb.knowledge_base_id || ':' || item.template_item_id), 1, 16),
       kb.knowledge_base_id,
       item.name,
       item.normalized_name,
       item.description,
       item.sort_order,
       true,
       false
FROM knowledge_bases kb
JOIN category_templates template
  ON template.is_default AND template.active
JOIN category_template_items item
  ON item.template_id = template.template_id AND item.active
WHERE kb.is_default
  AND NOT EXISTS (
      SELECT 1
      FROM document_categories existing
      WHERE existing.knowledge_base_id = kb.knowledge_base_id
  )
ON CONFLICT (knowledge_base_id, normalized_name) DO NOTHING;
