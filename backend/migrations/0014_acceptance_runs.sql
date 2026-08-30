-- V5-10：真实链路总验收运行与八阶段证据。
CREATE TABLE acceptance_runs (
    acceptance_run_id text PRIMARY KEY,
    knowledge_base_id text REFERENCES knowledge_bases(knowledge_base_id) ON DELETE SET NULL,
    status text NOT NULL CHECK (status IN ('passed','failed','blocked')),
    commit_sha text NOT NULL,
    schema_version integer NOT NULL CHECK (schema_version > 0),
    steps jsonb NOT NULL,
    limitations jsonb NOT NULL DEFAULT '[]'::jsonb,
    created_by text REFERENCES users(user_id) ON DELETE SET NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX acceptance_runs_created_idx ON acceptance_runs (created_at DESC);
CREATE INDEX acceptance_runs_kb_created_idx
    ON acceptance_runs (knowledge_base_id, created_at DESC);

ALTER TABLE evaluation_runs DROP CONSTRAINT evaluation_runs_evaluation_type_check;
ALTER TABLE evaluation_runs ADD CONSTRAINT evaluation_runs_evaluation_type_check
    CHECK (evaluation_type IN ('retrieval','answer','pipeline','security','acceptance'));
