UPDATE document_categories category
SET origin_type = 'template_copy'
FROM category_template_items item
JOIN category_templates template ON template.template_id = item.template_id
WHERE category.origin_type = 'migration'
  AND template.is_default
  AND category.category_id =
      'cat_' || substr(md5(category.knowledge_base_id || ':' || item.template_item_id), 1, 16);
