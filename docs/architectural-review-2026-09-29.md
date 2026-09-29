# Scout architectural and code review — 29 September 2026

**Reviewed checkout:** `claude/citations-synthesis` at `626be7f` (`scout-v20`, `scout-synthesis-v8`). This is a review of the current experimental branch, not a claim about the unmerged default branch. The untracked root `audit.md` was read and left alone. No model or paid service was called.

## Verdict

Keep the bounded PydanticAI workflow. Its planner, parallel scouts, code-owned evidence ledger, optional gap follow-up, and fixed-plan/fixed-ledger experiments are useful boundaries. The principal architectural defect is that the system records increasingly precise *provenance-looking* data without a precise relationship between a displayed assertion and the original source passage that supports it. Several status and cost labels claim more than their inputs establish. Adding a supervisor, graph runtime, or another model role before fixing those contracts would add moving parts around the same uncertainty.

The Claude citation work improves one layer of the chain: a citation can be checked against a passage actually sent to Claude. It does not establish that the passage is original source text, that it entails every sentence to which code attaches the citation, or that the displayed source is the one used to grade support. The current `supported` label should be read as an internal traceability heuristic, not a verified report-quality verdict.

### Scope and method

I traced planning, retrieval, evidence checking, follow-up, synthesis, rendering, cost admission, storage, and study evaluation in source. I read the prior audits and current roadmap to distinguish inherited findings from new synthesis behavior. `.venv/bin/pytest -q` returned **392 passed, 3 skipped in 52.47 seconds**; the skips are the PostgreSQL integration tests gated by `RESEARCH_TEST_DATABASE_URL`. I also ran read-only synthetic probes against this checkout. They reproduced the support, missing-section, and quote-matching counterexamples below. I did not inspect live database records or run a paid comparison. A passing offline suite therefore does not measure report quality or failure frequency in production.

## Workflow review and ranked findings

**Priority key:** P1 means the affected guarantee is unreliable today; P2 means a significant reliability or quality gap for the next focused milestone; P3 means maintainability or experimental risk. “Reproduced” denotes an offline counterexample on this head. “Static” denotes a code path, without a full live run.

| ID | Priority | Finding | Basis |
| --- | --- | --- | --- |
| E1 | P1 | Citation, statement, and source support are not locally bound | Reproduced |
| E2 | P1 | Quote verification can change numeric meaning; source identity can merge distinct pages | Reproduced for numbers; static for URL identity |
| S1 | P1 | Incomplete synthesis can still be `complete` and `supported` | Reproduced |
| B1 | P1 | A study ceiling can count a lower-bound cost as a known cost | Static |
| R1 | P2 | A cancelled research wave loses completed scouts from the ledger | Static; also reproduced in the 27 September audit |
| E3 | P2 | Contradicting evidence is not citable; source authority fields are model-authored | Static |
| E4 | P2 | Coverage measures labels, not whether the requirement is established | Static |
| V1 | P2 | Grading and auditing do not inspect the same report the reader sees | Static |
| T1 | P2 | Fuzz never exercises a successful citation synthesis | Static |
| B2 | P2 | Ordinary synthesis may retry in the provider SDK | Static; prior offline client inspection |
| M1 | P3 | Passage selection, paid-call accounting, and compatibility seams need consolidation | Static |

### Planning and coverage: E4

The planner proposes coverage items and researchers attach `claim.covers` labels. [`coverage_states`](../src/research_loop/evidence.py) marks an item covered when *any* claim names its ID. It does not require a verified passage, an independent source, an adequate answer to every requested field, or a contradiction check. [`_answer_support`](../src/research_loop/scout.py) then relies partly on those states. A researcher can make a thin claim about a broad category and move a coverage item from open to covered; a gap analysis sees the same ledger representation and may not pursue it. This is a useful work tracker, but it is not a measure of coverage sufficiency.

**Change:** retain coverage IDs but add deterministic requirements per item: at least one relevant claim, its exact passage IDs, source-access tier, any unresolved contradictions, and whether the final report actually addresses the requirement. Evaluate omissions on held-out broad questions. Avoid adding another run role until this representation has been tested.

### Retrieval and evidence: E2 and E3

[`_key`](../src/research_loop/evidence.py) strips nonword characters during quote matching. On this head, a tool page saying `1.2 percent` verified a model quote saying `12 percent`; a page saying `-5` verified `+5`. These are not harmless formatting differences. [`_url_key`](../src/research_loop/evidence.py) also discards query strings, which can identify different pages or records on the same path. A shared URL key can let one page's text verify a citation to another. The existing tests do not prevent these counterexamples.

