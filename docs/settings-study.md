# Settings study: search depth, response size, and models per role

Status: designed 2026-09-25, to be run in steps. Step 0, the free one, is done; step 1 needs the study runner and replay command first. Results and plan changes go in the [Decision log](#decision-log).

The presets' limits were set by hand and checked against one calibration question. This study measures what those limits cost and buy, so they can become rules: how deep a scout or deep dive should search, how many questions a plan should have, how much a result and a report should say, and which model each role should run on. It is meant to cost as little as possible, so it runs every expensive research loop once, gets the other settings from that run, and runs in steps so each one's results decide what the next one spends.

## What it decides

| Setting | Where it lives now | What the study measures |
|---|---|---|
| Scout depth | route `max_requests`, `max_tool_calls`, `total_tokens_limit` | The request at which each source a scout ends up citing first appeared, and how good its answer is when cut off earlier |
| Deep-dive depth | the same, on the deep-dive route | The same, for deep dives |
| Questions per plan | `planner_question_range` | How many planned questions add evidence the report uses |
| Deep dives and verification rounds | `ResearchConfig.max_deep_dives_per_round`, `max_verification_rounds` | Whether a deep dive or a second verification round changes the report's score |
| Result size | the scout and deep-dive prompts; no limit in code | Claims per result against the share of them the report cites |
| Report size | synthesizer `max_tokens`; no length guidance | Report length against rubric score and supported-claim rate |
| Model per role | policy routes | Score, cost, and failure modes of each model on the same inputs |

## The questions

`examples/settings_study.toml` is the suite: five questions of our own in `examples/settings_study_cases.jsonl`, and four DeepResearch Bench II tasks. They range from facts a model knows without searching to reports that need many sources, because the right depth depends on the question. A depth rule that suits one kind of question and wastes money on another shows up only when both are in the set.

| ID | Question type | Scored by | Expected depth |
|---|---|---|---|
| `st01-transformer-venue` | One fact a model knows from training | Exact answer | Shallow |
| `st02-resnet-author` | Two hops | Exact answer | Shallow |
| `st03-swebv-annotators` | A number from one primary source | Exact answer | Medium |
| `st04-ilsvrc-captioning` | A false premise: the track never existed | Rubric (4 points) | Medium |
| `st05-scaling-table` | Numbers from three papers | Rubric (11 points) | Medium |
| DRB-II `task2+` | Finance & Business report | Expert rubric (72 points) | Deep |
| DRB-II `task8` | Science & Technology report | Expert rubric (52 points) | Deep |
| DRB-II `task17+` | Software Development report | Expert rubric (75 points) | Deep |
| DRB-II `task26` | Health report | Expert rubric (71 points) | Deep |

The first three have short reference answers that `ReferenceAnswerMatch` scores without a judge. The others have rubrics: lists of points a good answer contains. `st01` measures over-searching, since a good run should stop almost at once. `st04` measures whether a run can conclude that something does not exist rather than searching until its limits stop it.

**The deep questions come from DeepResearch Bench II** ([benchmarks.md](benchmarks.md)), whose rubrics experts wrote, one fact per point, with a median of 80 characters a point. The four are English, since DuckDuckGo search, quote matching, and the judge are untested on Chinese, and one each from DRB-II's four largest English themes. Within a theme, the rule picks the lowest-`idx` CC BY 4.0 task with 50 to 80 rubric points, the middle half of English tasks by size, so no one chose them by reading them. Each keeps its blocked URLs, the expert reports its rubric came from, which the tools refuse. Our judge's rubric scores are not the official DRB-II scores. The study stores DRB-II runs' transcripts and recorded tool calls locally, in Postgres and the study cache, as it does for its own questions; they are never committed. That was decided on 25 September 2026: DRB-II is public and not encrypted, unlike BrowseComp, whose questions the study never stores.

`st06` and `st07` were the first deep questions, with rubrics we wrote. They are marked `retired` in their metadata: the runner skips them unless named, and they stay in the file, unchanged, so earlier runs of them can still be graded. The DRB-II tasks replaced them after step 1 showed how our own rubric wording decided scores.

**Rubric points state one fact each, with nothing incidental.** A judge reads every word of a point as required: in step 1 it marked "OpenAI stopped reporting SWE-bench Verified scores (announced February 2026)" unmet in a report that said OpenAI stopped reporting them but gave no date. A detail that matters, such as a date, is its own point. The `st04` and `st05` rubrics follow this rule; DRB-II's already do.

`ReferenceAnswerMatch` compares the whole normalized answer, so "93 developers" or "NeurIPS (then NIPS) 2017" scores 0 even though it is right. The case asks for a succinct `Exact Answer:` line, which makes this rare. Read every miss by hand before counting it.

The `st05` rubric was checked against primary sources in step 0; see the [Decision log](#decision-log). The Chinchilla paper gives no context length, so that point asks the report to say so.

## Models

These are the models the configured keys can reach, with list prices in USD per million tokens. The OpenAI and Anthropic prices and `glm-5.3` come from `genai-prices`. The two Flash prices come from [Z.ai's pricing page](https://docs.z.ai/guides/overview/pricing), checked on 25 September 2026:

| Model | Input | Cached input | Output | Tried for |
|---|---:|---:|---:|---|
| `openai:gpt-6-luna` | 0.10 | 0.01 | 0.50 | Scout, deep dive, planner, synthesizer; the fixed synthesizer |
| `zai:glm-5.3-flash` | 0.15 | 0.03 | 0.50 | Scout, deep dive, gap analyst (the current scout, from the eighth pilot on) |
| `zai:glm-5.3-flashx` | 0.37 | 0.075 | 1.25 | Scout, in the [FlashX test](#the-flashx-test) |
| `zai:glm-5.3` | 1.40 | 0.26 | 4.40 | Every role (the current synthesizer at `max` effort, from the eighth pilot on, and alternate deep dive; the scout until the seventh) |
| `openai:gpt-6-sol` | 2.00 | 0.20 | 10.00 | Every role (the current planner, gap analyst, deep dive, and verifier) |
| `anthropic:claude-opus-5-5` | 4.00 | 0.20 | 20.00 | Synthesizer from the fifth to the seventh pilot, and planner in the fifth and sixth |
| `openai:gpt-6-astra` | 10.00 | 1.00 | 50.00 | Synthesizer only, on three questions |

`genai-prices` is up to date: its latest data matches the 0.1.8 release the repository pins. But it prices `glm-5.3-flash` at 0.075, 0.015, and 0.25, half of Z.ai's list price, and has no entry for `glm-5.3-flashx`. The 0.1.9 release does not change either. Without a correction, a cap on a Flash route would let through about twice the spend it names, and a FlashX route would have no cost at all, so no cap could hold on it. `src/research_loop/prices.toml` now supplies both prices (see [setup.md](setup.md#model-routing)), so the caps hold.

The OpenAI prices above are the short-context ones. `genai-prices` also has a long-context tier for the OpenAI models, at about twice these prices: a request with 100,000 input tokens is charged at the prices above, and one with 1,000,000 input tokens at the higher tier. Check which tier applies when estimating a replay that sends a long transcript.

The GPT-5.6 models are the previous generation, and the run already recorded on GPT-5.6 Sol serves as their baseline.

## Method

Three techniques keep the cost down.

**One generous run, many depths.** Scouts and deep dives are not told their request or tool-call limits. The limits stop them from outside. So a run cut off at request *k* behaves, up to request *k*, exactly like the first *k* requests of a longer run. Each question is run once with generous limits, with transcripts captured, and a shorter limit is then studied by cutting the transcript. The free version of this reads the transcript and records at which request each source the result cites was first fetched or seen. The paid version runs the salvage call, the tool-free wrap-up that a capped run makes, on the first *k* requests only. One salvage call costs a few cents, where a separate run for each depth would cost a whole loop.

A task that stopped on its own limit in the generous run never shows where more searching stops paying off. Its curve is marked as cut off, and it counts only as a lower bound on the depth its question needs.

**Replaying one role.** A captured transcript holds each call's exact prompt. A replay sends the same prompt to another model with the role's own agent, instructions, and output schema, and changes nothing else. Comparing synthesizers means sending one ledger to four models, which costs four single calls. Comparing scouts or deep dives this way runs only that loop again, with its tools, while the plan and the gaps stay fixed.

**Recorded tools.** The reference runs use the `record` cache mode, which stores every search, fetch window, and scholarly response they make without reading older entries. Replays that call tools use `reuse`: a search or fetch the reference run made is served exactly as recorded, and only a call it never made, such as a query the new model words differently, goes to the live web and is recorded in turn. Two replays that issue the same calls therefore see the same results, so differences between models come from the models and not from the web changing between runs. Every search and fetch records whether it was served from the recording, and section 7 of `scripts/analyze_run.sql` reports the share for each role and tool. That share is how far a replay's comparison is one of models on the same web: a replay that got most of its calls from the recording compares models, while one that got few compares the web on two days as much as the models, and its result is reported with that share. The cache is shared by every run that uses the same cache directory, so the study sets `RESEARCH_BENCHMARK_CACHE` to a directory of its own under `benchmark_outputs/`. The recording then holds the reference runs' calls and the replays' additions, and nothing from unrelated runs. See [acquisition.md](acquisition.md#caching).

**Grading research through a fixed synthesizer.** The reference answers and rubrics are for a whole question. A scout or deep dive answers one planned sub-question, which has no reference, so a single research result cannot be scored against them. Depth and research-model variants are therefore graded through a report. Every research task of a question is cut at the same *k*, or replayed on the same model, and one fixed synthesizer writes a report from those results. The fixed synthesizer is `openai:gpt-6-luna`, one of the cheapest models in the study and already a synthesizer candidate; it stays the same for every variant, so differences between reports come from the research. Its scores are only for comparing one variant with another, not with the synthesizers compared in step 7.

**One fixed grader.** Short answers are scored by exact match. Rubrics are scored by one judge model and prompt that never change once variants are being graded, because the run's own verifier changes whenever the verifier route does. The judge is `openai:gpt-6-sol` at high effort (low effort missed plainly stated points in step 1), and its instructions ask only whether each rubric point is present and correct. It reads the report as a reader does: the answer, its key statements and caveats, and its Sources list, without the verifier's verdicts (`JUDGE_VERSION` 2). The first version read only the answer and sources, and in step 1 it marked unmet a point the report stated as a key statement. Two checks, both made in step 3 before any variant is graded, make its scores usable:

- **Its own variation.** Each reference report is graded three times. The spread of those scores is the judge's variation, which every decision rule below uses.
- **Its vendor.** `gpt-6-sol` is also one of the synthesizers compared in step 7, and judges tend to favour their own output. A second judge from another vendor, `anthropic:claude-opus-5-5` at high effort, grades the same reports with the same prompt. Without an Anthropic key, as in the current configuration, it is `zai:glm-5.3`, which is also a synthesizer candidate; requiring the two judges to agree limits either one's bias toward its own vendor. Report how often the two agree. In step 7, both judges grade every report, and a result counts only where they agree.

## Running it in steps

The study runs as a sequence of steps, each of which is small enough to read before the next one is planned in detail. Only the next step is fixed at any time; later ones are outlines, and each step's results can change them. This spends money only where earlier data left a question open, and it lets each step start from what the previous ones established.

Four rules make this adaptive without letting it drift:

- **Paired comparisons.** Every variant is compared with the reference run on the same questions and, where it can be, the same prompts, and the difference is taken question by question. This removes most of the variation between questions, which across questions this different is usually larger than the variation between models.
- **Decide, then stop.** After each step, every comparison it made is written down as better, worse, within the judge's variation, or undecided. Decided comparisons are not run again. Undecided ones get more samples, first by repeating a replay on the questions where the difference was largest, then by adding questions.
- **Questions are added, never edited.** A question that has been run keeps its wording, answers, and rubric, so earlier results stay comparable. When a step shows a need for a different question, such as one that targets a failure mode it found, the new question gets a new ID in `examples/settings_study_cases.jsonl` before it runs. A question on which every variant scores the same, at the top or bottom of the scale, is dropped from later replays, since it cannot tell variants apart. Rubrics can be corrected only in step 3, before any variant is graded against them.
- **Every change is logged.** Each step ends with an entry in the [Decision log](#decision-log): what ran, what it cost, what it found, and what it changed in the later steps. A later reader should be able to tell which plan any result was produced under.

| Step | Question it answers | What runs | Cap |
|---|---|---|---:|
| 0. Free | Is the tooling ready, and what does the recorded run already show? | Synthetic runs of the suite; depth curves and the model-versus-tool time split of the recorded README run; primary sources for the `st05` and `st07` rubric points | $0 |
| 1. Pilot | What does one deep question cost on `value` with generous limits, and does capture and replay work end to end? | DRB-II `task2+` once on `value` with generous limits (scouts 24 requests, 48 tool calls, and 2,000,000 tokens; deep dives 12 requests, 80 tool calls, and 2,000,000 tokens), transcripts captured, every tool call recorded (`record` cache mode), $5 cap with $1 held back. A first pilot ran `st07` at 400,000 tokens; see the [Decision log](#decision-log) | $5 |
| 2. Reference runs | What does each question need, and where does the time go? | The other eight questions, the same way; free depth curves, stop reasons, and time splits for every research task | Set by step 1; about $15 |
| 3. Judge | How much can a score difference be trusted? | Each reference report graded three times by each judge; rubric points the judges disagree on are reworded or dropped | $1 |
| 4. Depth | How deep should scouts and deep dives search? | Salvage replays of every research task at the cut points step 2 suggests, one fixed-synthesizer report per question and depth | $5 |
| 5. Research models | Can cheaper models scout and deep-dive? | Scouts replayed on `glm-5.3-flash` and `gpt-6-luna`, deep dives on `glm-5.3` and `gpt-6-luna`, at the depth step 4 chose, one fixed-synthesizer report per variant. Only if steps 1 and 2 show deep dives getting worse late in long loops: two or three deep dives replayed on `gpt-6-astra` too, as an upper bound, about $10 to $15 more | $3 |
| 6. FlashX | Is FlashX the same model as Flash, and is its speed worth 2.5 times the price here? | See [The FlashX test](#the-flashx-test) | $1.50 |
| 7. Finishing models | Which planner and synthesizer? | Planner on four models, synthesizer on `glm-5.3`, `gpt-6-sol`, and `gpt-6-luna` against the reference's Opus 5.5 for every question, plus `gpt-6-astra` on three, all on the step 2 ledgers, graded by both judges | $5 |
| 8. Check | Do the chosen settings hold up in a normal run? | All questions once with the chosen settings | About step 2's cost |
| 9. BrowseComp | Do the chosen settings beat the current preset on hard retrieval, on questions the study never saw? | Only if the study chooses settings worth confirming: the chosen settings and the current preset on 50 to 100 BrowseComp cases with `research-bench`, in memory, with no transcripts or recorded calls, as BrowseComp's encrypted questions require. Needs three changes to `research-bench`, none built yet: a per-case cap (`--job-budget`), since it caps only single calls; salvage on, since its benchmark settings leave `salvage_exhausted_research` off, so a loop that reaches a limit fails the whole case instead of answering from what it found; and a way to pass it the chosen settings | About $150 to $300 |

Steps 0 to 7 cap at about $36, but that is a ceiling, not an estimate: an adaptive plan usually skips work. The conditional Astra comparison and steps 8 and 9 come on top. The first pilot cost $2.24 at 400,000 tokens, where its loops were cut short, so the rerun at the new limits measures what a deep question really costs and replaces the estimate for steps 2 and 8.

The token limit is set high on purpose. PydanticAI counts every request's input, cached or not, plus its output, summed over the loop, and each request resends the loop's history, so the count grows with the square of the requests. The first pilot ran at 400,000 tokens, which stopped two scouts and both deep dives at 10 to 17 requests, well before their request limits, with 79 to 88% of their input read from cache. At 2,000,000 the request and tool-call limits bind: a scout at 24 requests needs about 900,000, and a deep dive at 12 about 600,000 to 900,000. Cost stays bounded by the dollar caps. Deep dives get 12 requests rather than the preset's 20 because the first pilot's had every source they cited by request 3. `scripts/depth_profile.py` names the limit that stopped each loop, so a limit binding that should not is seen at once rather than found by hand, as it was in the first pilot.

The $5 per-question cap, with $1 held for synthesis and verification, is there to catch a runaway run, not to shape the result. The first pilot's planning, synthesis, and verification cost $0.30 to $0.60, so $1 is enough to hold back, and research gets $4, above a deep question's projected $3.50. A run that reaches it stops researching early, which cuts off the depth curves the study measures. The cap also changes the plan: under a cap, the planner sees the budget and the per-call caps, and plans fewer questions when the budget is small. The questions-per-plan results therefore describe a planner that knows it has $5, and a check run under a different cap may plan differently.

### What each step can change

These are the triggers known in advance. A step's log entry may add others.

- **Step 0.** If the time split shows scouts spend most of their wall-clock time waiting on tools rather than on the model, FlashX's speed cannot shorten a run much, and step 6 shrinks to the equivalence check.
- **Step 1.** If the pilot reaches its $5 cap, step 2 runs with a higher cap, or with the scout limits lowered to what the pilot's curves show it used. If `depth_profile.py` names tokens or dollars as what stopped any loop, those limits change before step 2, since only requests and tool calls should bind. If the pilot's scouts use all 24 requests, the depth curves will be cut off. If each of those scouts had one subject, the limits go up before step 2; if the plan bundled several subjects into a question, the plan range is widened first, as after the second pilot. If capture or replay fails, the study stops until the tool is fixed, since nothing after step 1 can run without them.
- **Step 2.** Step 4's cut points are placed where step 2's curves are still rising, instead of at a fixed 3, 5, 8, and 12. If shallow questions such as `st01` stop adding cited sources by request 2 or 3, step 4 replays them at only one or two depths. If deep questions' curves are cut off, those questions are rerun with higher limits before step 4. If many cited sources are marked `not_found`, add a question that measures it before changing models, since a model change could hide or cause it.
- **Step 3.** If the judge's variation is larger than the differences step 4 or 5 are meant to detect, steps 4 and 5 grade each report three times and average. If the two judges disagree on more than about one rubric point in five, the rubric is too ambiguous to compare models with, and step 7 waits until it is fixed.
- **Step 4.** Depth is decided by grades, not by the depth curves alone: a source first returned late is not necessarily one the report needs. A research loop's limit goes above the reference runs' only if the reports cut at their full depth score better than those cut at about half of it. Steps 5 and 6 replay at the depth step 4 chose, not at the generous limits, which makes them cheaper and measures models at the depth they would actually run at.
- **Step 5.** If `glm-5.3-flash` scouts within the judge's variation of `glm-5.3`, step 6 runs in full, because FlashX would then be a cheaper and faster replacement for the current scout. If Flash is clearly worse, FlashX, being the same model, cannot do better, and step 6 runs only the equivalence check or is dropped. If a cheap model is undecided, repeat its replays on the two questions with the largest differences before moving on. The Astra upper bound runs only if steps 1 and 2 show deep dives declining late in long loops: no new cited sources after the first few requests while searches repeat, or more unsupported claims from long loops than short ones. If they do not, it is dropped.
- **Step 7.** The fixed synthesizer's reports from step 4 at full depth are already a comparison between `gpt-6-luna` and the reference synthesizer on the same ledgers. If that shows `gpt-6-luna` well behind, it is dropped from step 7.
- **Step 8.** Step 9 runs only if step 8 confirms settings that differ from the current preset. Salvage then has to be on for both arms of step 9: the current preset's 100,000-token scout limit is the kind that stopped the first pilot's loops early, and without salvage each such stop fails a BrowseComp case, so the preset would be measured mostly by its failures.

A result can also change a prompt, such as asking results for fewer, better-supported claims. A model-visible prompt change is a behavior change, so it becomes its own before-and-after comparison, added as a new step rather than folded into one already running.

## The FlashX test

Z.ai sells `glm-5.3-flashx` as a faster tier of `glm-5.3-flash` at 2.5 times the price. A third-party write-up ([OrcaRouter](https://www.orcarouter.ai/blog/glm-5-3-flashx-release)) reports that the two use the same weights, with FlashX serving up to 200 output tokens per second against about 98 measured for Flash. If that is so, FlashX cannot answer better than Flash; it can only answer sooner. The test therefore asks two things: whether FlashX behaves like Flash here, and whether finishing sooner is worth anything in this loop.

**Where the time goes (free, step 0 and step 2).** For every research task, split its wall-clock time into time waiting on the model and time waiting on tools such as searches and fetches. Each tool call in `research_tool_events` records when the model response that issued it arrived (`called_at`) and when its result returned (`returned_at`), and each task records its start and finish, so the split needs no transcript. `called_at` was never filled in before 2026-09-25, because PydanticAI's tool-call parts carry no timestamp of their own. For earlier runs, including the recorded README run, the split comes from captured transcripts instead, whose requests and responses are timestamped. Model time is the gap between a request being sent and its response arriving, and tool time is the gap between that arrival and the last result returning. Faster generation shortens only the first. Scouts run in parallel (up to `max_parallel_scouts`, 8 by default), so the scout phase lasts as long as its slowest scout, and only that scout's model time bounds what FlashX can save per job. If model time is a small share of that, the speed case for FlashX is already answered without spending anything.

**Equivalence (about $0.50).** Take the step 1 and step 2 scout transcripts and replay the salvage call at a fixed request *k*, tool-free, on Flash and on FlashX, five times each for each of about five scouts. Tool-free replays remove search noise, so any difference comes from the model. Compare the fixed-synthesizer report scores, output length, and schema retries between the two models, against the spread among Flash's own five samples. If the FlashX samples differ from Flash's no more than Flash's differ among themselves, treat the two as the same model. If they differ more, FlashX is served differently, for example quantized, and it needs the full scout comparison of step 5 in its own right.

**Speed in a live loop (about $1).** Replay the scouts of three questions with the cache `off` on Flash and on FlashX, since recorded tools return almost at once and would make model time look like a larger share than it is, at the depth step 4 chose, and record per-request output tokens per second, each scout's wall-clock time, and the resulting scout-phase time. Run the two models on the same question at the same time of day, since a provider's speed changes with its load, and repeat each once.

**Deciding.** FlashX is worth adopting where wall-clock time matters, such as interactive use or runs under `max_run_seconds`, and the seconds it saves per job are worth its extra cost per job, compared with other ways to save time, such as more parallel scouts. If step 5 found Flash as good as `glm-5.3` at scouting, the comparison that matters most is FlashX against the current `glm-5.3` scout: FlashX costs about a quarter as much and may be faster too.

## Turning results into rules

- **Depth.** A limit is set to the smallest value at which fixed-synthesizer report scores stop improving across the questions of that depth class, plus one request of margin. A class whose curves were cut off by their limits gets no rule until it is rerun with higher ones. If shallow and deep questions need very different limits, the rule becomes one limit per `expected_difficulty`, which the planner already assigns and `scout_for` already reads.
- **Questions per plan.** A planned question whose evidence the report never cites was not worth its scout. The upper end of `planner_question_range` is lowered until almost every question contributes.
- **Result size.** If reports cite only a small share of the claims results produce, the result prompts should ask for fewer, better-supported claims. This changes a prompt, so it needs its own before-and-after comparison.
- **Report size.** Rubric score and supported-claim rate against output tokens show whether longer reports say more or only repeat.
- **Models.** A cheaper model replaces the current one in a role when its score on the study questions is within the judge's own variation, measured in step 3, and, for finishing roles, the second judge agrees. Keep the verifier from a different vendor than the synthesizer.

Each rule is then checked in step 8.

## What has to be built

- **A study runner.** Built: `examples/settings_study.py --step NAME [--cases ...] --paid` runs the suite's cases one at a time on `value` (or `--policy`) with the study limits, a $5 cap per case with $1 held for synthesis and verification, three to eight planned questions, and the README example's trimmed run: two deep dives a round and one verification round. Every paid run is stored in Postgres with transcripts, and its tool calls are recorded in the study's cache directory (`benchmark_outputs/settings_study/cache`, `--cache-mode record` by default). It writes a step record naming each case's job, which `research-grade --jobs-from` grades; a failed case names its job too, so its research can still be profiled and preflighted. `research-bench` never captures transcripts, because they would hold benchmark inputs; these questions are our own or, for DRB-II, public and stored only locally. By default it skips cases marked `retired`, and it passes each case's blocked URLs to the run. Without `--paid` it runs the synthetic policy, which rehearses the runner, the record, and grading for free; `--persist` stores those jobs too.
  Step 1 is then `python examples/settings_study.py --step step1-pilot --cases task2+ --paid`.
- **A replay command** that reruns a captured task on another model, and that can cut a research task at request *k* and run its salvage call. For depth and research models, it works on a whole question at once: it cuts or replays every research task of the question, then writes a report from the results with the fixed synthesizer. It reuses the role calls in `AsyncResearchLoop`, so the prompts and schemas are the ones real runs use.
- **A grader.** Built: `RubricJudge` in `evals.py` and `research-grade`, which grades jobs stored in Postgres with the default metrics and any rubric judges, repeatedly; see [benchmarks.md](benchmarks.md#grading-stored-runs). Replays and fixed-synthesizer reports can be graded the same way once they are stored as jobs.
- **A local price override.** Built: `src/research_loop/prices.toml` and `research_loop.prices`. The two Flash corrections are not sent upstream to `genai-prices` for now.
- **A finishing preflight.** Built: `scripts/study_preflight.py <job_id>` rebuilds a stored job's gap-analysis, synthesis, and verification prompts, using the functions the orchestrator uses, and checks each against the study policy's token limit. A finishing call refuses a prompt that could not fit one validation retry, and deeper research makes larger prompts, so a step that raises research limits runs this on the previous step's largest job first. It exits nonzero when any role would be refused.
- **The depth and timing analysis.** Built: `scripts/depth_profile.py <job_id>` reads a captured job's transcripts and gives, for each scout and deep dive, the request at which each cited source was first returned, which limit stopped it if one did, and its model, tool, and salvage time. For a deep dive it also counts how many of the sources it cited the scout on the same question had already returned: a deep dive is given the gap, not the scout's evidence, so a high count means its early requests found the scout's sources again. Section 8 of `scripts/analyze_run.sql` gives the time split without transcripts for runs from 2026-09-25 on.

## First data

The recorded README run (job `bf89797d`, 25 September 2026, on `quality` before the current optimizations: GLM-5.3 scouts limited to 12 requests and 24 tool calls, GPT-5.6 Sol deep dives limited to 40 tool calls, no prompt caching) gives a first look, from its transcripts and at no cost, with `scripts/depth_profile.py`. For each research loop, the numbers are the request whose tool calls first returned each source its result cites, matched as `source_check` matches: by URL ignoring scheme, `www.`, query, and fragment, or by DOI or arXiv ID. Time is waiting on the model, waiting on tools, and the salvage call that wrote the result of a loop its limit stopped.

| Loop | Requests | Cut off | Model s | Tools s | Salvage s | Cited sources, by request first returned |
|---|---:|---|---:|---:|---:|---|
| Scout q1 | 12 | Yes | 74 | 9 | 101 | 1, 1, 1, 1, 3, 3, 9 |
| Scout q2 | 10 | No | 151 | 12 | | 1, 1, 1, 3, 4, 4, 5, 7, 7, 8 |
| Scout q3 | 11 | No | 200 | 20 | | 1, 1, 1, 1, 3, 3, 3, 3, 3, 4, 4, 4 |
| Deep dive q1 | 6 | Yes | 34 | 28 | 151 | 1, 1, 1, 1, 1, 2, 3, 3, 3, 3 |
| Deep dive q2 | 6 | Yes | 36 | 19 | 77 | 1, 1, 1, 1, 1, 1, 2, 3, 3, 3, 4, 4, 4, 5, 5 |

- **Depth.** Scouts had been returned 66% of what they cited by request 3, 83% by request 4, 97% by request 8, and all of it by request 9, three requests before scout q1's limit stopped it. Deep dives had 80% by request 3 and all of it by request 5; their stop at 6 came from that run's limit of 40 tool calls, and `readme_example.py` now allows 80. Every cited source was returned by some tool, as `source_check` also found. A first prototype, which compared URLs exactly, left seven unmatched.
- **Time.** Scouts spent 91% of their loop time waiting on GLM-5.3, 6 to 18 seconds a request, and 9% on tools. Deep dives spent 60% on the model. So generation speed, not the web, sets how long a scout takes, which is what the [FlashX test](#the-flashx-test) needs to be worth running.
- **Salvage.** The three salvage calls took 77 to 151 seconds each, more than their loops' own model time in both deep dives, likely because each sends a loop's whole gathered evidence, up to 64,000 characters, in one request. A limit that stops a loop early saves requests but adds a salvage call.

One run is too little for a rule. It shows that the method works on the data the repository already records.

## Caveats

- **Live web.** DuckDuckGo results and web pages change between runs, so each reference run is one sample of the web. The reference runs record every search, fetch, and scholarly response, and replays reuse them, so the comparisons after step 2 share that sample instead of each drawing a new one. A replay that makes calls the reference run did not still reaches the live web for those calls. Searches match a recording only when they are the same query apart from case and spacing, so a model that words its searches differently gets fewer recorded results. That is kept on purpose: how a model words its searches is part of what the study compares.
- **Cutting is almost capping.** The salvage prompt is built only from the tool calls and results gathered so far, so cutting a transcript at request *k* builds the prompt a run capped at *k* would send. The one difference is the last response: a capped run can end on tool calls that never got results, which salvage skips. Compare one cut against one real capped run to check.
- **Contamination.** `st01` and `st02` are answerable from training data on purpose. Scores on them measure how quickly a run stops, not retrieval skill.
- **Small sample.** Seven questions show direction and large effects only. A rule that rests on a small difference needs the core benchmark suite before it goes into a preset.

## Decision log

One entry per step, newest last: the date, what ran, its cost, what it found, and what it changed in the later steps.

**Step 0, 25 September 2026. Free, apart from one $0.008 judge call made while building the grader.**

- *Ran.* The suite on the synthetic policy: all seven cases ran and were graded. `scripts/depth_profile.py` on the recorded README run ([First data](#first-data)). Primary sources for the `st05` and `st07` rubric points.
- *Rubric sources.* `st05`: the GPT-3 paper gives a 2,048-token context and 300 billion training tokens (section 2.1), and the Llama 2 paper gives 70B at 4k context and 2.0T tokens (Table 1), as the rubric says. The Chinchilla paper gives no context length anywhere; 2,048 appears only as a model width in its hyperparameter tables. That point now reads "says the Chinchilla paper does not report a context length", which also tests whether a run invents a figure. `st07`: OpenAI announced SWE-bench Verified on 13 August 2024, from 1,699 samples screened by 93 developers into 500; it stopped reporting Verified scores on 23 February 2026, citing flawed tests in 59.4% of 138 audited tasks and frontier models reproducing gold patches; and METR's note of 10 March 2026 found about half of test-passing PRs would not be merged. Two compound points were split, taking `st07` from 7 points to 9. `st03`'s answer, 93, is confirmed. `st04` and `st06` were not rechecked.
- *Judge.* The one real judge call, `openai:gpt-6-sol` at low effort on the README run's report against the old `st07` rubric, marked "OpenAI stopped evaluating on SWE-bench Verified, and why" unmet, though the report says OpenAI "stopped reporting Verified scores" and why. It is a false negative on a paraphrase, from one call.
- *Changes to later steps.* Step 3 looks first at paraphrase: if the two judges disagree on points the report states in other words, the judge instructions get an explicit rule to credit equivalent wording, as `JUDGE_VERSION` 2, before any variant is graded. Step 4's cut points for deep questions are provisionally 2, 3, 4, 6, and 9 for scouts and 2, 3, and 4 for deep dives, to be set from step 2's curves. Step 6 keeps its full speed test, since model time is 91% of scout loop time. Salvage time is now recorded with each loop, since a depth limit trades requests for a salvage call.

**Step 1, first pilot, 25 September 2026. $2.24 for the run, $0.07 for judge checks.**

- *Ran.* `st07` on `value` as configured: `openai:gpt-6-sol` planning, gap analysis, deep dives, and verification; `zai:glm-5.3` scouts, synthesis, and alternate deep dive; `zai:glm-5.3-flash` cheap scout. Scouts had 24 requests and deep dives 80 tool calls, but both had 400,000 tokens, and the cap was $4. It took 25 minutes: 4 scouts, 2 deep dives, and 2 follow-up deep dives the verifier asked for. Capture, recording, the step record, grading, and profiling all worked. `research-report` rendered the job as a PDF with `pdflatex` and `xelatex`; `lualatex` failed on this machine because its font indexer, `luaotfload-tool`, is not installed.
- *Tokens stopped the loops.* Two scouts and both deep dives stopped on the token limit, at 10 to 17 requests, before any request or tool-call limit. The two scouts it stopped were still finding sources they went on to cite, at requests 10 and 15; the two that finished stopped on their own at 9. So the limits were wrong for the study, and this pilot's depth curves are cut off.
- *Deep dives plateau early.* Every source a deep dive cited had been returned by request 3, though they ran 5 to 13 requests.
- *Time.* Model time was 87% of scout loop time and 80% of deep-dive time. The four salvage calls took 47 to 92 seconds each. DuckDuckGo refused one connection for excessive load; no provider rate limits were hit.
- *Report.* The verifier supported 15 of 21 statements and rated 4 major, all of them claims stronger than their sources. The synthesizer was `glm-5.3`, where the recorded README run's was GPT-5.6 Sol; step 7 compares synthesizers on the same evidence.
- *Judge.* At low effort the judge scored the report 2 of 9. At high effort, three times each, it gave the README run 5, 5, and 5 of 9 and the pilot 1, 2, and 1 of 9: high effort made its verdicts stable, but most of the difference from a hand check came from two other causes. Two rubric points carried incidental details the reports lacked ("for public GitHub repositories", "(announced February 2026)"), which the judge rightly read as required; we wrote both. And the judge saw only the answer and sources, so it missed a point the pilot stated as a key statement.
- *Changes to later steps.* Scout and deep-dive token limits are 2,000,000, and deep dives get 12 requests; the cap is $5 with $1 held back. `depth_profile.py` reports which limit stopped each loop. The judge runs at high effort and reads the whole report (`JUDGE_VERSION` 2). Rubric points state one fact each. The deep questions are now four DRB-II tasks with expert rubrics, `st06` and `st07` are retired, and DRB-II runs are stored locally. The pilot reruns on DRB-II `task2+`. Step 5 gains a conditional Astra upper bound, and step 9, a BrowseComp confirmation, is added for the end.

**Step 1, second pilot, 25 September 2026. $1.67, no report.**

- *Ran.* DRB-II `task2+` on `value` at the new limits, job `7532c708`. The planner asked five questions. Four scouts used all 24 requests and were salvaged; the fifth stopped on its own at 15. The run then failed at gap analysis, before any deep dive, with `PromptExceedsRetryBudget`: five scouts' results made a 138,985-character gap-analysis prompt, and one validation retry of it needs about 79,000 tokens, above the preset's 70,000. The failure cost nothing itself, since the call is refused before any request, but the research before it was spent without a report.
- *Scouts are cut off again.* This time requests, not tokens, stopped them, as intended, but the deep question kept them finding sources to the end: across the five scouts, 26% of the 66 cited sources had been returned by request 3, 48% by request 10, 86% by request 20, and the last at request 23. The shallow questions of the recorded README run had 97% by request 8. So 24 requests does not show where a DRB-II scout stops paying off.
- *Time.* Model time was 76% of scout loop time. The four salvage calls took 62 to 183 seconds each. Scouts cost $0.16 to $0.35 each before salvage.
- *Changes.* The study runner gives the gap analyst, synthesizer, and verifier 600,000 tokens; their dollar caps stay the preset's. `scripts/study_preflight.py` confirms that this job's ledger fits with 4.5 to 7.6 times headroom, which leaves room for what deep dives add before synthesis. The finishing prompts are now built by shared functions in `async_orchestrator.py`, and the preflight's rebuilt gap-analysis prompt matches the one this job sent, character for character. - *Breadth, not depth.* The plan shows why the scouts ran long. The study limited plans to three to five questions, so the planner gave three scouts two countries each and a fifth all seven; the one scout with a single country, Vietnam, stopped on its own at 15 requests. Those curves measure how much each question held, not how deep one subject needs searching. Raising the request limit would have been the costly answer: each request resends the loop's history, so doubling a scout to 48 requests would roughly quadruple its tokens, to about $0.60 to $1.40 a scout, while another scout costs about $0.25.
- *Changes to later steps.* Plans may have three to eight questions, so a broad task can give each scout one subject; how many questions a plan needs is itself one of the study's questions, so the reference runs should not fix it. The scout request limit stays at 24 and is raised only for loops that still reach it after the wider plans. Step 4 decides depth by comparing graded reports cut at full and half depth, not by the curves alone. The pilot reruns on `task2+`, and `scripts/study_preflight.py` checks its ledger before step 2.

**Step 1, third pilot, 25 September 2026. $0.75, stopped by the Z.ai balance.**

- *Ran.* DRB-II `task2+` with plans of three to eight questions, job `974042de`. The planner gave each of the seven countries its own question. Four scouts ran for about 200 seconds, reaching 16 to 20 requests, when the Z.ai credit balance ran out: one scout's request got HTTP 429, and the job cancelled the rest and failed. `research-diagnose --smoke` then failed every GLM route with an exhausted balance and passed every OpenAI route.
- *Found.* The wider range works as intended: one subject per scout. The scouts were stopped before their own limits, so whether one-country scouts stop by themselves is still open.
- *Changes.* None. The pilot reruns once the balance is topped up.

**Step 1, fourth pilot, 25 September 2026. $3.12, 42 minutes, report written.**

- *Ran.* DRB-II `task2+` with plans of three to eight questions, on the old lineup (`gpt-6-sol` planning, `glm-5.3` synthesizing), job `2722a06e`. The planner split the task by topic, not by country: five questions, each across all seven countries. The finishing roles' 600,000-token limit held; gap analysis sent 188,370 characters, and the preflight puts each finishing prompt at 3.5 to 5.7 times inside its limit.
- *Scouts.* All five used their 24 requests and were salvaged, still finding sources to the end: 38% of the 80 cited sources had been returned by request 10, 85% by request 20. A question across seven countries holds too much for one scout, whatever its limit.
- *Deep dives.* Four ran, two after gap analysis and two in the verification round; one stopped on its 80 tool calls at request 10, the rest on their 12 requests. Only 7 of their 43 cited sources were ones the scout had already returned, against 15 of 20 in the first pilot, and they kept finding new sources to request 9. The first pilot's early plateau was largely rediscovery; here, with thin scout evidence, deep dives did new work, and 12 requests cut some of it off.
- *Report.* The first verification found 25 of 56 statements unsupported and 19 major, and asked for six follow-ups; two could run. After them, 14 of 49 were unsupported and 14 major, and the verifier still asked for more research.
- *Changes.* None to limits yet. Three planners have now shaped this task three ways (by country, by topic, and in pairs), so a wider range allows but does not produce one subject per scout. Whether to ask the planner for that is a prompt change, and so a step of its own.

**Model change, 25 September 2026, before the fifth pilot.**

- *Changed.* With Anthropic enabled, the study runs `value` as the preset intends: `claude-opus-5-5` plans and synthesizes, at `medium` effort, in place of `gpt-6-sol` and `glm-5.3`. The verifier stays on `gpt-6-sol`, from another vendor than the synthesizer. No reference run existed yet, so nothing already measured loses its comparison; the four pilots stay as pilots.
- *Why.* Both reports `glm-5.3` synthesized drew heavy verifier findings, with claims stronger than their sources: 4 major in the first pilot and 25 of 56 statements unsupported in the fourth. The fourth pilot's evidence was thin, so this does not show the synthesizer was at fault; step 7 compares synthesizers on the same ledgers, now with `glm-5.3` as a candidate against Opus 5.5.
- *Guard.* `examples/settings_study.py` names the study's model for every route and refuses a paid `value` step when the environment's `RESEARCH_*_MODEL` overrides move any of them, so a change to `.env` for everyday runs cannot change the study partway through.
- *Next.* The pilot reruns on `task2+` with Opus 5.5 once the Anthropic account has credits; `research-diagnose --smoke` found its balance empty.

**Dead tool calls and salvage, 25 September 2026, during the fifth pilot.**

- *Found.* In the fourth and fifth pilots, 166 of the scouts' 379 tool calls (44%) returned nothing usable: 69 fetches failed with HTTP 403 or 404 (37% of fetches), 48 DuckDuckGo searches were unavailable (31% of searches), 14 fetches were refused as an unsupported content type, 10 of them PDFs sent to `web_fetch`, which read HTML only, and the rest were refused, unreachable, or empty. Scouts then built Bing RSS URLs by hand, which failed too. A scout that reaches 24 requests has had roughly 13 useful ones, so the request limit mostly measures dead calls on these government sites, not depth.
- *Found.* The fifth pilot's Sri Lanka scout reached its limit and its salvage call failed: `glm-5.3` wrote a 15,000-token answer, the validation retry took the call past the salvage route's 80,000 tokens, and the question was left with an empty result, losing 24 requests of research. Stored salvage calls had used a retry in 8 of the 25 largest, and one succeeded at 78,000.
- *Checked.* Salvage kept every useful result in every cut-off loop and left none out as oldest, so the depth curves of salvaged loops are not biased toward late requests.
- *Changes.* The salvage route has 140,000 tokens. A salvage call that still fails leaves a result without claims that keeps the loop's search queries and names the pages it read as follow-ups. `web_fetch` reads PDFs, and both fetches know a PDF by its first bytes when a server declares another type (`fetch_version` 5). The scout request limit stays at 24 until the next pilot shows how many requests dead calls still take; raising it now would mostly buy more failed fetches. Searches that fail and sites that refuse the fetcher are not addressed yet.

**Step 1, fifth pilot, 25 September 2026. $4.30, 37 minutes, report written.**

- *Ran.* DRB-II `task2+` on the pinned lineup, Opus 5.5 planning and synthesizing, job `3bccbe6d`, from commit `7b936d0`, before the salvage and PDF changes above. Opus 5.5 planned four questions, three of them pairs of countries, like the second pilot's planner.
- *Scouts.* All four used their 24 requests and were salvaged; the Sri Lanka scout's salvage failed (above) and left its question empty until the verification round. Of 60 cited sources, 58% had been returned by request 8 and 90% by request 20.
- *Deep dives.* Four ran, two after gap analysis on `gpt-6-sol` and two in the verification round on `glm-5.3`; three stopped on their 12 requests and one on its 80 tool calls. Only 11 of their 78 cited sources were ones the scout had already returned, and they found new sources to their last request.
- *Report.* Opus 5.5 synthesized in one request each time, with no validation retry, for $0.55 and then $0.78. The first verification found 24 of 41 checked statements unsupported and 9 major; after the follow-ups, 21 of 39 and 13 major, and the verifier still asked for more research. That is no better than the fourth pilot's `glm-5.3` report (14 of 49, 14 major), so the unsupported claims are not the synthesizer's alone; step 7 compares synthesizers on the same ledgers.
- *Time.* A salvage call took 1.6 to 2.9 minutes, as long as 12 requests of the loop it summarized, since it writes a whole result: 21,000 to 44,000 characters, 15 to 45% of `glm-5.3`'s output tokens being reasoning. Every research loop in five pilots was salvaged, so salvage is about a third of each research phase.
- *Preflight.* The finishing prompts, gap analysis at 222,778 characters, fit their 600,000 tokens 3.0 to 4.9 times over.
- *Next.* The loops cannot see their limits. The sixth pilot turns on budget notes (`--budget-notes scout deep_dive`), which end each loop request with the requests and tool calls left and ask for the result before a limit, so a loop can write its result with its full tool output in context instead of a salvage call writing it from cut-down output. It also runs the salvage and PDF changes, so dead fetches and salvage failures are measured with it; a change in depth is read from the depth curves and whether loops stopped on their own.

**Prompt trials and a planner change, 25 September 2026, during the sixth pilot. About $6, by the pilots' per-call costs.**

- *Tool.* `scripts/prompt_trial.py` runs the orchestrator's own planner, synthesizer, and verifier calls on the study's inputs with the current and a candidate instruction, and stores nothing in Postgres.
- *Refusal.* Opus 5.5 refused to plan `task26`, on T-cell exhaustion, as a biological risk, under both prompts. Planner and synthesizer routes now name a `refusal_fallback`, `gpt-6-sol` in `value`, which a refused call runs on once more; `task26` then planned in four questions.
- *Synthesizer prompt, undecided.* The candidate asked for facts stated no more broadly than their evidence, with unverified, out-of-period, and secondary-source facts marked where they appear. On the ledgers of the first, fourth, and fifth pilots it drew 4 of 26, 11 of 31, and 11 of 55 checked statements unsupported, against 1 of 22, 18 of 42, and 12 of 46 for the current prompt. The fifth pilot's own report, with the same prompt, models, and ledger, drew 21 of 39, so one verification per report varies more than the prompts differ. It waits for step 3's measure of the verifier's variation. The script reported $7.61, but it counted the calls of units running at the same time into each other's costs, so the figure and every per-unit cost it printed are too high; at the pilots' $0.55 to $0.78 a synthesis and $0.19 to $0.24 a verification, the trial cost about $5. The script now attributes spend by job and has a hard `--max-usd` cap.
- *Planner.* On `task2+`, Opus 5.5 paired countries in all three of its plans. `gpt-6-sol` has now planned one question per country in two of three plans and split by topic in the third, at a third of the cost. A candidate prompt asking for one question per parallel subject made both models plan per country, but on `gpt-6-sol` it also filled the eight-question range on `task2+` and `task17+`, adding a scout each, so it is not adopted; the planner model was the larger effect.
- *Changed.* `value`'s planner, and the study's, is `gpt-6-sol` at `high`. No reference run exists, so no comparison is lost. Opus 5.5 remains the synthesizer until step 7.

**Step 1, sixth pilot, 25 September 2026. $5.03, no report.**

- *Ran.* DRB-II `task2+` with budget notes on for scouts and deep dives (`--budget-notes scout deep_dive`), on the salvage and PDF changes above, job `7f456a01`. The planner was still Opus 5.5; it planned five questions, three of them pairs of countries, and one across all seven.
- *Budget notes.* Nine of the ten research loops returned their result on their own, where none of the sixteen in the fourth and fifth pilots did. Scouts stopped at 12 to 19 requests and deep dives at 11 or 12. The exception, the cross-country scout, used its 48 tool calls in 17 requests: the notes invite parallel calls, so the tool-call limit, not the request limit, is now the one a broad scout reaches. Of 54 cited scout sources, 67% had been returned by request 8 and 98% by request 16.
- *Failure.* The verification round's synthesis needed a validation retry and cost $1.12, against $0.59 for the first; the job had then spent $4.77 of its $5 cap, and the final verification ran out of it after one request. A failed verification failed the job, so the finished report was lost.
- *Changes.* A verification round now runs only if the job can pay for its synthesis and verification, budgeted at twice the latest pair's cost, and its deep dives leave that amount unspent; a round that still runs out finishes with the report verified before it instead of failing ([graph.md](graph.md#verification-rounds)). With these rules the sixth pilot's round would have run with $1.57 held back, capping its deep dives at about $0.44, and its synthesis and verification would have fit.

**Scout trial, planned 25 September 2026, before it ran.**

- *Question.* Can `zai:glm-5.3-flash`, at about a ninth of `glm-5.3`'s price, scout as well, alone or run three times with the results merged? And does `glm-5.3` at `low` effort in place of `high` lose anything?
- *Method.* `scripts/scout_trial.py` scouts the seventh pilot's plan again with the study's scout limits and budget notes, reusing its recorded searches and fetches. The arms are three Flash runs, those three merged, and `glm-5.3` at `low`; the seventh pilot's own scouts are the baseline. Every arm is scored by measures of its results computed without a model, and by a report the fixed synthesizer (`gpt-6-luna`) writes from its scout results alone, graded by the judge against `task2+`'s rubric. The arms run with tools withdrawn on a loop's last request, which the baseline did not have; that changes only loops that reach a limit.
- *Decision rules.* Flash replaces `glm-5.3` as the scout if the baseline's grade lies within the spread of the three Flash runs' grades and Flash's share of quotes not found in tool output is no higher. Three merged Flash runs are an option if they grade above the baseline at under half its scouting cost. `glm-5.3` at `low` replaces `high` if its grade lies within the Flash spread of the baseline's and it costs less. Otherwise nothing changes. The baseline and `low` are one sample each, so a result near a boundary is recorded as undecided rather than repeated.
- *Cap.* $3.50 on scouting and synthesis; the judge adds about $0.05 a report.
- *Storage.* This first run keeps its results in memory and writes only its JSON record, as the prompt trials did; it has no transcripts. Trials run after it store every unit as a Postgres job marked with its trial and arm, with transcripts and costs, so they can be profiled, graded, and rendered afterwards and a crash cannot lose their spend.

**Scout trial, ran 25 September 2026. $1.80 of the $3.50 cap, and $0.47 for the judge.**

- *Ran.* On the seventh pilot's five-question plan, from the code before the search and fetch fixes, and before trials were stored, so the arms have no transcripts. Grades are the judge's share of `task2+`'s rubric points met by a report `gpt-6-luna` wrote from the arm's scout results alone, one grading each. Scout-only reports score low, and 0 on the rubric's analysis points, which ask for a comparison no scout was asked to write; only differences between arms matter.

| Arm | Questions answered | Claims | Cited sources | Quotes not found | Scouting cost | Grade |
|---|---:|---:|---:|---:|---:|---:|
| Baseline, `glm-5.3` at `high` (the pilot's scouts) | 5 of 5 | 72 | 51 | 8 of 122 | $1.84 | 0.21 |
| `glm-5.3-flash`, run 1 | 5 of 5 | 66 | 48 | 3 of 72 | $0.17 | 0.22 |
| `glm-5.3-flash`, run 2 | 5 of 5 | 66 | 52 | 3 of 105 | $0.15 | 0.22 |
| `glm-5.3-flash`, run 3 | 4 of 5 | 57 | 39 | 6 of 95 | $0.19 | 0.12 |
| The three Flash runs merged | 5 of 5 | 189 | 119 | 12 of 272 | $0.51 | 0.26 |
| `glm-5.3` at `low` | 5 of 5 | 62 | 42 | 6 of 69 | $1.23 | 0.18 |

- *Decided, by the rules set before the run.* **Flash replaces `glm-5.3` as the scout**: the baseline's 0.21 lies within Flash's 0.12 to 0.22, and Flash's quotes not found in tool output, 12 of 272 (4%), are fewer than the baseline's 8 of 122 (7%), at about a tenth of the cost. **Three merged Flash runs pass as an option**: 0.26 against 0.21, at $0.51 against $1.84. **`glm-5.3` at `low` passes too** (0.18, $1.23), and is moot while Flash scouts.
- *Caveats.* One grading per report, and step 3 has not yet measured the judge's variation, so 0.26 against 0.21 is a small margin. Flash's third run lost a question: its two-country scout reached its limit and salvage returned no claims, taking two countries out of the report, which is the whole of its low grade; Flash is less steady than its mean suggests on broad questions. Flash's dead tool calls were 29 to 39% of its calls, as `glm-5.3`'s were, since the fixes came after.
- *Next.* Flash is the study's scout from the next run. Running three Flash scouts per question and merging them is a change to how scouts are dispatched, with a larger ledger for synthesis and verification to pay for, so it is a separate decision.

**Planner trial, planned 25 September 2026, before it ran.**

- *Question.* Do several planner calls, or a landscape survey before planning, give steadier plans than one call? The planner split `task2+` four ways across runs.
- *Method.* `scripts/plan_trial.py` plans each deep case (`task2+`, `task8`, `task17+`, `task26`) twice in each of three arms: `single`, today's call; `best-of-3`, three calls and a `gpt-6-luna` selector choosing against fixed criteria; and `landscape`, a Flash scout survey (8 requests, 16 tool calls) whose summary and findings the planner is given. Measured without a model: agreement, how alike a case's two plans are, question by question by shared words; and coverage, the share of the entities the objective names that some question names.
- *Decision rules.* An arm is adopted if its mean agreement exceeds `single`'s by at least 0.15 and its mean coverage is no more than 0.02 below. If both pass, the one with the higher agreement goes into the next pilot, and the other is tried with it only if that pilot's plan still varies. Otherwise planning stays as it is.
- *Cap.* $2.50.

**Model change, 25 September 2026, after the scout trial.**

- *Changed.* The study scouts on `zai:glm-5.3-flash`, as the scout trial's rules decided, and synthesizes on `zai:glm-5.3` at Z.ai's highest reasoning effort (`max`, PydanticAI's `xhigh`) with a 64,000-token output allowance for its reasoning and report, in place of Opus 5.5. `examples/settings_study.py` sets both routes itself; the `value` preset keeps `glm-5.3` scouting and Opus 5.5 synthesizing until a pilot confirms the change. The verifier stays `gpt-6-sol`, from another vendor than the synthesizer, and a refused synthesis still falls back to it.
- *Why.* Opus 5.5 cost $0.55 to $1.12 a synthesis without better-supported reports, which the verifier's run-to-run variation made impossible to tell apart; `glm-5.3` costs about a third as much. At `high` effort it needed a validation retry in the first and fourth pilots, which `max` may or may not change. Step 7 still compares synthesizers on the same ledgers; this sets the one the study runs with in the meantime.
- *Checked.* A live call confirmed Z.ai receives `reasoning_effort: max` and `max_completion_tokens: 64000`, and returns structured output.

**Planner trial, ran 25 September 2026. $0.70 of the $1.50 cap.**

| Arm | Agreement | Coverage | Questions | Cost |
|---|---:|---:|---:|---:|
| `single` | 0.43 | 0.46 | 4.4 | $0.11 |
| `best-of-3` | 0.41 | 0.45 | 4.6 | $0.33 |
| `landscape` | 0.40 | 0.45 | 4.6 | $0.26 |

- *Decided, by the rules set before the run.* Neither arm is adopted: neither beat `single`'s agreement by 0.15. Planning stays one call, without a survey.
- *The measure was flawed.* Agreement matched questions by shared words, so it measured wording, not how a case was split. On `task2+`, `best-of-3`'s two plans split the countries identically (Indonesia with Malaysia, Pakistan with Sri Lanka, then the Philippines, Thailand, and Vietnam alone) but were worded differently and scored 0.20, while `single` split once by topic and once by country, and `landscape` by country with different pairings. Coverage counted every capitalized word the objective uses mid-sentence, table headings among them, and barely moved. So the trial could not have shown the steadiness it was meant to measure. What it does show, not as a result, is that on `task2+` only `best-of-3` gave the same split twice.
- *Next, if pursued.* Measure agreement by which of the objective's named entities each question covers, and log that before rerunning `single` against `best-of-3` three times each on `task2+` and `task17+`, the cases that name their entities; about $0.60.

**Planner follow-up, planned 25 September 2026, before it ran.**

- *Question.* Does `best-of-3` give steadier plans than `single`, measured by how a case is split rather than how questions are worded?
- *Measure, fixed before the run.* `task2+` names seven countries and `task17+` seven platforms and four organizations; `ENTITIES` in `scripts/plan_trial.py` lists them and the forms plans use (such as "Thai" and "Philippine"). Two plans' agreement is the share of questions, over the larger plan, matched by naming exactly the same entities; questions naming none, such as a topic across every country, match by shared words. Coverage is the share of the entities some question names, so a split by topic, which names none, covers less than a split by entity: intended, since the study wants a scout per subject. On the first trial's plans this measure gives `best-of-3` 1.00 on `task2+`, where word overlap gave 0.20; that was a check of the measure, not a result.
- *Run.* `single` and `best-of-3`, three plans each on `task2+` and `task17+`, so three pairs per case and arm.
- *Decision rule.* `best-of-3` is adopted if its mean agreement exceeds `single`'s by at least 0.15 and its mean coverage is no more than 0.02 below. Otherwise planning stays one call.
- *Cap.* $0.60.

**Planner follow-up, ran 25 September 2026. $0.36 of the $0.60 cap.**

| Arm | Agreement | Pairs fully alike | Coverage | Questions | Cost |
|---|---:|---:|---:|---:|---:|
| `single` | 0.70 | 1 of 6 | 1.00 | 4.5 | $0.09 |
| `best-of-3` | 0.60 | 3 of 6 | 1.00 | 4.8 | $0.27 |

- *Decided, by the rules set before the run.* `best-of-3` is not adopted: its agreement is below `single`'s. Planning stays one call.
- *By case.* On `task2+`, `best-of-3` split the seven countries the same way all three times (three alone, two pairs), where `single` split them into three or four groups with different pairings (agreement 0.6, 0.6, 1.0); every plan in both arms split by country this time, none by topic. On `task17+`, `best-of-3`'s three plans each grouped the platforms differently (0.2 each), while `single`'s agreed more (0.8, 0.6, 0.6). So the selector steadied the case it was built for and unsettled the other, and pooled it did worse. Both arms named every entity in every plan.
- *Reading.* Across both trials, one `gpt-6-sol` call has varied in how it groups entities but, in the follow-up, always split by entity; a different grouping changes which subjects share a scout, not whether they are researched. The next pilots' reports show whether that matters; the planner trials stop here.

**Scout budgets, 25 September 2026, before the next pilot.**

- *Found, in pilot 8.* The two-country scouts used all 48 tool calls in 14 to 17 requests. Most calls were empty searches or failed fetches: q2 had 8 of 9 searches empty and 28 of 36 fetches failed, then returned a conclusion and no claims. q4, one country, stopped itself at 33 calls with 14 claims. The note was telling them to batch calls, and a miss counted the same as a page.
- *Changed.* A study scout now has 12 requests, 16 productive tool calls, and 12 misses. A productive call is a search with results, a fetch with text, or a further window of a page already fetched. An empty search, an HTTP error, a timeout, or a blocked or unsafe URL is a miss. Either budget withdraws the tools, and after a batch that mostly missed the note asks for one or two broader searches. Deep dives stay at 12 requests and 80 tool calls. The running pilot 8 job keeps the old limits.

**Flash effort, 25 September 2026. Noted, not yet applied.**

- *Intent.* When a route specifies `zai:glm-5.3-flash`, its default reasoning effort is max. In this code that is `thinking="xhigh"`, which PydanticAI sends to Z.ai as `reasoning_effort: max`, the same setting `SYNTHESIS_EFFORT` already uses for `glm-5.3`.
- *What ran.* The 16 stored Flash scouts, eleven in the scout trial and five in pilot 8, were at `high`. The scout route was copied from the `glm-5.3` scout and only the model name was changed. Max has been used twice, both on `glm-5.3` synthesis.
- *Not changed yet.* The study scout and the cheap-scout preset still think at `high`. The next time a route is set to Flash, set its effort to max with that change.

**Pilot 8, ran 25 September 2026. Failed at synthesis; $0.75, no report.**

- *What happened.* `task2+` planned four questions, and its scouts, gap analysis, and two deep dives all finished by 16:02. The synthesizer, `glm-5.3` at `max` with a 64,000-token output allowance, sent its request and ended 1,802 seconds later with `ModelAPIError`. No response was stored. The job's evidence ledger was kept.
- *Cause, inferred from the timing.* Nothing in this code set a model timeout, so PydanticAI's 600-second read timeout applied, and the OpenAI SDK, which the Z.ai provider uses, retries a timed-out request twice: three attempts of 600 seconds. At `high`, `glm-5.3` synthesized `task2+` in 180 to 256 seconds, writing about 110 tokens a second; at that rate most of a 64,000-token allowance takes about ten minutes, and an unstreamed reply arrives only when it is complete. Only the error's type was stored, not its message, so a provider that held the connection without answering would look the same.
- *Also.* The `gpt-6-sol` fallback did not run, because it answered refusals only. The scouts ran with the old limits (48 tool calls), since the budget change came after this job started.
- *Changed.* Synthesis is streamed, so the read timeout applies between chunks. A route's `refusal_fallback` also takes a call that ends on a provider error, and the job's notes name both models.

**Flash effort and re-synthesis, 25 September 2026.**

- *Flash effort, applied.* Every route on `zai:glm-5.3-flash` now thinks at `xhigh`, which PydanticAI sends as `reasoning_effort: max`: the study's scout, the study's and the `glm-heavy` and `value` presets' cheap scouts (from `low`), and any route an override moves to Flash. `MODEL_EFFORT` in `policy.py` holds the rule and `apply_model_effort` applies it once a policy's models are final; a route moved off Flash keeps its preset's effort. Scouts at max write more reasoning, so their cost per call rises; the study's dollar caps are unchanged.
- *Re-synthesis.* `scripts/resynthesize.py <job_id> --max-usd 1.50` synthesizes and verifies a stored job's ledger on the study's current synthesizer and verifier, stores the result as a trial job, and grades it with the study's judge. It records each finishing call's model, status, time, tokens, and cost, so a fallback or a slow synthesis shows. It runs one verification without a follow-up round, so its report is comparable to a pilot's first report, not to one revised after follow-ups.

**Synthesis effort, 25 September 2026. Back to `high`.**

- *Found.* `scripts/resynthesize.py` on pilot 8's ledger, with synthesis streamed, was stopped after 7.8 minutes still in the synthesis call; the trial job is `087b942e`, recorded failed (`CancelledError`). A small streamed call at `max` streamed its thinking, but only after 11.8 seconds.
- *Correction, the same day.* The stop was based on a reading of no bytes received, taken from `rchar` in `/proc/<pid>/io`, which does not count socket reads; the socket's own counter (`ss -ti`, `bytes_received`) showed a later streamed call receiving data throughout. So whether the streamed `max` synthesis was receiving its reply, and whether it would have finished, is not known. Pilot 8's unstreamed failure after about 30 minutes stands.
- *Changed.* The study synthesizes on `glm-5.3` at `high`, as pilot 4 did on this case in 180 to 256 seconds, keeping the 64,000-token output allowance. The reason `max` was chosen, fewer validation retries than `high` needed, is untested, and so is whether a streamed `max` synthesis of this size finishes.

**Re-synthesis of pilot 8 on Opus 5.5, ran 25 September 2026. $0.68 with the judge.**

- *Ran.* `scripts/resynthesize.py b796008c --synthesizer anthropic:claude-opus-5-5` wrote a report from pilot 8's stored ledger with Opus 5.5 at `medium` (`MODEL_EFFORT`), the study route's 64,000-token allowance, and its `gpt-6-sol` fallback; `gpt-6-sol` verified it and the judge graded it. Trial job `6d385f25`.
- *Result.* Synthesis took one streamed request, 92 seconds, 41,475 input and 10,936 output tokens, $0.43, with no fallback and no validation retry. Verification took 195 seconds and $0.17: of 27 checked statements, 14 unsupported and 8 major, with 6 follow-ups, which the script does not research. The judge gave 0.21 ($0.08), one grading.
- *Reading.* The share unsupported is like the fifth pilot's Opus 5.5 report (24 of 41) and higher than the fourth pilot's `glm-5.3` report (14 of 49); one verification varies more than that between runs, so this does not rank synthesizers. It shows pilot 8's ledger synthesizes in under two minutes on Opus 5.5.
