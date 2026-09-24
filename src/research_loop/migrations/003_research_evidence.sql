-- What a finished job's report and verification cite, and what the job left unresolved.
-- Task outputs keep each worker's own claim IDs (c1, c2, ...); only this ledger holds the
-- unique IDs (q1/c1, q1/c1~2, ...) that reports cite. Failed jobs keep the evidence gathered
-- before the failure. review_reasons is empty for a clean result.

alter table research_jobs
    add column if not exists evidence_ledger jsonb,
    add column if not exists review_reasons jsonb;
