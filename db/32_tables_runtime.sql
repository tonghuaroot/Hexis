-- Hexis DB-owned runtime tables.
SET search_path = public, ag_catalog, "$user";

CREATE TABLE IF NOT EXISTS prompt_modules (
    key TEXT PRIMARY KEY,
    content TEXT NOT NULL,
    description TEXT,
    source_path TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS llm_task_kinds (
    task_kind TEXT PRIMARY KEY,
    provider_config_key TEXT NOT NULL,
    prompt_module_keys JSONB NOT NULL DEFAULT '[]'::jsonb,
    response_schema JSONB NOT NULL DEFAULT '{}'::jsonb,
    defaults JSONB NOT NULL DEFAULT '{}'::jsonb,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS external_driver_calls (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    driver TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'in_progress', 'completed', 'failed', 'dropped')),
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    result JSONB,
    error TEXT,
    attempts INT NOT NULL DEFAULT 0,
    max_attempts INT NOT NULL DEFAULT 3,
    next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    claimed_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_external_driver_calls_pending
    ON external_driver_calls (driver, next_attempt_at, created_at)
    WHERE status = 'pending';

CREATE INDEX IF NOT EXISTS idx_external_driver_calls_in_progress
    ON external_driver_calls (claimed_at)
    WHERE status = 'in_progress';

CREATE TABLE IF NOT EXISTS tool_definitions (
    name TEXT PRIMARY KEY,
    category TEXT NOT NULL,
    schema JSONB NOT NULL DEFAULT '{}'::jsonb,
    default_energy_cost INT NOT NULL DEFAULT 1,
    allowed_contexts TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
    requires_approval BOOLEAN NOT NULL DEFAULT FALSE,
    supports_parallel BOOLEAN NOT NULL DEFAULT FALSE,
    execution_kind TEXT NOT NULL DEFAULT 'python_driver'
        CHECK (execution_kind IN ('db_function', 'python_driver', 'external_driver')),
    driver TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS agent_turns (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    mode TEXT NOT NULL,
    session_id UUID,
    heartbeat_id UUID,
    status TEXT NOT NULL DEFAULT 'running'
        CHECK (status IN ('running', 'waiting_external', 'completed', 'failed', 'cancelled')),
    phase TEXT NOT NULL DEFAULT 'execute',
    user_message TEXT,
    messages JSONB NOT NULL DEFAULT '[]'::jsonb,
    runtime_state JSONB NOT NULL DEFAULT '{}'::jsonb,
    stopped_reason TEXT,
    result JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_agent_turns_status_created
    ON agent_turns (status, created_at DESC);

CREATE TABLE IF NOT EXISTS agent_turn_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    turn_id UUID NOT NULL REFERENCES agent_turns(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_agent_turn_events_turn_created
    ON agent_turn_events (turn_id, created_at);

CREATE TABLE IF NOT EXISTS workflow_step_runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workflow_id UUID NOT NULL REFERENCES workflow_executions(id) ON DELETE CASCADE,
    step_name TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    arguments JSONB NOT NULL DEFAULT '{}'::jsonb,
    depends_on TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'ready', 'in_progress', 'completed', 'failed', 'skipped')),
    output JSONB,
    error TEXT,
    attempts INT NOT NULL DEFAULT 0,
    max_attempts INT NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    UNIQUE (workflow_id, step_name)
);

CREATE INDEX IF NOT EXISTS idx_workflow_step_runs_status
    ON workflow_step_runs (workflow_id, status, created_at);

-- Change legibility (#93): the substrate-change journal the agent reads.
CREATE TABLE IF NOT EXISTS change_journal (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    kind TEXT NOT NULL CHECK (kind IN ('migration', 'code', 'prompt_module', 'config_flip', 'self_extension')),
    summary TEXT NOT NULL,
    detail JSONB NOT NULL DEFAULT '{}'::jsonb,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_change_journal_occurred
    ON change_journal (occurred_at DESC);

-- Per-section ingestion receipts (#85/#90): completion, not intent.
CREATE TABLE IF NOT EXISTS ingestion_receipts (
    doc_ref TEXT NOT NULL,
    section_hash TEXT NOT NULL,
    memory_id UUID,
    memories_created INT NOT NULL DEFAULT 0,
    source_path TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (doc_ref, section_hash)
);

-- Durable ingestion jobs (#87): background ingestion survives restarts.
CREATE TABLE IF NOT EXISTS ingestion_jobs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    kind TEXT NOT NULL CHECK (kind IN ('text', 'url')),
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'in_progress', 'completed', 'failed', 'cancelled')),
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    content TEXT,
    content_hash TEXT,
    progress JSONB NOT NULL DEFAULT '{}'::jsonb,
    result JSONB,
    error TEXT,
    cancel_requested BOOLEAN NOT NULL DEFAULT FALSE,
    attempts INT NOT NULL DEFAULT 0,
    max_attempts INT NOT NULL DEFAULT 3,
    next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    claimed_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_ingestion_jobs_pending
    ON ingestion_jobs (next_attempt_at, created_at) WHERE status = 'pending';
CREATE INDEX IF NOT EXISTS idx_ingestion_jobs_in_progress
    ON ingestion_jobs (claimed_at) WHERE status = 'in_progress';
CREATE UNIQUE INDEX IF NOT EXISTS idx_ingestion_jobs_active_hash
    ON ingestion_jobs (content_hash) WHERE status IN ('pending', 'in_progress');
