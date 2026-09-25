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

The seven questions are in `examples/settings_study_cases.jsonl`, and `examples/settings_study.toml` makes them a suite. They range from facts a model knows without searching to questions that need several primary sources, because the right depth depends on the question. A depth rule that suits one kind of question and wastes money on another shows up only when both are in the set.

| ID | Question type | Scored by | Expected depth |
|---|---|---|---|
| `st01-transformer-venue` | One fact a model knows from training | Exact answer | Shallow |
| `st02-resnet-author` | Two hops | Exact answer | Shallow |
| `st03-swebv-annotators` | A number from one primary source | Exact answer | Medium |
| `st04-ilsvrc-captioning` | A false premise: the track never existed | Rubric | Medium |
| `st05-scaling-table` | Nine numbers from three papers | Rubric | Medium |
| `st06-cot-small-models` | A synthesis of the research literature | Rubric | Deep |
| `st07-swebench-trust` | Contested and recent; the README example | Rubric | Deep |

The first three have short reference answers that `ReferenceAnswerMatch` scores without a judge. The others have rubrics: lists of points a good answer contains. `st01` measures over-searching, since a good run should stop almost at once. `st04` measures whether a run can conclude that something does not exist rather than searching until its limits stop it. `st07` repeats the README example, so its results compare with the run already recorded.

`ReferenceAnswerMatch` compares the whole normalized answer, so "93 developers" or "NeurIPS (then NIPS) 2017" scores 0 even though it is right. The case asks for a succinct `Exact Answer:` line, which makes this rare. Read every miss by hand before counting it.

