# First Scout study design

This is a proposed study, not authorization to run it. It is based solely on the briefing in scout-study-briefing.md, dated 25 September 2026. Model prices, measurements, and implementation descriptions below are supplied by that briefing; they have not been independently verified against the repository or providers. No paid calls or implementation changes were made while preparing this design.

## What the briefing changes

The immediate question is whether the new Scout workflow finishes reliably at its current effort and deadline settings. Its cost and quality have never been measured live. The previous workflow is useful for planning expenditure, but its salvage calls, deep dives, prompts, and ledgers make it an unsuitable control for causal comparisons.

Keep Sol as the reference planner. Earlier planning trials provide no positive evidence for replacing it with Opus or adding a survey before planning. The old merged-Flash result supports testing breadth eventually, but three runs cost about three times one run and improved the rubric from 0.22 to 0.26 on one task. That does not establish that more scouts are the best next purchase.

Defer intake scouting, adaptive overflow, and new long-horizon roles. First measure Flash at max versus high and synthesis on Scout-sized ledgers. A narrow intake classifier is a different intervention from the previously tested landscape survey, but still needs its own justification.

## Spending and interpretation

All amounts are USD. Approve one stage at a time, and stop after each stage. Unspent allowances do not authorize additional work. Judge calls, retries, fallback calls, smoke calls, and failed calls count toward the stage limit.

There are two different quantities:

1. An empirical planning estimate uses the most expensive comparable observed call, including retries. It is not a worst-case guarantee.
2. A hard stage ceiling is a bound enforced before dispatch. A stage may stop without completing its planned sample if it cannot afford the next bounded request.

The existing post-response cost checks cannot guarantee either a per-run or study-wide hard ceiling. Before paid study execution, add a shared reservation mechanism: reserve a conservative maximum request charge, atomically across concurrent scouts, before dispatch. Include the full outgoing input, applicable context price tier, cache writes where relevant, maximum billable output including reasoning, retries, and fallback requests. Release unused reservations after accounting. If output or provider-side automatic retries cannot be bounded, disable those paths or do not dispatch. Do not count expected cache discounts when computing the bound.

Keep this in the current process with durable accounting; no new infrastructure is needed. On restart, unresolved in-flight reservations must remain reserved until reconciled. A cancellation is not evidence that a provider billed nothing.

This guard may refuse a request that the current defaults would send. Label that outcome as a study-budget refusal, not a model failure. Do not silently shrink output limits or change the workflow to make it fit. To observe exact defaults, request an adequately sized reservation first. For example, 32,000 Opus output tokens alone cost $0.64 using the supplied rate, before input or a retry; a $0.40 synthesis allocation is therefore not a strict request bound.

The initial cost model is intentionally conservative:

| Work unit | Empirical maximum from the briefing | Limitation |
|---|---:|---|
| Sol plan | $0.022 | Old prompts. |
| Luna plan | $0.001 | Old prompts. |
| Flash high scout | $0.048 | Only 12 calls, mostly one question. |
| Opus medium synthesis | $1.118 | Large old-design ledgers; retries included in call total. |
| Short-report judge | $0.020 | Observed range ceiling. |
| Deep-report judge | $0.100 | Observed range ceiling. |

A four-scout high-effort run with Sol planning, Opus synthesis, and short grading has an empirical envelope of $0.022 + 4 × $0.048 + $1.118 + $0.020 = $1.352. This is neither an estimate of typical Scout cost nor a bound for Flash at max. No empirical worst case exists for max-effort scouting. Use bounded dispatch for that uncertainty.

## Preparation without paid calls

Build only the study capabilities required by the next approved stage:

- Add role-specific effort configuration and record the effective provider settings. Preserve the current defaults.
- Add a small Pydantic Evals entry point, outside pytest and CI, for stored reports, fixed ledgers, and live runs.
- Port the existing version-2 rubric judge unchanged initially. Freeze its model, high effort, prompt, and validator.
- Add fixed-plan research runs and fixed-ledger synthesis runs. Both must pass through production validation and accounting.
- Implement bounded dispatch and include evaluation charges in study accounting.
- Make dataset cases, rubrics, blocked URLs, prompts, price snapshots, and code revisions immutable within an experiment.
- Check citation and quote evaluators using local fixtures. No provider smoke call belongs in offline preparation.

