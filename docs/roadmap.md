# Roadmap

This is the working plan as of 28 September 2026. A session that resumes the work starts here, checks which step is current, and updates this file when a step finishes or the plan changes. Finished steps move to the bottom, with a link to their study-log entry. Each study's own decision rule is in its spec header, and its results go in [study-log.md](study-log.md).

Every paid step follows AGENTS.md: a spec with its decision rule, a clean dry check, a cheap check only when an arm uses something without a real run on the current code, and the user's approval with an estimate and a hard ceiling. Steps 1 to 3 are already approved. Steps 4 onward each need approval before they spend anything.

## Status on 28 September, evening

The trimming study finished and turned trimming off. The first search study was stopped after request timeouts, and scout-v14 raised the scout request timeout to 600 seconds and the time, call, and request limits (study log, "Trimming off, and limits as safety nets"). The search study is rerunning as `search-rescout-task8-v14`: check its first plan's four rescouts for failures before letting the rest run. scout-v15 (scouts pay for their own paid searches, productive calls a loop guard at 128, 48,000-token replies) waits on branch `claude/scout-v15` and is merged once the search study finishes. The DeepSeek study then runs on scout-v15, with its dry check rerun first.

## Paused: audit (28 September, evening)

All paid calls are paused until the contamination audit passes ([audit](audit-2026-09-28-case-contamination.md)). Its pass criteria:
- the v16 fixes are merged;
- the stale runs are closed;
- the study log marks the results since v6;
- the claim fix is measured on held-out cases only.

After it passes, write up the Serper rerun and the partial DeepSeek study, then design a held-out measurement of the claim fix with its baseline and cost, for approval.

## Experiment: synthesis with Claude's citations (29 September)

