-- 索引版本是知识库级的读指针来源。重建写入非 active 版本，用户检索因此看不到未放行的
-- 分块；切换与回滚都只是移动指针，旧版本分块完整保留。V5-4 之前的重建是原地 DELETE
-- 再写入，指标变差时没有任何退路。
CREATE TABLE index_versions (
    index_version_id text PRIMARY KEY,
    -- 索引版本从属于知识库：用 RESTRICT 会让分块删完的空知识库因为版本记录还在而永远删不掉。
    -- 真正的删除保护由 chunks 对知识库的 RESTRICT 承担。
    knowledge_base_id text NOT NULL REFERENCES knowledge_bases(knowledge_base_id) ON DELETE CASCADE,
    status text NOT NULL CHECK (status IN ('building', 'ready', 'active', 'previous', 'retired', 'failed')),
    chunking_version text NOT NULL,
    parser_version text NOT NULL,
    embedding_model text NOT NULL,
    embedding_dimension integer NOT NULL CHECK (embedding_dimension > 0),
    processing_options jsonb NOT NULL DEFAULT '{}'::jsonb,
    config_fingerprint text NOT NULL CHECK (config_fingerprint ~ '^[a-f0-9]{64}$'),
    evaluation_report_id text,
    rebuild_batch_id text,
    created_at timestamptz NOT NULL DEFAULT now(),
    activated_at timestamptz,
    retired_at timestamptz
);

-- 三种在用状态各自唯一，由数据库保证，不依赖应用层自律。
CREATE UNIQUE INDEX index_versions_one_active_idx
    ON index_versions (knowledge_base_id) WHERE status = 'active';
CREATE UNIQUE INDEX index_versions_one_building_idx
    ON index_versions (knowledge_base_id) WHERE status = 'building';
CREATE UNIQUE INDEX index_versions_one_previous_idx
    ON index_versions (knowledge_base_id) WHERE status = 'previous';

-- active 版本必须有放行依据；building 与 ready 阶段还没有。
ALTER TABLE index_versions ADD CONSTRAINT index_versions_active_requires_report
    CHECK (status <> 'active' OR evaluation_report_id IS NOT NULL);

-- 与 index_versions 互为外键，因此指针侧用 SET NULL 打破删除时的循环引用。
ALTER TABLE knowledge_bases
    ADD COLUMN active_index_version_id text
        REFERENCES index_versions(index_version_id) ON DELETE SET NULL;

ALTER TABLE chunks ADD COLUMN index_version_id text REFERENCES index_versions(index_version_id);

-- 存量回填：有分块的知识库各得到一条 active 版本。配置取自现有事实，取不到写 legacy
-- 而不猜测历史值。没有分块的知识库不建版本，首次索引时再引导创建。
DO $$
DECLARE
    kb record;
    new_id text;
    settings record;
BEGIN
    SELECT embedding_model, embedding_dimension INTO settings FROM index_settings WHERE singleton;
    FOR kb IN SELECT knowledge_base_id FROM knowledge_bases LOOP
        IF NOT EXISTS (SELECT 1 FROM chunks WHERE knowledge_base_id = kb.knowledge_base_id) THEN
            CONTINUE;
        END IF;
        new_id := 'iv_' || substr(md5(kb.knowledge_base_id || clock_timestamp()::text), 1, 16);
        INSERT INTO index_versions (
            index_version_id, knowledge_base_id, status, chunking_version, parser_version,
            embedding_model, embedding_dimension, processing_options, config_fingerprint,
            evaluation_report_id, activated_at
        )
        SELECT new_id, kb.knowledge_base_id, 'active',
               COALESCE(max(v.chunking_version), 'legacy'),
               COALESCE(max(v.parser_version), 'legacy'),
               COALESCE(settings.embedding_model, 'legacy'),
               COALESCE(settings.embedding_dimension, 1),
               '{"legacy": true}'::jsonb,
               md5(new_id) || md5('legacy-backfill'),
               'legacy-backfill', now()
        FROM documents d
        JOIN document_versions v ON v.document_version_id = d.current_version_id
        WHERE d.knowledge_base_id = kb.knowledge_base_id;

        UPDATE chunks SET index_version_id = new_id WHERE knowledge_base_id = kb.knowledge_base_id;
        UPDATE knowledge_bases SET active_index_version_id = new_id
            WHERE knowledge_base_id = kb.knowledge_base_id;
    END LOOP;
END $$;

-- 回填完成后才能加 NOT NULL。若仍有分块未归属，这里失败即停，不让升级留下检索不到的数据。
ALTER TABLE chunks ALTER COLUMN index_version_id SET NOT NULL;

ALTER TABLE chunks DROP CONSTRAINT chunks_document_version_id_chunk_index_key;
ALTER TABLE chunks ADD CONSTRAINT chunks_version_index_key
    UNIQUE (document_version_id, index_version_id, chunk_index);

CREATE INDEX chunks_index_version_idx ON chunks (index_version_id);

-- pgvector 的 HNSW/IVFFlat 要求列带维度修饰，无维度列建索引会报
-- "column does not have dimensions"。维度从既有登记读取，不写死数字；空库时
-- index_settings 无行，列保持无维度，由首次 register_embedding_model 补做。
DO $$
DECLARE dim integer;
BEGIN
    SELECT embedding_dimension INTO dim FROM index_settings WHERE singleton;
    IF dim IS NOT NULL THEN
        EXECUTE format('ALTER TABLE chunks ALTER COLUMN embedding TYPE vector(%s)', dim);
    END IF;
END $$;
