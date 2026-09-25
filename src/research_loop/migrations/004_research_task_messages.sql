-- Opt-in full transcripts: every request and response of a task, with real tool arguments and
-- results and per-response usage, for analysing a run's cost and research quality. Written only
-- by a repository created with capture_transcripts=True; benchmark runs never write it, so
-- protected benchmark inputs stay out. String values past a size cap are cut (truncated_values).

create table if not exists research_task_messages (
    task_id uuid primary key references research_tasks(id) on delete cascade,
    messages jsonb not null,
    message_count integer not null,
    truncated_values integer not null default 0,
    recorded_at timestamptz not null default now()
);