Preserve runs and run_calls as the authoritative records. Proposed additions are study ID, arm ID, replicate ID, input artifact hash, judge version, cache provenance, reserved charge, and guard-refusal reason. Retain failures and partial output. Do not delete or truncate additional content.

The briefing reserves recording and storage decisions for the user. Before implementing those additions, confirm: may the study use the existing Postgres tables, retain the existing transcript policy, and add the listed study metadata? This is a proposed decision; no storage changes have been made. The current 50,000-character string truncation means exact transcript replay is not always possible. Mark such records ineligible rather than pretending they are complete. Fixed-ledger synthesis can still use an intact stored ledger.

Validate existing answer keys against accessible original sources before paid evaluation. In particular, check st05's assertion about whether the Chinchilla paper reports context length. Do not assume a briefing's expected answer is authoritative. If a frozen case has a wrong key, retain it for provenance, create a corrected version, and exclude the defective version from selection decisions.

## Common evaluation protocol

Use st01 and st03 for shallow behavior, st04 for false-premise handling, st05 for a medium comparison, and st07 only as a contested-topic diagnostic and historical bridge. It is retired as a selection case; do not restore it silently. A deep DRB-II case is a stress test, not a requirement that a six-minute Scout reproduce a full expert report.

Record all planned runs, including failures. Report correctness and coverage alongside completion rate, cost, and elapsed time. Do not compute quality only over successful runs. Show case-level paired differences; rubric points within a report are not independent samples.

One run per cell is screening only. Use two paired repetitions on the most informative cases before a provisional change, and keep the result undecided if repetitions disagree. These sample sizes cannot establish population-level equivalence or reliable p90/p95 latency. The old Flash scores of 0.12–0.22 show meaningful run variation; the stable judge does not remove it.

For provisional adoption, require all of the following:

- No new wrong direct answer or fabricated premise on the included cases.
- No new unresolved citation-integrity defect.
- At most one lost rubric point on each medium-case comparison, with no loss of a fact explicitly required by the question.
- At least a 25% paired reduction in cost, or at least a 20% reduction in elapsed time, or a clearly resolved completion failure.
- The direction of the benefit agrees across the repeated cases.

These are proposed operational thresholds, not statistically established margins. Freeze them before execution. If a result is within one rubric point or depends on one synthesis sample, report it as undecided and repeat only if separately approved.

Use one high-effort Sol judge call per report; manually inspect decisive disagreements. Repeating the generator is more valuable than repeatedly grading the same report. Before adopting Sol as synthesizer on small judged differences, require a blind human review of the decisive points or a separately budgeted cross-vendor judge. Mask model and arm labels.

Run deterministic checks on every output. Exact-answer scoring should retain the historical strict metric and add a versioned, conservative answer-field extraction metric with manual adjudication of misses such as “93 developers.” Do not change old grades in place.

Citation presence, source observation, and quote matching measure traceability. The read label measures access to supporting material, not entailment or correctness. Audit decisive claims against their cited passages. Count unsupported statements as both a number and a fraction, and score required-question coverage so a short answer cannot win by omitting difficult facts. The quote check's order-insensitive ellipsis matching also requires caution; use ordinary ordered passage inspection for decisive quotes.

## Cache and timing controls

Record the first reference run, then use reuse for subsequent live research arms, with a dedicated study cache. Reuse does not freeze the environment: differently phrased queries miss the cache and go live. Log hit rate by tool, new entries, timestamps, and retrieval failures. Alternate execution order on the second repetition.

Use exact stored ledgers for synthesis comparisons and identical stored plans for effort comparisons. These provide stronger isolation than a shared retrieval cache. Do not use strict replay misses to penalize a model's novel queries.

Report cache-served latency separately from end-to-end live latency. Reuse makes retrieval faster, so it cannot by itself establish production deadline performance. Before a speed-based adoption, require a small live confirmation within the same explicitly approved stage allowance or a new approval.

## Sequential experiments

Each stage below is optional after its predecessor. Its ceiling includes grading and retries. The table gives a maximum payable amount if the bounded-dispatch guard is implemented; it does not promise that every intended call will fit.

