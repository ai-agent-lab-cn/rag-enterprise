CREATE TABLE document_categories (
    category_id text PRIMARY KEY,
    knowledge_base_id text NOT NULL REFERENCES knowledge_bases(knowledge_base_id) ON DELETE CASCADE,
    name text NOT NULL,
    normalized_name text NOT NULL,
    description text NOT NULL DEFAULT '',
    sort_order integer NOT NULL DEFAULT 0,
    active boolean NOT NULL DEFAULT true,
    is_system boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (knowledge_base_id, normalized_name),
    UNIQUE (knowledge_base_id, category_id)
);

INSERT INTO document_categories
    (category_id, knowledge_base_id, name, normalized_name, description, sort_order, active, is_system)
SELECT 'cat_' || substr(md5(knowledge_base_id || ':未分类'), 1, 16),
       knowledge_base_id, '未分类', '未分类', '尚未完成分类的资料', 0, true, true
FROM knowledge_bases
ON CONFLICT (knowledge_base_id, normalized_name) DO NOTHING;

INSERT INTO document_categories
    (category_id, knowledge_base_id, name, normalized_name, description, sort_order, active, is_system)
SELECT 'cat_' || substr(md5(d.knowledge_base_id || ':' || lower(d.metadata->>'category')), 1, 16),
       d.knowledge_base_id, d.metadata->>'category', lower(d.metadata->>'category'), '', 100, true, false
FROM documents d
WHERE COALESCE(d.metadata->>'category', '未分类') <> '未分类'
GROUP BY d.knowledge_base_id, d.metadata->>'category'
ON CONFLICT (knowledge_base_id, normalized_name) DO NOTHING;

UPDATE documents d
SET metadata = d.metadata || jsonb_build_object(
    'category_id', c.category_id,
    'classification_status', CASE WHEN c.is_system THEN 'pending' ELSE 'manual' END,
    'classification_confidence', NULL,
    'suggested_category_id', NULL,
    'classification_model', NULL,
    'classified_at', NULL
)
FROM document_categories c
WHERE c.knowledge_base_id = d.knowledge_base_id
  AND c.normalized_name = lower(COALESCE(d.metadata->>'category', '未分类'));

UPDATE chunks ch
SET metadata = ch.metadata || jsonb_build_object(
    'category_id', d.metadata->'category_id',
    'category', d.metadata->'category',
    'classification_status', d.metadata->'classification_status',
    'classification_confidence', d.metadata->'classification_confidence',
    'suggested_category_id', d.metadata->'suggested_category_id',
    'classification_model', d.metadata->'classification_model',
    'classified_at', d.metadata->'classified_at'
)
FROM documents d
WHERE ch.knowledge_base_id = d.knowledge_base_id
  AND ch.metadata->>'document_id' = d.document_id;

CREATE INDEX document_categories_kb_order_idx
    ON document_categories (knowledge_base_id, active DESC, sort_order, normalized_name);
CREATE INDEX documents_category_id_idx ON documents ((metadata->>'category_id'));