[`check_evidence`](../src/research_loop/evidence.py) verifies the quote against labeled tool output but leaves the rest of `SourceRef` largely as the scout wrote it: title, publisher, publication status, source type, and retraction flag. Those fields enter the source table and synthesis prompt. A verified quote does not verify their accuracy. `evidence_is_read` uses `source_access`, the most complete *any* returned portion of a source, rather than the access level of the exact matched quote. A snippet quote can consequently inherit `full_text` after an unrelated window of the same source was fetched.

The citation path compounds this distinction. [`EvidenceLedger.passages`](../src/research_loop/evidence.py) sends all supporting evidence as citable blocks, sometimes the scout's paraphrase or a cut excerpt rather than source text; contradicting evidence remains in the JSON brief but is not offered as a citable block. A synthesis can easily cite the supporting side of a dispute but cannot attach a native citation to the opposing passage.

**Change:** give every fetched or scholarly text a durable source record and passage locator: canonical work ID, fetched URL, content hash, access tier, character or page offsets, extraction method, and immutable text. Quote matching should preserve signs, decimals, units, and word order; allow only explicitly safe normalization. Reconcile model-written source metadata against tool records. Offer both supporting and contradicting passages to synthesis with code-owned stance labels, without treating a citation as an endorsement.

### Synthesis and support: E1 and S1

[`citations.py`](../src/research_loop/citations.py) now validates source, search-result index, block range, and cited text against the sent passages. This is valuable. It then marks *every sentence in a cited Claude text block* with every source ID attached to that block. A text block is not necessarily an atomic assertion. One cited block can contain several independent sentences, a heading, or text crossing `<summary>` and `<answer>` tags. [`cited_report`](../src/research_loop/citations.py) still creates one `ReportClaim` for the entire text block, with all cited claim IDs. Thus the new per-sentence markers can make unrelated sentences *appear* individually sourced without establishing that the cited passage supports them.

The support check uses [`support_level`](../src/research_loop/evidence.py) over **all supporting evidence behind each referenced claim**, not the exact source/passages cited by the report. In an offline probe, the visible answer cited only snippet source `s2`; the same claim also had a verified full-text source `s1`. The result was `statement_support=read` and `answer_support=supported`. [`audit_items`](../src/research_loop/audit.py) likewise supplies verified quotes from every source behind the claim, so a semantic auditor can approve text by looking at evidence other than its displayed citation. This is evidence laundering across sources, even though each Anthropic citation pointer is valid. The study log's v8 reduction in uncited sentences was an offline re-marking of a stored v7 reply, not new retrieval or proof that those sentences gained support.

[`_cited_report`](../src/research_loop/scout.py) notices missing title, summary, or answer sections but treats the first two as notes. A synthetic answer-only response produced empty title and summary and still had `status=complete`; a cited answer could still be `supported`. [`_answer_support`](../src/research_loop/scout.py) also ignores `uncited_sentences`. Its documentation describes how report statements are backed, but statements are generated from cited text blocks, so uncited factual prose is absent from the denominator. `mismatched_citations` are noted and dropped but not placed in `RunChecks.citation_problems`.

**Change:** parse the reader-visible report into location-addressed assertion units, including summary, answer, and tables. Bind each unit to the exact cited passage IDs and compute provenance grade from those passages only. Preserve invalid or removed citation diagnostics as structured checks. Separate three statuses: structural completeness, provenance integrity, and semantic entailment; make “not checked” explicit. A successful report should require its required sections or be explicitly partial. A model auditor should see the same assertion and the same cited passage the reader sees.

### Scheduling, interruption, and persistence: R1

[`_research`](../src/research_loop/scout.py) collects scout tasks, waits for the wave, and returns a list. [`execute`](../src/research_loop/scout.py) adds those results to the ledger only after `_research` returns. If the parent run is cancelled while the wave is active, completed calls may already be in `run_calls`, but the run's ledger is still empty when [`_recorded`](../src/research_loop/scout.py) finalizes it. Deep dives likewise enter the ledger only after a whole `gather` completes. This wastes paid findings and makes cancellation appear less productive than it was.

**Change:** commit each checked `ResearchResult` to Postgres as a scout finishes, with a stable `(run_id, question_id, attempt)` key and an idempotent ledger merge. At restart, recover completed results and rerun only unfinished assignments. This can be implemented at the current workflow boundary; it does not require a new agent role or a graph runtime. Test cancellation after one of two scouts completes, then resume and compare ledger and cost records.

### Money and rate limits: B1 and B2

The per-command [`StudyBudget`](../src/research_loop/study_budget.py) reserves a conservative charge before dispatch and retains a reservation when a failure might have been billed. That is good. But [`_spend`](../src/research_loop/scout.py) marks an unpriced call while accumulating only known charges; the finished run still reports a numeric `cost_usd` lower bound. [`Outcome.charged_usd`](../src/research_loop/study.py) counts that numeric value, and the study runner reserves the step cap only when the record's cost is `None`. It does not use `checks.study_budget_reserved_usd` or the `unpriced` review reason. A later run may therefore start under a ceiling that has not accounted for earlier unknown charges. The same concern applies to paid-search/read reservations after uncertain failed requests, which are kept by the guard but may not enter reported `ExternalSpend`.

