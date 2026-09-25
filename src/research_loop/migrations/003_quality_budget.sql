-- Preserve the per-command pre-dispatch cap and conservative reservation on each judge call.
alter table quality_assessments add column if not exists budget_cap_usd numeric;
alter table quality_assessments add column if not exists reserved_usd numeric;
alter table quality_assessments add column if not exists budget_policy text;
