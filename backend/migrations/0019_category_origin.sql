DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = 'document_categories'
          AND column_name = 'origin_type'
    ) THEN
        ALTER TABLE document_categories
            ADD COLUMN origin_type text NOT NULL DEFAULT 'manual';

        -- 升级前的分类没有可靠来源证据；统一记为历史迁移，不按名称推断。
        UPDATE document_categories
        SET origin_type = 'migration';
    END IF;
END $$;

ALTER TABLE document_categories
    DROP CONSTRAINT IF EXISTS document_categories_origin_type_check;

ALTER TABLE document_categories
    ADD CONSTRAINT document_categories_origin_type_check
    CHECK (origin_type IN ('template_copy', 'manual', 'migration'));

CREATE INDEX IF NOT EXISTS document_categories_kb_origin_idx
    ON document_categories (knowledge_base_id, origin_type);