The ordinary synthesizer is built by [`role_model`](../src/research_loop/models.py) with `sdk_retries=None`; the Anthropic SDK default is two retries. The hard-capped study path sets zero. Therefore “one request, never retried” accurately describes the PydanticAI agent request and the hard-capped client, but not necessarily the ordinary client's HTTP attempts. The server-side Opus fallback is another attempt inside that request and is now priced separately when its usage is returned.

**Change:** report known charge and uncertain reserved exposure separately; charge the latter against a study ceiling until reconciled. Explicitly set synthesizer SDK retries to the intended value in ordinary runs. Persist attempt-level model, search, and reading spend, including uncertain outcomes, and reconcile them against provider bills when possible. Coordinate the token pacer across concurrently running study processes only if the 429 telemetry shows the per-run pacer is insufficient.

### Evaluation and tests: V1 and T1

[`reader_text`](../src/research_loop/evals.py) deliberately omits the executive summary to preserve historical grade comparability, while including the answer and a separate generated claim list. [`audit_items`](../src/research_loop/audit.py) audits those claims, and [`assessment_input`](../src/research_loop/quality.py) supplies a different, fuller report view. As a result, “the report was graded” means different text under different evaluators, and an uncited summary assertion can escape the rubric and support audit. Historical scores can stay versioned, but new selection decisions need one reader-visible projection and clear stage-specific metrics.

The offline [`FuzzModel`](../src/research_loop/dryrun.py) returns arbitrary text when no output tool is requested. The Claude synthesizer is text-output, so fuzz runs generally never produce a tagged, cited `FinalReport`; `check_record` also recomputes some expectations using the same production functions. Passing fuzz runs therefore do not validate successful synthesis, citation mappings, completion labels, or all money paths. The three skipped PostgreSQL tests leave actual persistence untested in this review.

The experimental branch has one live Haiku citation smoke and one Opus 5.5 resynthesis on synthesis v7, plus an offline v8 re-marking of that stored Opus reply. The study log says neither live synthesis was graded, and the current v8 path has not had its own paired graded run. The development case `drb2-task8` was contaminated by model-visible examples in earlier versions; absolute scores on it do not establish generalization. Keep failures and partial reports in the study denominator.

**Change:** add a successful synthetic citation stream to the offline world, with multi-sentence and cross-section blocks, mismatched citations, contradictory passages, fallback and refusal events, and unknown charges. Create a new evaluator version that judges the *rendered* summary, answer, tables, and citations. Calibrate it against human-marked assertion samples before it chooses a default; preserve the old rubric reader for historical comparisons.

### Maintainability: M1

The largest modules (`scout.py`, `dryrun.py`, `study.py`) contain cohesive owners but have become difficult to review. The strongest extraction opportunities are **policies with one invariant**, not a general orchestration framework:

1. A source/passage identity service used by search, fetch, scholar, evidence checking, citation validation, and audit.
2. One paid-call accounting context for model, Exa/Serper/Brave search, and Exa/Firecrawl reading. Several adapters repeat reserve → send → release on 429 → settle, and their uncertain-error paths are not surfaced consistently.
3. A report assertion/provenance object used by rendering, checks, rubric reading, quality, and support audit.
4. A small study-stage adapter for the similar grade, audit, and diagnose subprocess wrappers, while keeping distinct evaluator schemas and versions.

`CitingAnthropicModel` overrides private PydanticAI methods. It is understandable while native search-result citations are unavailable, but a dependency range that permits upgrades needs compatibility tests against each permitted PydanticAI release used in CI. This is a contained adapter, not a reason to replace PydanticAI.

## Lessons from other research projects and exact integration points

These are comparisons of documented mechanisms, not a performance ranking. The systems use different questions, corpora, models, budgets, and evaluators.

