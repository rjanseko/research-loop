-- Baseline for the Scout design (2026-09). The first design's schema, migrations 001 to 005, was
-- archived with its data in ~/research-loop-archive/2026-09-25/; its README explains how to restore it.

-- One research run: its question, configuration, results, and outcome.
create table if not exists runs (
    id uuid primary key,
    -- A study's question runs point at the study run (Long-horizon); null for a standalone run.
    parent_run_id uuid references runs(id) on delete set null,
    mode text not null,
    workflow_version text not null,
    question text not null,
    -- A digest of the question, notes, and blocked sources, so runs given the same input can be paired.
    input_hash text,
    -- Where the run sits in a study: its arm and repetition. Null for a run outside a study.
    study_id text,
    arm text,
    replicate integer,
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
    -- The cache mode and, by provider, the lookups the research tools served, missed, and wrote.
    cache jsonb,
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
    -- Why the call stopped: a spent budget, a deadline, a limit, a provider error, or returning a result.
    stop_reason text,
    -- Seconds a scout's research tools ran, overlapping calls counted once; the rest of the call was the model.
    tool_seconds numeric,
    started_at timestamptz not null default now(),
    finished_at timestamptz
);

-- One grade of a run's report by a rubric judge, with the judge's own call and what it cost.
create table if not exists grades (
    id uuid primary key,
    run_id uuid not null references runs(id) on delete cascade,
    case_id text not null,
    -- The judge's model, effort, and version, and the rubric's version; scores compare only when these match.
    judge_model text not null,
    judge_thinking text,
    judge_version integer not null,
    rubric_version text not null,
    status text not null check (status in ('succeeded', 'failed')),
    score numeric,
    -- A verdict for every rubric point, by category and number.
    points jsonb,
    usage jsonb,
    cost_usd numeric,
    messages jsonb,
    error jsonb,
    created_at timestamptz not null default now()
);

create index if not exists runs_parent_idx on runs(parent_run_id);
create index if not exists runs_started_idx on runs(started_at);
create index if not exists runs_study_idx on runs(study_id) where study_id is not null;
create index if not exists run_calls_run_idx on run_calls(run_id);
create index if not exists grades_run_idx on grades(run_id);
