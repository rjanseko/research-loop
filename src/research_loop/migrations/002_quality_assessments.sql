-- Versioned holistic and source-grounded fact assessments, distinct from historical rubric grades.
create table if not exists quality_assessments (
    id uuid primary key,
    run_id uuid not null references runs(id) on delete cascade,
    case_id text not null,
    packet_version text not null,
    packet_sha256 text not null,
    evaluator_version integer not null,
    judge_model text not null,
    judge_thinking text,
    status text not null check (status in ('succeeded', 'failed')),
    judgment jsonb,
    usage jsonb,
    cost_usd numeric,
    messages jsonb,
    error jsonb,
    created_at timestamptz not null default now()
);
create index if not exists quality_assessments_run_idx on quality_assessments(run_id);
