-- Whether the verified quotes behind a report's statements say what the statements say (audit.py).
-- One row per audit of one run; `verdicts` holds one entry per report statement.
create table if not exists support_audits (
    id uuid primary key,
    run_id uuid not null references runs(id) on delete cascade,
    audit_version integer not null,
    evidence_version integer,
    judge_model text not null,
    judge_thinking text,
    status text not null check (status in ('succeeded', 'failed')),
    verdicts jsonb,
    counts jsonb,
    usage jsonb,
    cost_usd numeric,
    messages jsonb,
    error jsonb,
    budget_cap_usd numeric,
    reserved_usd numeric,
    budget_policy text,
    created_at timestamptz not null default now()
);
create index if not exists support_audits_run_idx on support_audits(run_id);
