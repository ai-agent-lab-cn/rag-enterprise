ALTER TABLE documents
    ADD COLUMN metadata jsonb NOT NULL DEFAULT '{
      "category":"未分类", "tags":[], "source_system":"upload",
      "external_resource_id":null, "owner_user_id":null, "department":null,
      "sensitivity":"internal", "valid_from":null, "valid_to":null,
      "retrieval_status":"searchable", "acl_version":1,
      "allow_user_ids":[], "deny_user_ids":[]
    }'::jsonb;

ALTER TABLE data_sources
    ADD COLUMN acl jsonb NOT NULL DEFAULT '{
      "version":1, "allow_user_ids":[], "deny_user_ids":[]
    }'::jsonb;

CREATE INDEX documents_metadata_gin_idx ON documents USING gin (metadata);
CREATE INDEX data_sources_acl_gin_idx ON data_sources USING gin (acl);
