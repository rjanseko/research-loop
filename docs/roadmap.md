# Current roadmap

**29 September 2026.** The approved [passage-evidence plan](passage-evidence-plan.md) is the main work. The evidence-linked backlog and audit register are in [the project synthesis](project-synthesis.md), and what the studies so far support is in [the takeaways](takeaways-2026-09-29.md). The 28 September run queue is preserved unchanged in [the archive](archive/roadmap-2026-09-28.md); its scheduled studies and approvals are not current instructions.

**Paid calls are paused** until the user confirms that the contamination audit passes. The blocked-report exposure found on 29 September (study log) belongs to that audit; with DeepResearch Bench II removed, no current case has a blocked expert report.

1. **Land the old design and tag it.** Merge `claude/scout-v15` and the repository cleanup into master, and tag the merge `archive/pre-passage-2026-09`.
2. **Small robustness fixes, offline. Done 29 September:** every run role retries under one explicit policy and records its retries (B2, `scout-429-v6`); a run's uncertain spend counts against the study ceiling (B1, `usage-anchor-v8`); and a report without its title, summary, or answer makes the run partial, with invalid citations stored (S1, synthesis v10, `scout-v22`). See [the plan](passage-evidence-plan.md#robustness-work-outside-the-evidence-layer).
3. **Freeze new evaluation cases.** DeepResearch Bench II was removed on 29 September, so there is no broad development or held-out case. Review the [draft development set](example-evaluation-set.md), verifying the facts its rubric points rest on, and choose a held-out set at the same time, before any Scout output on either is read. This can go alongside steps 2 and 4, since no paid run needs it until step 7.
4. **Before archiving the database.** *Done 29 September:* `scripts/passage_replay.py` ran about 1,700 stored page windows through the passage splitter with no invariant failures, and put a run's full texts at a median of 0.73 MB (study log).
5. **Build the rebuild,** in the plan's [build order](passage-evidence-plan.md#build-order); step 1, the splitter (`passages.py`), is done: splitter, storage and checkpoints, tools, scouts, ledger and coverage, synthesis, rendering, evaluators, the dry-run world, and documentation. Archive and reset the database when the new migration baseline lands.
6. **The rebuild's two small paid checks,** once paid calls resume: scouts inventing handles, and the synthesizer's citations over passages. Each has a hard cap of $0.25 or less.
7. **A new baseline, with the user's approval.** Paired runs on the new development set, then its held-out set. It establishes the new design's level; no default changes on it.
8. **One-factor trials on the new design:** in-document search, then the passage selector on fixed ledgers, then scholarly relevance filtering. Each has its own spec and decision rule.

Do not treat a dated audit, an archived stage allowance, or a single screen as authorization for a paid run. The approval and dry and cheap requirements are in [AGENTS.md](../AGENTS.md); results belong in [study-log.md](study-log.md).