The `st05` and `st07` rubrics were checked against primary sources in step 0; see the [Decision log](#decision-log). The Chinchilla paper gives no context length, so that point now asks the report to say so. The `st07` points were written after the recorded README run, so each was confirmed from its own primary source, and points that bundled several facts were split, since the judge gives no partial credit for a point half met.

## Models

These are the models the configured keys can reach, with list prices in USD per million tokens. The OpenAI prices and `glm-5.3` come from `genai-prices`. The two Flash prices come from [Z.ai's pricing page](https://docs.z.ai/guides/overview/pricing), checked on 25 September 2026:

| Model | Input | Cached input | Output | Tried for |
|---|---:|---:|---:|---|
| `openai:gpt-6-luna` | 0.10 | 0.01 | 0.50 | Scout, deep dive, planner, synthesizer; the fixed synthesizer |
| `zai:glm-5.3-flash` | 0.15 | 0.03 | 0.50 | Scout, deep dive, gap analyst |
| `zai:glm-5.3-flashx` | 0.37 | 0.075 | 1.25 | Scout, in the [FlashX test](#the-flashx-test) |
| `zai:glm-5.3` | 1.40 | 0.26 | 4.40 | Every role (the current scout and synthesizer) |
| `openai:gpt-6-sol` | 2.00 | 0.20 | 10.00 | Every role (the current planner, gap analyst, deep dive, and verifier) |
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

**One fixed grader.** Short answers are scored by exact match. Rubrics are scored by one judge model and prompt that never change once variants are being graded, because the run's own verifier changes whenever the verifier route does. The judge is `openai:gpt-6-sol` at low effort, and its instructions ask only whether each rubric point is present and correct. Two checks, both made in step 3 before any variant is graded, make its scores usable:

- **Its own variation.** Each reference report is graded three times. The spread of those scores is the judge's variation, which every decision rule below uses.
- **Its vendor.** `gpt-6-sol` is also one of the synthesizers compared in step 7, and judges tend to favour their own output. A second judge from another vendor, `anthropic:claude-opus-5-5` at low effort, grades the same reports with the same prompt. Report how often the two agree. In step 7, both judges grade every report, and a result counts only where they agree.

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
| 1. Pilot | What does one deep question cost on `value` with generous limits, and does capture and replay work end to end? | `st07` once on `value` with generous limits (scouts 24 requests and 48 tool calls, deep dives 80 tool calls, 400,000 tokens each), transcripts captured, every tool call recorded (`record` cache mode), $4 cap | $4 |
| 2. Reference runs | What does each question need, and where does the time go? | The other six questions, the same way; free depth curves and time splits for every research task | Set by step 1; about $6 |
| 3. Judge | How much can a score difference be trusted? | Each reference report graded three times by each judge; rubric points the judges disagree on are reworded or dropped | $1 |
| 4. Depth | How deep should scouts and deep dives search? | Salvage replays of every research task at the cut points step 2 suggests, one fixed-synthesizer report per question and depth | $5 |
| 5. Research models | Can cheaper models scout and deep-dive? | Scouts replayed on `glm-5.3-flash` and `gpt-6-luna`, deep dives on `glm-5.3` and `gpt-6-luna`, at the depth step 4 chose, one fixed-synthesizer report per variant | $3 |
| 6. FlashX | Is FlashX the same model as Flash, and is its speed worth 2.5 times the price here? | See [The FlashX test](#the-flashx-test) | $1.50 |
| 7. Finishing models | Which planner and synthesizer? | Planner on four models, synthesizer on `glm-5.3`, `gpt-6-sol`, and `gpt-6-luna` for every question, plus `gpt-6-astra` on three, all on the step 2 ledgers, graded by both judges | $5 |
| 8. Check | Do the chosen settings hold up in a normal run? | All questions once with the chosen settings | About step 2's cost |

These caps add up to about $26 without the check run, but that is a ceiling, not an estimate: an adaptive plan usually skips work. Step 1 is the least certain cost. The only recorded runs of the example question cost $2.26 and $3.68, but both ran on `quality` before the current optimizations, so they say little about the `value` lineup. Step 1 replaces the guesses for steps 2 and 8 with a measured cost.

The $4 per-question cap is there to catch a runaway run, not to shape the result. A run that reaches it stops researching early, which cuts off the depth curves the study measures. The cap also changes the plan: under a cap, the planner sees the budget and the per-call caps, and plans fewer questions when the budget is small. The questions-per-plan results therefore describe a planner that knows it has $4, and a check run under a different cap may plan differently.

### What each step can change

These are the triggers known in advance. A step's log entry may add others.

- **Step 0.** If the time split shows scouts spend most of their wall-clock time waiting on tools rather than on the model, FlashX's speed cannot shorten a run much, and step 6 shrinks to the equivalence check.
- **Step 1.** If the pilot reaches its $4 cap, step 2 runs with a higher cap, or with the scout limits lowered to what the pilot's curves show it used. If the pilot's scouts use all 24 requests, the depth curves will be cut off, so the limits go up before step 2. If capture or replay fails, the study stops until the tool is fixed, since nothing after step 1 can run without them.
- **Step 2.** Step 4's cut points are placed where step 2's curves are still rising, instead of at a fixed 3, 5, 8, and 12. If shallow questions such as `st01` stop adding cited sources by request 2 or 3, step 4 replays them at only one or two depths. If deep questions' curves are cut off, those questions are rerun with higher limits before step 4. If many cited sources are marked `not_found`, add a question that measures it before changing models, since a model change could hide or cause it.
- **Step 3.** If the judge's variation is larger than the differences step 4 or 5 are meant to detect, steps 4 and 5 grade each report three times and average. If the two judges disagree on more than about one rubric point in five, the rubric is too ambiguous to compare models with, and step 7 waits until it is fixed.
- **Step 4.** Steps 5 and 6 replay at the depth step 4 chose, not at the generous limits, which makes them cheaper and measures models at the depth they would actually run at.
- **Step 5.** If `glm-5.3-flash` scouts within the judge's variation of `glm-5.3`, step 6 runs in full, because FlashX would then be a cheaper and faster replacement for the current scout. If Flash is clearly worse, FlashX, being the same model, cannot do better, and step 6 runs only the equivalence check or is dropped. If a cheap model is undecided, repeat its replays on the two questions with the largest differences before moving on.
- **Step 7.** The fixed synthesizer's reports from step 4 at full depth are already a comparison between `gpt-6-luna` and the reference synthesizer on the same ledgers. If that shows `gpt-6-luna` well behind, it is dropped from step 7.

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

- **A study runner.** Built: `examples/settings_study.py --step NAME [--cases ...] --paid` runs the suite's cases one at a time on `value` (or `--policy`) with the study limits, a $4 cap per case with $1.50 held for synthesis and verification, and the same trimmed run as the README example: three to five planned questions, two deep dives a round, and one verification round. Every paid run is stored in Postgres with transcripts, and its tool calls are recorded in the study's cache directory (`benchmark_outputs/settings_study/cache`, `--cache-mode record` by default). It writes a step record naming each case's job, which `research-grade --jobs-from` grades. `research-bench` never captures transcripts, because they would hold benchmark inputs; these questions are our own, so capturing is safe. Without `--paid` it runs the synthetic policy, which rehearses the runner, the record, and grading for free; `--persist` stores those jobs too.
  Step 1 is then `python examples/settings_study.py --step step1-pilot --cases st07-swebench-trust --paid`.
- **A replay command** that reruns a captured task on another model, and that can cut a research task at request *k* and run its salvage call. For depth and research models, it works on a whole question at once: it cuts or replays every research task of the question, then writes a report from the results with the fixed synthesizer. It reuses the role calls in `AsyncResearchLoop`, so the prompts and schemas are the ones real runs use.
- **A grader.** Built: `RubricJudge` in `evals.py` and `research-grade`, which grades jobs stored in Postgres with the default metrics and any rubric judges, repeatedly; see [benchmarks.md](benchmarks.md#grading-stored-runs). Replays and fixed-synthesizer reports can be graded the same way once they are stored as jobs.
- **A local price override.** Built: `src/research_loop/prices.toml` and `research_loop.prices`. The two Flash corrections are still worth sending upstream to `genai-prices`.
- **The depth and timing analysis.** Built: `scripts/depth_profile.py <job_id>` reads a captured job's transcripts and gives, for each scout and deep dive, the request at which each cited source was first returned, whether a limit cut it off, and its model, tool, and salvage time. Section 8 of `scripts/analyze_run.sql` gives the time split without transcripts for runs from 2026-09-25 on.

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
