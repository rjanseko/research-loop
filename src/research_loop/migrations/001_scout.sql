-- Baseline for the Scout design (2026-09). The first design's schema, migrations 001 to 005, was
-- archived with its data; see docs/setup.md for restoring it.

-- One research run: its question, configuration, results, and outcome.
create table if not exists runs (
    id uuid primary key,
    -- A study's question runs point at the study run (Long-horizon); null for a standalone run.
    parent_run_id uuid references runs(id) on delete set null,
    mode text not null,
    workflow_version text not null,
    question text not null,
    status text not null check (status in ('running', 'complete', 'partial', 'failed', 'cancelled')),
    -- Models, limits, prompt fingerprint, evidence version, and git commit the run used.
    config jsonb not null default '{}'::jsonb,
    plan jsonb,
    report jsonb,
    ledger jsonb,
    -- Deterministic checks of the finished run: citation problems, support per statement,
    -- questions left unanswered, unreachable sources, and review reasons.
    checks jsonb,
    cost_usd numeric,
    usage jsonb,
    error jsonb,
    trace_id text,
    started_at timestamptz not null default now(),
    finished_at timestamptz
);

-- One agent call within a run, with what it cost and its full messages.
create table if not exists run_calls (
    id uuid primary key,
    run_id uuid not null references runs(id) on delete cascade,
    role text not null,
    question_id text,
    model text not null,
    status text not null check (status in ('running', 'succeeded', 'failed', 'cancelled')),
    usage jsonb,
    cost_usd numeric,
    output jsonb,
    messages jsonb,
    error jsonb,
    started_at timestamptz not null default now(),
    finished_at timestamptz
);

create index if not exists runs_parent_idx on runs(parent_run_id);
create index if not exists runs_started_idx on runs(started_at);
create index if not exists run_calls_run_idx on run_calls(run_id);
