CREATE INDEX document_versions_status_idx
    ON document_versions (knowledge_base_id, document_id, status, version_number DESC);
