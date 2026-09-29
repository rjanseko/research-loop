## Serper, Brave, and DeepSeek integration study

> This section preserves the 28 September study design and decision rules. Several screens were later replaced by rescout studies and some runs stopped early. For the current decision state and unfinished confirmations, use [the project synthesis](../project-synthesis.md#what-the-completed-studies-actually-establish) and the [study log](../study-log.md); this section is not a new paid-run authorization.

The 28 September 2026 integrations add two web-search choices (`serper` and `brave`) and two
DeepSeek scout candidates (`deepseek-flash` and `deepseek-v4-pro`). Evaluate search and models in
separate studies so each comparison changes one setting. The frozen development cases are st04
(false premise), st05 (structured extraction), st07 (contested synthesis), and drb2-task8 (broad
research). Force standard depth in every arm. Keep the current planner, synthesizer, reading
fallback, prompts, judge, and auditor fixed. The search comparison uses Luna scouts; the model
comparison uses DuckDuckGo. The study runner snapshots all model settings and call limits; verify
each saved run's configuration and prompt fingerprint before comparing it.

### Redesign, 28 September 2026

Two cheaper checks replace parts of the screens below. A paired search replay (`scripts/search_replay.py`)
answered the search screen's availability question: on 40 replayed queries DuckDuckGo found nothing for 18,
Serper and Brave for 4 each. The DeepSeek comparison is now a rescout study,
[`deepseek-rescout-task8.toml`](../../studies/deepseek-rescout-task8.toml): Luna, DeepSeek Flash, and DeepSeek
V4 Pro each rescout the same three stored drb2-task8 plans, and a rescout's claims and research are graded
with `diagnose`, since a rescout writes no report. Only the scout model changes, with no planner or
synthesizer variation. The spec's header holds its decision rule. Run the trimming study
([`trim-history-rescout-task8.toml`](../../studies/trim-history-rescout-task8.toml)) first, because DeepSeek's
cost depends far more on the prompt cache hit rate than Luna's, and report each arm's hit rate.

The search comparison is now a rescout study too, [`search-rescout-task8.toml`](../../studies/search-rescout-task8.toml):
DuckDuckGo, Serper, Brave, and Exa, each alone, rescout the same three plans with Luna@xhigh scouts and trimming
off, graded with `diagnose`. It pins trimming instead of waiting for the trimming study, and it replaces
the search screen's full runs. Both rescout studies run every scout at xhigh, the highest effort.

### Screen and confirmation

The runnable screens are [`search-integrations-screen.toml`](../../studies/search-integrations-screen.toml)
and [`deepseek-integrations-screen.toml`](../../studies/deepseek-integrations-screen.toml). Each runs
one replicate of each arm on all four cases, with the same rubric judge and support auditor. A
screen identifies broken integrations and large directional changes. Its scores are **provisional**:
one run per arm cannot select a default. The spec headers fix their advancement rules before any
paid call. A failed or partial run stays in the denominator. Do not select a winner from an average
that combines the four cases.

Before either paid screen, run its `research study run SPEC --dry` and `--cheap` checks and require
no invariant violations. The cheap mode replaces every model, so it checks the search engines but
does not prove DeepSeek itself can answer a tool call. Smoke-check both DeepSeek model IDs before its screen: set `RESEARCH_MODELS__SCOUT` to each
candidate in separate `research doctor --smoke` invocations under the small-step cap. Log these paid checks and
their run IDs and costs. The current `.env` has the Serper, Brave, and DeepSeek keys but its provider allowlist omits
DeepSeek. The DeepSeek spec explicitly allows the four providers it needs in every arm; use the
same allowlist during its smoke checks. Do not copy key values into a spec or log. The local Postgres `research_loop` stores real study runs; the dry
check uses `research_dry`.

Advance a screen candidate only under its spec's rule. Confirm any advancing candidate against
its baseline with **three replicates per case** and the same four cases, fixed depth, judge, auditor,
and version. The runner rotates the arm order on each replicate and target. Create a new confirmation spec
with its own study label and ceiling estimated from the screen's most expensive comparable run,
then run that exact spec's dry and cheap checks. A DuckDuckGo-then-candidate chain is a separate
search setting and needs its own comparison after the direct-engine result; it must not be credited
to the direct-engine arm. Search caches are namespaced by engine, while page reads can be shared
within a study.

Run **at most two** Luna studies at once (AGENTS.md). The study runner
executes its runs sequentially. Standard depth plans at most four research questions, so a single
run can have up to four simultaneous scouts; scouts on Luna share that run's token pacer. Its
configured rate is 2,000,000 tokens per minute and it adopts a limit OpenAI reports. Separate
study processes have separate pacers, so a third concurrent Luna study
can exceed the account limit even though each process looks safe alone. After each paid screen,
check `research breakdown` for long model spans, 429s, and request timeouts, and Logfire
for explicit pacing waits. Keep affected runs in
the reported denominator; investigate these stops before treating a quality difference as an
integration effect.

### Scorecard and confirmation decision rules

Report each case and replicate, including completion, elapsed time, settled cost, search calls
with results versus empty/error outputs, full pages read and failed fetches, verified and
misattributed quotes, coverage, answer support, rubric points, and support-audit verdicts.
Inspect stored tool messages for search outcomes; the study summary does not yet count empty
searches. Record model tokens, provider 429s, and budget/time stops for the DeepSeek comparison.
Review decisive claims against independent sources when a grade or audit would decide adoption.
Use the same two rubric judges on every confirmation report; the study runner uses the configured
judge first, and a second, separately capped regrade is an explicit follow-up.

| Candidate | Confirmation rule against its baseline |
|---|---|
| Serper or Brave as sole search engine | At least a 25% lower empty/error share among web-search calls across all cases; at least as many full pages read in three of four cases; no extra failed run or blocked-source exposure; supported-audit share no more than 5 percentage points lower; no case loses more than one rubric point on st04/st05 or eight on st07/task8 under either judge. Median added cost must be at most $0.30 per run and median time at most 20% longer. |
| DeepSeek Flash or V4 Pro as scout | No extra failed or partial run, blocked-source exposure, or decisive factual defect; supported-audit share no more than 5 points lower; no case loses more than one rubric point on st04/st05 or eight on st07/task8 under either judge. It must either gain at least eight points on drb2-task8 under both judges with a median cost premium of at most $0.50 per run, or tie case-level quality (no mean loss greater than two points on any case) while cutting median cost by 25% or median time by 20%. |

The eight-point broad-case threshold reflects the variation seen even with three replicates in
the existing drb2-task8 study. Treat a close or conflicting result as **undecided**, not as a
small measured improvement. A candidate meeting its rule is eligible for a held-out confirmation
before changing a default. A candidate that improves reliability or evidence but misses a price
or latency limit can be rated useful for opt-in use without changing the default. Report source
integrity, research quality, completion, cost, and time separately; do not compress them into a
single score.

Historical standard-depth runs in local Postgres cost at most $0.368 on drb2-task8, $0.113 on
st05, and $0.206 on st07. The search screen estimates $0.60 per run plus $0.10 for grading and
audit, with a $9.00 study ceiling; the DeepSeek screen estimates the $0.75 standard-depth run
limit plus $0.10 for evaluation, with an $11.00 ceiling. These are planning ceilings, not spend
approval. Request approval with the hard cap before either paid screen or confirmation.

Historical standard-depth runs took 191–254 seconds on st05, 349 seconds on st07 (one run),
and 454–514 seconds on drb2-task8. Stored st04 runs used quick depth and took 65–70 seconds,
so its standard-depth time is an extrapolation. In the previous six-run study, the sequential
grade-and-audit interval between runs was 100–147 seconds. For twelve serial runs, allow about
1–2 hours for the search screen and 1.5–3 hours for the DeepSeek screen; DeepSeek latency has
not been measured here. Both screens together may take roughly 3–5 hours, including evaluation.
These are wall-time estimates, not deadline guarantees; individual standard runs have a 12-minute
whole-run deadline.

Afterward, `research breakdown` can compare planner, scout, and synthesizer time, token use,
cost, 429s, and stop reasons for every stored run. A separately capped `research diagnose` can
grade the saved research, claims, and final report without rerunning acquisition, attributing
missed rubric points to evidence not found, evidence seen but not claimed, and claims not
reported. That analysis is useful for locating a weak stage, but the search screen alone cannot
identify a causal planner or synthesizer effect. For controlled role comparisons, use `rescout`
on a fixed stored plan to compare scouts and `synthesize` on a fixed ledger to compare
synthesizers. A validator that judges existing records can be checked on these saved runs;
a gap-analysis change that sends scouts back to research needs new paired runs. The standard-depth
screens do not invoke the existing gap-analysis follow-up.
