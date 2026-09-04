ALTER TABLE generation_provider_states
    DROP CONSTRAINT IF EXISTS generation_provider_states_provider_check;

UPDATE generation_provider_states
SET provider = 'kimi', model_name = 'kimi-k3', updated_at = now()
WHERE provider = 'qwen'
  AND NOT EXISTS (
      SELECT 1 FROM generation_provider_states existing WHERE existing.provider = 'kimi'
  );

DELETE FROM generation_provider_states WHERE provider = 'qwen';

ALTER TABLE generation_provider_states
    ADD CONSTRAINT generation_provider_states_provider_check
    CHECK (provider IN ('deepseek', 'gemini', 'kimi'));

ALTER TABLE generation_provider_states
    ADD COLUMN IF NOT EXISTS balance_status text NOT NULL DEFAULT 'unknown',
    ADD COLUMN IF NOT EXISTS balance_amount numeric(18, 6),
    ADD COLUMN IF NOT EXISTS balance_currency text,
    ADD COLUMN IF NOT EXISTS balance_limit numeric(18, 6),
    ADD COLUMN IF NOT EXISTS balance_checked_at timestamptz;

ALTER TABLE generation_provider_states
    DROP CONSTRAINT IF EXISTS generation_provider_states_balance_status_check;

ALTER TABLE generation_provider_states
    ADD CONSTRAINT generation_provider_states_balance_status_check
    CHECK (balance_status IN ('unknown', 'available', 'unsupported', 'error'));
