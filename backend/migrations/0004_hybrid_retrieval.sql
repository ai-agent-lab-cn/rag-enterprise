CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE INDEX chunks_content_trgm_idx
    ON chunks USING gin (lower(content) gin_trgm_ops);
