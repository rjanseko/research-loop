-- The passage-evidence rebuild's storage (docs/passage-evidence-plan.md, build step 2).

-- One scout or deep dive's result, stored as soon as its call ends, so a run cut short keeps the research that
-- finished. `id` is the attempt's own, so writing it again is harmless.
create table if not exists research_results (
    id uuid primary key,
    run_id uuid not null references runs(id) on delete cascade,
    -- Null when the attempt was cancelled before its call started.
    call_id uuid references run_calls(id) on delete set null,
    question_id text not null,
    -- 'scout' or 'deep_dive'.
    role text not null,
    -- 'returned' when the model returned a result, 'cut_off' when a limit, deadline, or error stopped it.
    status text not null check (status in ('returned', 'cut_off')),
    cut_off text,
    result jsonb not null,
    created_at timestamptz not null default now()
);
create index if not exists research_results_run_idx on research_results(run_id);

-- Text a research tool returned, stored once by its SHA-256 however many runs saw it: a search result's title
-- and snippet, a scholarly record's title and abstract, or a fetched document's whole extracted text.
create table if not exists texts (
    sha256 text primary key,
    body text not null,
    chars integer not null
);

-- One thing a tool returned in one call: a search result, a scholarly record, or a window of a document.
create table if not exists observations (
    id uuid primary key,
    run_id uuid not null references runs(id) on delete cascade,
    call_id uuid not null references run_calls(id) on delete cascade,
    question_id text,
    -- The call's own handle for it, such as r7; unique within the call.
    handle text not null,
    tool text not null,
    args jsonb not null default '{}'::jsonb,
    status text not null,
    error jsonb,
    -- snippet, metadata, abstract, or document.
    access text,
    url text,
    final_url text,
    -- DOI, arXiv ID, and OpenAlex ID, from the tool's record only.
    work jsonb,
    -- What the tool returned about the source: title, authors, date, venue, status, extraction method.
    metadata jsonb,
    text_sha256 text references texts(sha256),
    window_start integer,
    window_end integer,
    -- Page start offsets, pages extracted, and pages in the file, when known.
    pages jsonb,
    observed_at timestamptz not null,
    unique (call_id, handle)
);
create index if not exists observations_run_idx on observations(run_id);

-- A span of a stored text, as the passage splitter found it (passages.py). Its ID digests the text's hash and
-- the offsets, so the same span has the same ID in every run.
create table if not exists passages (
    id text primary key,
    text_sha256 text not null references texts(sha256),
    splitter_version integer not null,
    ordinal integer not null,
    start_char integer not null,
    end_char integer not null,
    page integer,
    kind text not null,
    header_start integer,
    header_end integer
);
create index if not exists passages_text_idx on passages(text_sha256);
