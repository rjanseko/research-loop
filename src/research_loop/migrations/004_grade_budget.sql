-- Keep the enforced pre-dispatch ceiling and conservative reservation with each rubric grade.
alter table grades add column if not exists budget_cap_usd numeric;
alter table grades add column if not exists reserved_usd numeric;
alter table grades add column if not exists budget_policy text;
