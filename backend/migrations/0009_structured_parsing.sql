ALTER TABLE document_versions
    ADD COLUMN parser_name text,
    ADD COLUMN parser_version text,
    ADD COLUMN processing_options jsonb NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN parsed_content_hash text,
    ADD COLUMN parse_status text NOT NULL DEFAULT 'pending',
    ADD COLUMN parse_failure_code text,
    ADD COLUMN parsed_tree jsonb NOT NULL DEFAULT '[]'::jsonb;

ALTER TABLE document_versions
    ADD CONSTRAINT document_versions_parse_status_check
    CHECK (parse_status IN ('pending', 'parsing', 'chunking', 'ready', 'failed'));

UPDATE document_versions
SET parser_name = CASE
        WHEN source_path ~* '\\.pdf$' THEN 'pypdf'
        ELSE 'legacy-text'
    END,
    parser_version = 'legacy',
    processing_options = jsonb_build_object('legacy', true),
    parsed_content_hash = content_sha256,
    parse_status = CASE WHEN status = 'failed' THEN 'failed' ELSE 'ready' END,
    parse_failure_code = CASE WHEN status = 'failed' THEN 'LEGACY_PROCESSING_FAILED' END
WHERE parser_name IS NULL;

CREATE INDEX document_versions_parse_status_idx
    ON document_versions (knowledge_base_id, parse_status, created_at DESC);
