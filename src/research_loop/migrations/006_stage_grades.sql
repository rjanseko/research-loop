-- The rubric judge's verdicts on the earlier stages of a run (diagnose.py): its claims, and everything its
-- synthesizer was shown. Kept apart from `grades`, which holds only report grades, so no report score can
-- take in a stage's. A point met at a stage but not in the report names the step that lost it.
create table if not exists stage_grades (
    id uuid primary key,
    run_id uuid not null references runs(id) on delete cascade,
    case_id text not null,
    -- 'claims' or 'research'.
    view text not null check (view in ('claims', 'research')),
    diagnose_version integer not null,
    judge_model text not null,
    judge_thinking text,
    judge_version integer not null,
    rubric_version text not null,
    status text not null check (status in ('succeeded', 'failed')),
    score numeric,
    points jsonb,
    usage jsonb,
    cost_usd numeric,
    messages jsonb,
    error jsonb,
    budget_cap_usd numeric,
    reserved_usd numeric,
    budget_policy text,
    created_at timestamptz not null default now()
);
create index if not exists stage_grades_run_idx on stage_grades(run_id);
