-- Analyse one research job. Pass the job with -v job=<uuid>, for example, from the repository root:
--   docker compose exec -T postgres psql -U research -d research_loop -v job=<job_id> < scripts/analyze_run.sql
-- Sections 1 and 6 work on any stored job; sections 2 to 5 need a run made with --capture (research_task_messages).
\pset pager off

-- A tool call's arguments as JSON: models send an object or a JSON string. Malformed or cut
-- strings give null rather than stopping the section. Lives only for this session.
create function pg_temp.tool_args(args jsonb) returns jsonb language sql immutable as $$
    select case jsonb_typeof(args)
               when 'object' then args
               when 'string' then case when pg_input_is_valid(args #>> '{}', 'jsonb') then (args #>> '{}')::jsonb end
           end
$$;
\x off

\echo '== 1. Cost, tokens, and time by role and model'
select t.role, t.model_id, count(*) as calls,
       round(sum((t.usage->>'cost')::numeric), 4) as cost_usd,
       sum((t.usage->>'input_tokens')::bigint) as input_tokens,
       sum((t.usage->>'output_tokens')::bigint) as output_tokens,
       sum((t.usage->>'requests')::int) as requests,
       sum((t.usage->>'tool_calls')::int) as tool_calls,
       max(t.finished_at - t.started_at) as longest
  from research_tasks t
 where t.job_id = :'job'
 group by 1, 2
 order by cost_usd desc nulls last;

\echo '== 2. Context growth: input tokens of each request, per research task'
select t.role, t.question_id, t.attempt, r.n as request,
       (r.msg->'usage'->>'input_tokens')::int as input_tokens,
       (r.msg->'usage'->>'output_tokens')::int as output_tokens
  from research_tasks t
  join research_task_messages m on m.task_id = t.id,
       lateral (select e.value as msg, row_number() over (order by e.ordinality) as n
                  from jsonb_array_elements(m.messages) with ordinality e
                 where e.value->>'kind' = 'response') r
 where t.job_id = :'job' and t.role in ('scout', 'deep_dive')
 order by t.started_at, r.n;

\echo '== 3. Every search and fetch, in order, with its real argument'
with calls as (
    select t.id as task_id, t.role, t.question_id, t.started_at, e.ordinality as step,
           p.value->>'tool_name' as tool,
           pg_temp.tool_args(p.value->'args') as args, p.value->>'args' as raw_args
      from research_tasks t
      join research_task_messages m on m.task_id = t.id,
           jsonb_array_elements(m.messages) with ordinality e,
           jsonb_array_elements(e.value->'parts') p
     where t.job_id = :'job' and p.value->>'part_kind' = 'tool-call'
)
select role, question_id, step, tool,
       coalesce(args->>'query', args->>'url', args->>'identifier', args::text, 'unparsed: ' || left(raw_args, 80)) as argument
  from calls
 order by started_at, step;

\echo '== 4. Repeated calls: the same search or URL more than once in the job'
with calls as (
    select t.role, p.value->>'tool_name' as tool,
           pg_temp.tool_args(p.value->'args') as args
      from research_tasks t
      join research_task_messages m on m.task_id = t.id,
           jsonb_array_elements(m.messages) e,
           jsonb_array_elements(e.value->'parts') p
     where t.job_id = :'job' and p.value->>'part_kind' = 'tool-call'
)
select tool, coalesce(args->>'query', args->>'url', args->>'identifier') as argument,
       count(*) as times, string_agg(distinct role, ', ') as roles
  from calls
 where coalesce(args->>'query', args->>'url', args->>'identifier') is not null
 group by 1, 2
having count(*) > 1
 order by times desc;

\echo '== 5. Fetch yield: fetched URLs, and whether any evidence in the ledger cites them'
with fetched as (
    select distinct t.role,
           rtrim(pg_temp.tool_args(p.value->'args')->>'url', '/') as url
      from research_tasks t
      join research_task_messages m on m.task_id = t.id,
           jsonb_array_elements(m.messages) e,
           jsonb_array_elements(e.value->'parts') p
     where t.job_id = :'job' and p.value->>'part_kind' = 'tool-call'
       and p.value->>'tool_name' in ('web_fetch', 'scholar_fetch')
       and pg_temp.tool_args(p.value->'args')->>'url' is not null
),
cited as (
    select distinct rtrim(ev.value->'source'->>'url', '/') as url
      from research_jobs j,
           jsonb_each(j.evidence_ledger) q,
           jsonb_array_elements(q.value) res,
           jsonb_array_elements(res.value->'claims') c,
           jsonb_array_elements(c.value->'evidence') ev
     where j.id = :'job'
)
select f.role, f.url, (c.url is not null) as cited
  from fetched f left join cited c on c.url = f.url
 order by cited, f.role, f.url;

\echo '== 6. Verifier checks: supported or not, by severity'
select ck.value->>'severity' as severity, (ck.value->>'supported')::boolean as supported, count(*) as checks
  from research_jobs j, jsonb_array_elements(j.verification->'checks') ck
 where j.id = :'job'
 group by 1, 2
 order by 1, 2;