Branch `claude/citations-synthesis`, stacked on `claude/scout-v15`, tests binding each sentence of the report to the passages it rests on, which addresses audit findings A02 and A04 (the support label and the evaluators judge a claim list that is not the report's text). The synthesizer is always Claude. It receives the ledger's supporting evidence as `search_result` blocks, and code writes the report's citations and claim list from the passages Claude cites (`citations.py`). It makes one request, not cached and never retried, and each depth's budget grew so its synthesis share covers a reply at the full output cap. One live check on st07 cost $0.03 and worked end to end (study log, "First live synthesis with Claude's citations"). Nothing has been graded. Whether it replaces the current synthesis needs a paired, graded comparison with a decision rule, planned and approved after the audit passes. The fuzz model writes no tagged reply, so on this branch fuzz runs never reach a written report.

Synthesis v7 (scout-v19) followed a review against Anthropic's citations, search-result, and refusal documentation. The passages come before the brief, and a claim with a passage is listed without its statement, so the report's facts come from what it can cite; the live check left 6 of 14 answer sentences uncited. A citation counts only when its source, search-result position, block range, and cited text match what was sent (all 21 of the live check's did). When Opus 5.5's classifiers decline the synthesis, Anthropic continues it on Opus 5 inside the same request; every attempt is priced at its own model's rates, the guard reserves both (usage-anchor-v7), and each depth's total grew by the fallback's worst case ($1.45, $1.16 for quick) with the scouts' shares unchanged. Nothing of v7 has run against the real API. Before any paid run on it: run `research doctor --smoke`, which sends the synthesizer's smoke call with its fallback and so checks that Anthropic accepts Opus 5 as Opus 5.5's fallback; and add the resynthesis of st07 to the comparison's cheap check, to see the uncited share with the statements gone. The two prompt changes are part of the arm, not measured apart.

## 1. Finish the two running studies

The trimming study (`studies/trim-history-rescout-task8.toml`, $3.00 ceiling) and the search-engine study (`studies/search-rescout-task8.toml`, $9.50 ceiling) were started detached on 28 September. Check them with `pgrep -af "study run"`, then read `runs/<study>/run.log` and `runs/<study>/summary.md`.

When each finishes:
- apply its spec's decision rule exactly as written;
- check `research breakdown` for 429s, 500s, and pacing waits, since the two ran at the same time;
- write its study-log entry, with run IDs, costs, and the decision, and update the index at the top of the log.

The trimming study's rule decides whether `RESEARCH_TRIM_HISTORY` defaults to off. The user expects trimming to hurt more than it helps, but the default changes only if the rule is met. The search study is a screen, so an engine can advance to a confirmation, but no default changes on its result.

## 1b. Find where budgets bottleneck runs

After the search study, and before the DeepSeek study, measure which limit actually stops each call, from the stored runs and for free: no model calls. The user asked for this on 28 September.

For every planner, scout, deep dive, gap analysis, and synthesis call, by workflow version, depth, and model:
- what stopped it: a limit (which one) or its own return;
- how much of its dollar share, time window, requests, productive calls, input tokens, and output cap it used;
- what the time went on: model time, tool time, pacing waits, and 429s.

For each run, also record hard-cap and study-ceiling refusals, and paid search and reading spend against each share. Report which limits bind, and how often a binding limit cost claims (a cut-off keeps none). Then propose changes with the data behind them. Write it as a reusable script, so each study can be checked the same way. Record the findings in the study log.

## 2. Run the DeepSeek rescout study

`studies/deepseek-rescout-task8.toml` compares Luna, DeepSeek Flash, and DeepSeek V4 Pro scouts, all at xhigh, on the same three plans ($10.50 ceiling, approved). Start it once one of the two running studies has finished, so that no more than two Luna studies run at once. If the trimming study turned trimming off, set `RESEARCH_TRIM_HISTORY = "false"` in every arm first and run its dry check again. Write it up as in step 1.

## 3. Commit, and ask about pushing

Commit the specs, the doc changes, and the study-log entries on `claude/example-glp1-alcohol`. Ask the user before pushing or opening a pull request. Before a push, run the Postgres tests with `RESEARCH_TEST_DATABASE_URL` set.

## 4. Design the top-down example

After the studies above, the user wants a top-down run: every setting at its best value, with a large budget, to see what quality is reachable before refining toward cost. The tooling so far was built bottom up, through small screens that cannot show the ceiling.

The design should include:
- the highest efforts: Luna@xhigh and DeepSeek@xhigh scouts, Sol@xhigh (or high) for the planner and gap analyzer, and Opus 5.5 at no more than medium;
- the best search engine and scout model from steps 1 and 2;
- deep depth with the follow-up, the reading fallback, and generous limits;
- which cases it runs on, what it is compared against (the stored deep and standard runs on drb2-task8), and how it is graded (both judges, the audit, and the diagnosis).

Bring the design to the user with an estimate from the most expensive comparable run and a hard ceiling. Then refine down: remove or cheapen one setting at a time and measure what each costs in quality.

## 5. Confirm any candidate that advanced

A search engine or scout model that advanced in steps 1 or 2 gets a confirmation. That means a new spec with three replicates per case on the four development cases, following the protocol in [evaluation.md](evaluation.md#serper-brave-and-deepseek-integration-study). A chain such as DuckDuckGo then Serper is a separate setting and needs its own comparison. Only a confirmation that meets its rule changes a default.

## 6. Measure the claim fix

scout-v13 contains the claim fix: one coverage item per category and field asked, category-level claims from reviews, and a claim for each named set member. It has not been measured. Run it on two or more development cases at standard depth with `diagnose = true`. Base the rule on the diagnosis's stage counts, and compute the detectable difference before any paid run. If it raises scores, rerun the key model comparisons on the new workflow. Confirm a fix on the held-out cases (drb2-task82, task59, and task78) only once one exists.

## Open questions for the user

- Should the paid baseline diagnosis of the six deep-vs-standard runs run ($0.50 to $0.80, $1.50 cap)? The user held it on 28 September.
- Deep against standard was undecided, and the GLP-1 example waits on it. The top-down run may settle whether deep depth is worth its cost.
- The ideas in [notes.md](notes.md) are unmeasured: cache warming, a source ranker, and provider usage policies.

## Done

Nothing has finished since this file was written.