| Stage | Arms and cases | Initial sample | Empirical planning basis | Hard stage ceiling |
|---|---|---|---|---:|
| A. First live calibration | Current defaults; user-run st07 first, followed by st05 only if useful | One run per case; reuse an already completed st07 rather than rerunning it | Up to $1.352 per high-effort analogue; max-effort scouting unknown | $3.00 |
| B. Scout effort | Flash max versus high; same stored plans for st05 and st07; Luna fixed synthesis | One paired run per case, then repeat only an unresolved case if allowance remains | High arm: at most four × $0.048 per case; max unknown; Luna synthesis must be bounded from actual ledger size | $2.00 |
| C. Synthesis | Opus medium versus Sol high on two intact Scout ledgers | One output per arm per ledger; reuse exact comparable outputs | Opus alone: two × $1.118; Sol on these ledgers unknown | $2.75 |
| D. Shallow behavior and planning | Sol high versus Luna high on st01, st03, st04, st05 | Three stored-input plan generations per model per case | 12 × ($0.022 + $0.001) = $0.276 for plans, before any downstream trials | $0.50 |
| E. Budget shape | 1, 2, 4 scout assignments on st05; two predefined decompositions on one deep case | One screen per configuration; repeats deferred | Flash high: $0.048 per scout plus bounded synthesis and grading | $2.00 |
| F. Deadline remedy | Flash high versus FlashX at the same effort and plan, only if model time causes cutoffs | Two paired repetitions of one failing case | FlashX's claimed speed and price ratio are unvalidated; bound using supplied prices | $1.00 |

These ceilings total $11.25 if every stage were separately approved. That is not the recommended initial commitment. Start with stage A only, and stop if the new workflow cannot reliably produce usable ledgers. A comprehensive model and budget study is not credible for a few dollars. Stages B and C are alternative next purchases based on A, not an obligation to run both immediately.

### Stage A: measure the workflow before optimizing it

The user has reserved the first st07 run. Import and evaluate that result once available. Do not duplicate it. Check current balances through account information where available; a smoke call is paid and requires an allowance. Parse a Z.ai 429's message rather than treating every 429 as either rate limiting or exhausted credit.

Keep all default settings, including Flash max. Record per-role and per-request cost, scout completion, ledger size, synthesis output tokens, and the exact reason each loop stops. After st07, stop for review if records are missing, the guard blocks the intended defaults, or no usable ledger is produced. Run st05 only when it answers a remaining calibration question.

Grade st05 with its validated rubric; grade st07 for continuity only. Do not compare old and new scores as a controlled architecture experiment. An output can be complete yet shallow, or partial yet contain useful correct evidence.

Inspect deadline semantics in code before interpreting them: determine whether research_seconds is measured from run start or scout start, whether queued time counts, and whether the whole-run deadline includes planning. The apparent 90-second difference between 360 and 270 is not automatically usable synthesis time. Old Opus synthesis reached 226 seconds and had a 110-second median.

Deliver an observed cost decomposition, elapsed-time decomposition, failure reasons, and current guard bound. With two questions, explicitly leave shallow/deep cost distributions and tail probabilities unanswered.

### Stage B: test effort independently of planning

Hold questions, plan, per-scout budgets, concurrency, tool implementation, synthesis prompt/model, and grader fixed. Change only Flash effort. If no usable max baseline exists, run both arms; otherwise reuse it only when its settings and artifacts match exactly.

Use a fixed Luna synthesizer as a low-cost measurement instrument, not as an adopted production choice. Grade report coverage and inspect the scout evidence separately: a weak synthesizer may hide a scout improvement. If a decision turns on missing analysis, rerender only the decisive pair with the same stronger synthesizer, within a new approved allowance if needed.

Adopt high provisionally only under the common decision rule. If max misses deadlines and high produces useful claims on both cases, that is a practical reason to prefer high even without a tiny rubric advantage. One timed-out max run is enough to stop an expensive screen, not enough to estimate a timeout rate.

### Stage C: isolate synthesis cost and variation

Use one st05 ledger and one st07 ledger, each identical across arms. Measure semantic correctness, coverage, citation integrity, read fraction, cost, and latency. Enforce each production retry policy identically and retain retry costs.

Start with Opus and Sol only. Defer GLM-5.3 high and Flash high until neither initial candidate offers an acceptable cost/quality/deadline trade-off. Flash max is already a poor deadline candidate from the supplied evidence. Do not pay to reconfirm that first.

The $2.75 ceiling is tight even under the old Opus maxima. Preflight actual ledger sizes and request bounds; reuse existing exact-input Opus output where possible. If insufficient funds remain for the comparison, stop and report the missing cell. Never call the comparison complete based on cheaper successful outputs alone.

Repeat synthesis on the decisive ledger before adopting a close result. That confirmation is not automatically funded beyond the ceiling. If no model wins clearly, retain the default and report the trade-off rather than conducting a broad tournament.

