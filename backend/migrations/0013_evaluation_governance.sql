-- V5-9：统一评测运行、Bad Case 生命周期与回归集治理。
CREATE TABLE evaluation_runs (
    evaluation_run_id text PRIMARY KEY,
    evaluation_type text NOT NULL
        CHECK (evaluation_type IN ('retrieval','answer','pipeline','security')),
    dataset_id text NOT NULL,
    dataset_version text NOT NULL,
    commit_sha text NOT NULL,
    knowledge_base_id text REFERENCES knowledge_bases(knowledge_base_id) ON DELETE SET NULL,
    prompt_version text,
    prompt_hash text,
    parser_version text,
    chunking_version text,
    index_version_id text REFERENCES index_versions(index_version_id) ON DELETE SET NULL,
    models jsonb NOT NULL DEFAULT '{}'::jsonb,
    parameters jsonb NOT NULL DEFAULT '{}'::jsonb,
    metrics jsonb NOT NULL DEFAULT '{}'::jsonb,
    passed boolean NOT NULL,
    official boolean NOT NULL DEFAULT false,
    run_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (evaluation_type, dataset_id, dataset_version, commit_sha, run_at)
);

CREATE INDEX evaluation_runs_type_run_idx
    ON evaluation_runs (evaluation_type, run_at DESC);

CREATE TABLE bad_cases (
    case_id text PRIMARY KEY,
    source_type text NOT NULL CHECK (source_type IN ('online','evaluation','manual')),
    source_record_id text NOT NULL,
    knowledge_base_id text NOT NULL REFERENCES knowledge_bases(knowledge_base_id) ON DELETE CASCADE,
    dataset_version text,
    question text NOT NULL,
    expected_source_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
    actual_source_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
    expected_answer_status text,
    actual_answer_status text,
    actual_answer text,
    failure_stage text NOT NULL,
    root_cause text,
    category text NOT NULL,
    severity text NOT NULL DEFAULT 'medium'
        CHECK (severity IN ('low','medium','high','critical')),
    assignee text,
    fix_commit text,
    status text NOT NULL DEFAULT 'new'
        CHECK (status IN ('new','confirmed','fixing','resolved','regression_added','ignored')),
    regression_added boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now(),
    confirmed_at timestamptz,
    resolved_at timestamptz,
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (source_type, source_record_id)
);

CREATE INDEX bad_cases_kb_status_created_idx
    ON bad_cases (knowledge_base_id, status, created_at DESC);

CREATE TABLE regression_cases (
    regression_case_id text PRIMARY KEY,
    case_id text NOT NULL UNIQUE REFERENCES bad_cases(case_id) ON DELETE CASCADE,
    dataset_id text NOT NULL DEFAULT 'rag-enterprise-bad-cases',
    dataset_version text NOT NULL,
    last_evaluation_run_id text REFERENCES evaluation_runs(evaluation_run_id) ON DELETE SET NULL,
    last_passed boolean,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
