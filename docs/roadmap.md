# Roadmap

This is the working plan as of 28 September 2026. A session that resumes the work starts here, checks which step is current, and updates this file when a step finishes or the plan changes. Finished steps move to the bottom, with a link to their study-log entry. Each study's own decision rule is in its spec header, and its results go in [study-log.md](study-log.md).

Every paid step follows AGENTS.md: a spec with its decision rule, a clean dry check, a cheap check only when an arm uses something without a real run on the current code, and the user's approval with an estimate and a hard ceiling. Steps 1 to 3 are already approved. Steps 4 onward each need approval before they spend anything.

## Status on 28 September, evening

The trimming study finished and turned trimming off. The first search study was stopped after request timeouts, and scout-v14 raised the scout request timeout to 600 seconds and the time, call, and request limits (study log, "Trimming off, and limits as safety nets"). The search study is rerunning as `search-rescout-task8-v14`: check its first plan's four rescouts for failures before letting the rest run. The DeepSeek study follows it on scout-v14, with its dry check rerun first.

## 1. Finish the two running studies

The trimming study (`studies/trim-history-rescout-task8.toml`, $3.00 ceiling) and the search-engine study (`studies/search-rescout-task8.toml`, $9.50 ceiling) were started detached on 28 September. Check them with `pgrep -af "study run"`, then read `runs/<study>/run.log` and `runs/<study>/summary.md`.

When each finishes:
- apply its spec's decision rule exactly as written;
- check `research breakdown` for 429s, 500s, and pacing waits, since the two ran at the same time;
- write its study-log entry, with run IDs, costs, and the decision, and update the index at the top of the log.

The trimming study's rule decides whether `RESEARCH_TRIM_HISTORY` defaults to off. The user expects trimming to hurt more than it helps, but the default changes only if the rule is met. The search study is a screen, so an engine can advance to a confirmation, but no default changes on its result.

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
