-- 问答留存与检索源解耦：删除知识文件不会破坏排障时需要的历史证据。
CREATE TABLE query_records (
    request_id uuid PRIMARY KEY,
    user_query text NOT NULL,
    request_options jsonb NOT NULL,
    status text NOT NULL CHECK (status IN ('running','answered','insufficient','error','interrupted')),
    final_response jsonb,
    model_called boolean NOT NULL DEFAULT false,
    no_call_reason text,
    current_stage text NOT NULL DEFAULT 'planning',
    error_stage text, error_code text, error_message text,
    lease_key bigint NOT NULL UNIQUE,
    started_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    finished_at timestamptz,
    expires_at timestamptz NOT NULL
);
CREATE INDEX query_records_expiry ON query_records(expires_at) WHERE status <> 'running';
CREATE TABLE llm_calls (
    id uuid PRIMARY KEY,
    request_id uuid NOT NULL REFERENCES query_records(request_id) ON DELETE CASCADE,
    attempt_no integer NOT NULL CHECK (attempt_no > 0),
    status text NOT NULL CHECK (status IN ('pending','succeeded','error','interrupted')),
    request_model text,
    response_model text,
    request_messages jsonb NOT NULL,
    request_parameters jsonb NOT NULL,
    evidence_snapshot jsonb NOT NULL,
    document_ids text[] NOT NULL,
    raw_response text,
    assistant_message text,
    http_status integer,
    usage jsonb,
    error_stage text, error_code text, error_message text,
    started_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    finished_at timestamptz,
    duration_ms double precision,
    UNIQUE(request_id, attempt_no)
);
CREATE INDEX llm_calls_documents ON llm_calls USING gin(document_ids);
