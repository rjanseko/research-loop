-- Research-loop persistence designed to sit beside existing sessions/runs/requests tables.
-- If those parent tables already exist, add FKs in your application migration as appropriate.

create table if not exists research_jobs (
    id uuid primary key,
    session_id uuid not null,
    root_run_id uuid not null,
    objective text not null,
    status text not null,
    policy_name text not null,
    effective_config jsonb not null default '{}'::jsonb,
    plan jsonb,
    final_report jsonb,
    verification jsonb,
    error jsonb,
    created_at timestamptz not null default now(),
    finished_at timestamptz
);

create table if not exists research_tasks (
    id uuid primary key,
    job_id uuid not null references research_jobs(id) on delete cascade,
    parent_task_id uuid references research_tasks(id),
    role text not null,
    question_id text,
    prompt text not null,
    model_id text not null,
    status text not null,
    attempt integer not null default 0,
    effective_config jsonb not null default '{}'::jsonb,
    output jsonb,
    usage jsonb,
    agent_run_id text,
    conversation_id text,
    error jsonb,
    started_at timestamptz,
    finished_at timestamptz
);

create table if not exists research_tool_events (
    id uuid primary key,
    task_id uuid not null references research_tasks(id) on delete cascade,
    call_index integer not null,
    tool_name text not null,
    tool_call_id text,
    tool_kind text,
    provider_name text,
    args jsonb,
    result jsonb,
    outcome text,
    called_at timestamptz,
    returned_at timestamptz,
    unique (task_id, call_index)
);

create index if not exists research_jobs_root_run_idx on research_jobs(root_run_id);
create index if not exists research_tasks_job_idx on research_tasks(job_id);
create index if not exists research_tasks_question_idx on research_tasks(job_id, question_id);
create index if not exists research_tasks_role_idx on research_tasks(job_id, role);
create index if not exists research_tool_events_task_idx on research_tool_events(task_id);
create index if not exists research_tool_events_name_idx on research_tool_events(tool_name);
