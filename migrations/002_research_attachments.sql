-- Stable attachment manifests for reproducible research/benchmark runs.
-- File contents and host filesystem paths are intentionally not stored here.

create table if not exists research_attachments (
    id uuid primary key,
    job_id uuid not null references research_jobs(id) on delete cascade,
    attachment_id text not null,
    name text not null,
    kind text not null,
    media_type text not null,
    sha256 text not null,
    size_bytes bigint not null,
    extractor text not null,
    chunk_count integer not null default 0,
    truncated boolean not null default false,
    requires_multimodal boolean not null default false,
    metadata jsonb not null default '{}'::jsonb,
    extraction_error text,
    created_at timestamptz not null default now(),
    unique (job_id, attachment_id)
);

create index if not exists research_attachments_job_idx on research_attachments(job_id);
create index if not exists research_attachments_sha_idx on research_attachments(sha256);