| Project or framework | Documented mechanism | Fit in Scout | Test before adopting |
| --- | --- | --- | --- |
| [PaperQA2](https://github.com/Future-House/paper-qa) | Retrieves more candidate passages than it sends to the answer model; ranks for relevance and diversity, then caps answer sources. | Add source-located chunks and an optional *ephemeral* passage selector between `EvidenceLedger` and synthesis. Start with lexical ranking plus coverage and source-diversity constraints; no vector database is necessary. | On fixed ledgers, measure decisive-fact recall, citation support, context tokens, cost, and latency against the current all-passages path. Never rank by source authority alone. |
| [STORM](https://github.com/stanford-oval/storm) | Uses perspective-guided question asking and an outline between research and article writing. Its maintainers explicitly describe the output as useful pre-writing material, not publication-ready. | For broad questions, enrich `ResearchPlan.coverage` with a few explicit perspectives and derive an evidence-grounded outline from established coverage before synthesis. Keep the current planner and scout roles. | Held-out broad cases: coverage of independent dimensions, material omissions, unsupported assertions, and cost. Do not spend the extra planning work on quick factual questions. |
| [DeepResearchGym](https://www.deepresearchgym.ai/) | Reproducible search sandbox to reduce variability from live commercial search. | Add an evaluation-only `WebSearch` adapter for its fixed corpus, retaining the current live engines for normal runs. | Same frozen tasks and corpus across arms; compare retrieval recall and failure behavior before report scores. Check corpus match to Scout's target use cases. |
| [DeepResearch Bench II](https://github.com/imlrz/DeepResearch-Bench-II) | Expert-report-derived rubrics separate information recall, analysis, and presentation; the official evaluator requires evidence from the submitted report. | Continue using its held-out tasks, but report those three dimensions and the retrieval/synthesis stage at which each point was lost. Never tune prompts on held-out rubrics. | One blinded, versioned evaluator and human checks of disputed points; paired replicates and failure-inclusive results. |
| [Pydantic Evals](https://pydantic.dev/docs/ai/evals/evals/) / Logfire | Code-defined datasets, experiments, output and trajectory evaluators, and trace comparison. Scout already imports its evaluator classes. | Consolidate the study metrics into reusable evaluators over the exact rendered report and run trajectory; keep Postgres as the run of record. | Verify that CLI/study and Evals projections agree for the same stored run. |
| [DSPy](https://dspy.ai/3.2.0/learn/optimization/overview/) | Optimizes prompts against an explicit metric and training examples. | Use offline to propose planner or scout instruction variants only after the metric is calibrated; export a frozen prompt into `prompts.py`. No runtime dependency is needed for the first experiment. | Development/validation separation; one untouched held-out read; budgeted paired comparison. Otherwise it optimizes benchmark leakage. |
| [LangGraph checkpoints](https://github.com/langchain-ai/docs/blob/main/src/oss/langgraph/persistence.mdx) | Stores workflow state between steps, with PostgreSQL checkpointers available. | Consider only if the simple Postgres result-checkpoint approach above cannot meet a measured recovery requirement. Map the existing phases to graph nodes and keep PydanticAI agents. | A forced-crash/resume experiment that proves no duplicate paid calls or lost results, weighed against migration and storage cost. |

[LangChain Open Deep Research](https://github.com/langchain-ai/open_deep_research) provides a useful reference for research briefs, supervision, and compression, but its repository is archived and its more elaborate supervisor topology is not evidence that Scout needs more run roles. [GPT Researcher](https://docs.gptr.dev/docs/gpt-researcher/getting-started/introduction) demonstrates that Scout's plan/search/read/report structure is ordinary for this class of system. Their lesson for this repository is to improve retrieval and evaluation within the existing boundaries first.

## Recommended sequence and acceptance gates

1. **Repair truth-preserving deterministic checks.** Add regression tests for decimal/sign changes, ordered quote segments, query-distinct source identity, quote-access promotion, and blocked-source identity. Version evidence behavior when it changes. Acceptance: every synthetic counterexample above fails the integrity check before a model auditor is involved.
2. **Make displayed assertions the unit of provenance.** Persist sentence/table-cell location, exact cited passage IDs, and unresolved or contradictory evidence. Structural completeness and invalid citation counts must affect the run record. Acceptance: the snippet-source probe cannot be `read/supported`; an uncited summary assertion appears in the audit; a missing required section cannot be a complete report.
3. **Make spend and interruption accounting truthful.** Persist each completed scout result immediately; carry uncertain reservations into study ceiling accounting; set ordinary synthesis retry policy explicitly. Acceptance: kill a two-scout wave after one completes and recover its checked evidence without a second paid call; unknown usage never releases ceiling room.
4. **Give the test world a successful synthesis path.** Its cited stream should exercise report creation, partial output, fallback, and budget interactions. Run PostgreSQL integration tests against a disposable test database in CI and locally when configured.
5. **Only then run a selection study.** Compare the old and new synthesizers on the *same stored ledgers and same Opus model*, rotate arm order, and predeclare margins for factual support, completion, cost, and latency. Assess the whole reader-visible report; record failures. A single st07 resynthesis is a smoke check, not a quality estimate. The roadmap currently pauses paid work until its contamination audit passes, so this review makes no paid run proposal executable by itself.

The best first external idea is PaperQA2's passage selection, but only after Scout can identify and audit exact source passages. STORM's perspective coverage is the next likely quality experiment for broad reports. Checkpointing and prompt optimization should follow their own measured failure or quality bottlenecks.
