-- Stage 10 application schema. LangGraph manages its own checkpoint migrations.
CREATE TABLE IF NOT EXISTS review_runs (
    id UUID PRIMARY KEY,
    status TEXT NOT NULL CHECK (status IN ('pending', 'running', 'reviewed', 'failed')),
    pull_request JSONB NOT NULL,
    error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS findings (
    review_id UUID NOT NULL REFERENCES review_runs(id) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL,
    payload JSONB NOT NULL,
    PRIMARY KEY (review_id, ordinal)
);
CREATE TABLE IF NOT EXISTS tool_calls (
    review_id UUID NOT NULL REFERENCES review_runs(id) ON DELETE CASCADE,
    call_id TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    payload JSONB NOT NULL,
    PRIMARY KEY (review_id, call_id)
);
CREATE TABLE IF NOT EXISTS executions (
    review_id UUID NOT NULL,
    call_id TEXT NOT NULL,
    payload JSONB NOT NULL,
    PRIMARY KEY (review_id, call_id),
    FOREIGN KEY (review_id, call_id) REFERENCES tool_calls(review_id, call_id) ON DELETE CASCADE
);
