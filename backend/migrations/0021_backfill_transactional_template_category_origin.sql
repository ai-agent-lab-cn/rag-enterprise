WITH creation_batches AS (
    SELECT category.knowledge_base_id, category.created_at
    FROM document_categories category
    JOIN knowledge_bases knowledge_base
      ON knowledge_base.knowledge_base_id = category.knowledge_base_id
    WHERE category.origin_type = 'migration'
      AND category.created_at >= knowledge_base.created_at
      AND category.created_at < knowledge_base.created_at + interval '1 second'
    GROUP BY category.knowledge_base_id, category.created_at
    HAVING count(*) >= 2
)
UPDATE document_categories category
SET origin_type = 'template_copy'
FROM creation_batches batch
WHERE category.knowledge_base_id = batch.knowledge_base_id
  AND category.created_at = batch.created_at
  AND category.origin_type = 'migration';
