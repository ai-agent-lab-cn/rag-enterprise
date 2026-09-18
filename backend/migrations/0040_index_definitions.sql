-- 知识库级 Index Definition 只保存当前真正可编辑的 Chunking。
-- Embedding 维度仍受 chunks.vector(N) 与全局 index_settings 约束；Parser、索引结构和
-- Reranker 由运行时注册表提供，不能复制成看似可编辑、实际不生效的字段。
CREATE TABLE index_definitions (
    knowledge_base_id text PRIMARY KEY
        REFERENCES knowledge_bases(knowledge_base_id) ON DELETE CASCADE,
    chunk_size integer NOT NULL CHECK (chunk_size BETWEEN 100 AND 4000),
    chunk_overlap integer NOT NULL CHECK (chunk_overlap BETWEEN 0 AND 1000),
    updated_by text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK (chunk_overlap < chunk_size)
);

ALTER TABLE index_versions
    ADD COLUMN excluded_documents_acknowledged boolean NOT NULL DEFAULT false;

-- 已有知识库优先继承 Active Version 的真实 Chunking，避免升级后重新退回应用默认值并
-- 制造一条并不存在的 config drift。没有 Active Version 的知识库在首次创建时落 Definition。
INSERT INTO index_definitions (knowledge_base_id, chunk_size, chunk_overlap)
SELECT iv.knowledge_base_id,
       (iv.processing_options ->> 'chunk_size')::integer,
       (iv.processing_options ->> 'chunk_overlap')::integer
FROM index_versions iv
WHERE iv.status = 'active'
  AND iv.processing_options ? 'chunk_size'
  AND iv.processing_options ? 'chunk_overlap'
ON CONFLICT (knowledge_base_id) DO NOTHING;