### Stage D: test planning cheaply

Blindly inspect plans for required entities, omitted constraints, duplicate questions, inappropriate splitting of a one-fact request, and total scout count. Use deterministic entity coverage where appropriate and human review, not word overlap.

Three planning repeats per case characterize decomposition instability cheaply; they do not establish end-to-end equivalence. Only distinct plans that change coverage or scout count need downstream trials, using the selected fixed research and synthesis settings. The $0.50 ceiling may cover planning only. Report downstream validation as deferred if it does not fit.

Adopt Luna only after useful downstream checks. The planner's own savings may be outweighed by one unnecessary scout or a missing subject. Do not include Opus or an intake survey in this first comparison without a new hypothesis.

### Stage E: study breadth and depth without confusing them

For st05, use explicit, coverage-equivalent partitions: all three models in one assignment; two groups covering the same three models; and four assignments that divide factual extraction and source reconciliation without adding new requested content. Report any coordination burden introduced by that fourth assignment.

For task2+, compare four grouped-country assignments against seven single-country assignments, with concurrency held at four and identical total research dollars. Preserve benchmark blocked URLs. Treat this as a deep-task stress test and report partial coverage explicitly. Seven scouts in two waves may miss the shared deadline even if they are individually cheap.

Hold total research money, total productive-call allowance, and synthesis limits fixed in the primary breadth comparison; document integer budget allocation in advance. Compare 8 versus 12 requests per scout separately, after selecting a decomposition, while holding other limits fixed. These are two experiments, not one combined arm.

A fixed-total-budget comparison measures allocation efficiency. A fixed-per-scout comparison spends more and measures return on additional expenditure; defer it. Eight scouts and adaptive overflow are also deferred. No gap-analysis role is added.

Use cost per correctly supported required fact, answer coverage, total cost including synthesis, and failure count. More sources or claims alone do not win. A cut-at-request-k replay is unnecessary initially: source discovery curves can be computed from existing messages, but cannot predict whether the model would have returned a valid final result with a shorter budget. Test shorter budgets prospectively when needed.

### Stage F: investigate speed only when it is the bottleneck

Separate model wait, tool time, queue time, and finalization time. Test FlashX only after evidence indicates model latency dominates failures. Do not assume shared weights or equal quality.

Hold effort, assignments, and budgets constant. Require useful completion improvement or at least a 20% latency reduction under the common quality gates. Measure cost per successful run as well as cost per call. A provider speed claim alone does not justify replacement. Confirm the decisive timing result with live retrieval.

## Retrieval and regression reporting

Retrieval measurement is passive across all paid stages; it needs no separate model calls. Report empty searches, provider failures, 403/404s, guessed URLs, cache hits, repeated failures, and useful retrievals by tool. Tool time is directly attributable; model tokens spent deciding what to do after failures are generally not exactly attributable. Report associations rather than inventing precise wasted-dollar figures.

Initially track these regressions:

| Measure | How it should affect a decision |
|---|---|
| Direct answer and false-premise correctness | A new error blocks adoption on the tested case. |
| Rubric coverage | Report by case and required fact; do not average unrelated rubrics without showing the components. |
| Citation integrity | Block new unresolved unknown claims or detached citations. |
| Semantic support | Audit decisive statements against evidence; access labels alone cannot pass this gate. |
| Read share and quote verification | Diagnostic rates with denominators, not stand-alone quality scores. |
| Cost and duration | Include failures, retries, judging, and reservations separately from settled spend. |
| Completion and budget exhaustion | Record every scheduled arm; report missing outputs explicitly. |
| Retrieval availability | Explain environment-related failures and potential cache confounding. |

Use the observed maximum only as an observed maximum. Do not label it a worst-case cost or derive p95 from a handful of runs. After the first stages, update estimates using the greater of comparable historical maxima and new maxima; seek approval if a later stage needs a larger allowance.

## The next decision

Prepare the offline harness and bounded-dispatch design, confirm the proposed recording additions, then review the user's first st07 result. Approve at most the $3.00 stage-A ceiling, inclusive of any st07 spend assigned to this study. If st07 has already consumed part of that ceiling, subtract it.

The first deliverable should be one reliable Scout cost breakdown and a clear account of where it fails. It is acceptable for the first study to conclude that model selection, intake scouting, overflow, or deep-task suitability remain undecided.
