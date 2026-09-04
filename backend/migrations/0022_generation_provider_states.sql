CREATE TABLE IF NOT EXISTS generation_provider_states (
    provider text PRIMARY KEY CHECK (provider IN ('deepseek', 'gemini', 'qwen')),
    model_name text NOT NULL,
    active boolean NOT NULL DEFAULT false,
    configured boolean NOT NULL DEFAULT false,
    status text NOT NULL DEFAULT 'unconfigured'
        CHECK (status IN (
            'unconfigured', 'available', 'region_unsupported', 'quota_exhausted',
            'auth_failed', 'rate_limited', 'timeout', 'model_not_found', 'unavailable'
        )),
    status_code text,
    status_message text NOT NULL DEFAULT '未配置',
    checked_at timestamptz,
    updated_by text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS generation_provider_states_single_active_idx
    ON generation_provider_states ((active))
    WHERE active;
