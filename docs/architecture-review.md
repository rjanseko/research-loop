# Architecture review and implementation roadmap

Review date: 2026-09-23. Baseline: commit **cdf2de1**, plus the uncommitted cleanup changes from the preceding review. This report evaluates the current working tree; line links point into that tree and will drift as it changes.

**Status after the consolidation pass (same day).** Graph and legacy now share one job lifecycle (`_job_scope`, `_create_job`, `_finish`) and the planner and gap-analysis calls (`_plan`, `_analyze_gaps`), so the graph no longer reads the loop's agent attributes, and campaign code uses public experiment helpers (both part of finding 10). The cache, memo, rate-limit, and guarded-download code shared by the web and scholarly tools moved from `scholar.py` to `acquisition.py` (the first part of finding 12). Graph steps still call the loop's private methods, and the runtime still reloads settings. Prompts and topology did not change. The suite now has 166 tests and fails any test that reaches the network. Line numbers in the links below had drifted and were removed; the links point at files, and the text names the code.

Later the same day, the stale p01 pilot outputs were archived to `benchmark_outputs/archive/`, and the whole-file spec-hash fallback described under the quick fixes was removed: a run without an objective hash no longer counts, so the pilot needs p01 rerun before synthesis.

Finding 7 is partly addressed: cancellation (including Ctrl-C) and graph-cancelled sibling branches now leave failed job, task, and manifest records with error type `CancelledError`; those writes are shielded and time-bounded, and a failed write no longer replaces the primary error. Runs still have no deadline, nothing reconciles records left by a killed process, and the legacy loop's `asyncio.gather` still leaves sibling scouts running after one fails. Interrupted pilot job `b8bff92d` was reconciled by hand.

Finding 2 is mostly addressed (`evidence_version` 3): each agent's output is checked against the run, with one retry and then a failed run. Plans need at least one question, unique IDs, and no more than the policy's maximum; research results are filed under the question asked; attachment citations must name run attachments; gaps and verifier follow-ups must name planned questions, so follow-ups outside the plan no longer buy an empty round; and report and verifier citations must be ledger claim IDs. The archived pilot shows why the last matters: it ran before claim IDs were unique, and all 38 of its report citations used a `q1:c1` form that matched no ledger ID; its other outputs would have passed. A quality disposition now sits beside execution status: `ResearchOutcome.review_reasons` names what a finished run left unresolved, and benchmark run records and campaign `run.json` files and manifests carry it (it is not stored in Postgres). Cited sources are now checked against each research run's tool output (`source_check`), surfaced to the synthesizer, verifier, campaign synthesis, and a benchmark metric; the review's fuller acquisition-observation record is not built. Still open: the held routing fix for `max_deep_dives_per_round = 0`.

Finding 5 is addressed for the normalized tool stack (`fetch_version` 3): a job-scoped `SourcePolicy` with an explicit matching rule is enforced in `web_fetch` and `scholar_fetch` before the cache, DNS, or any request and at every redirect; evidence citing a blocked source gets a retry; and the benchmark audit separates refused fetches, completed fetches, search sightings, and citations, with compliance failing only on the last two. Provider-native tools in `adaptive` mode remain outside enforcement and are only audited. Typed acquisition events are not stored as their own records; the audit reads in-memory tool events.

Revised the same day after a second pass: findings re-ranked by how silently they corrupt results, low-effort fixes separated from the restructure, and three probes added (invalid verifier follow-ups, empty plans, zero scout concurrency).

**Recommendation.** Keep PydanticAI, Pydantic Graph, the evidence ledger, and Postgres. Make a focused internal restructure around run lifecycle, canonical evidence, and bounded prompts. The graph is a useful description of the research algorithm. The largest problems occur in the contracts around it: what counts as a successful run, whether citations resolve, whether campaign artifacts belong together, and how much evidence a model receives.

The repository is a capable local research prototype with several unusually good foundations. Its current results need stronger integrity guarantees before it can reliably compare model policies or support larger campaigns. Expanding the number of stages, agents, or concurrent workers would increase costs and amplify the existing gaps.

This review included the orchestration, graph, schemas, policy, acquisition, attachments, repositories, benchmark/evaluation, campaign, settings, diagnostics, migrations, and their tests. The full suite passed: **76 tests in 3.47 seconds**. I also ran deterministic probes with fake agents and temporary fixtures, and measured existing pilot artifacts without printing their research content. No paid model calls, live database tests, or deployment tests were performed. Provider availability and current pricing were not revalidated. The installed versions were PydanticAI, Pydantic Graph, and Pydantic Evals 2.48.0, with Pydantic 2.13.5.

**What is working well.**

| Area | What the implementation gets right | Why it is worth preserving |
|---|---|---|
| Workflow | Explicit planning, map/join research, gap analysis, synthesis, and verification | The algorithm is inspectable and its topology can be versioned independently. |
| Parallel evidence | Workers return typed results; serial joins sort by input ordinal before ledger updates | Provider timing does not arbitrarily reorder evidence or create concurrent ledger writes. |
| Policy | Role selection and per-call budgets live in ModelPolicy and ModelRoute | Vendor-specific choices are mostly outside the graph. |
| Provenance | Claims carry evidence, source metadata, contradiction records, and attachment locators | The data model can support stronger grounding without a wholesale redesign. |
| Source distinctions | Scholarly adapters retain provider records and explicit preprint/publication status | This avoids silently equating preprints with reviewed publications. |
| Evaluation conditions | Normalized acquisition and explicit attachment modes | The project recognizes that tool differences can confound model comparisons. |
| Persistence | A small repository protocol, optional Postgres, and checksummed SQL migrations | Durable storage is separated from model behavior without an unnecessary framework. |
| Operations | Synthetic runs, diagnostics, paid CLI opt-in, cost accounting, and failure categories | There are useful ways to exercise local wiring before spending money. |
| Campaign citations | Campaign synthesis validates claim references and retries invalid output | This is an existing pattern that can be applied to the ordinary research pipeline. |
| Tests | Fast deterministic tests, a graph/legacy parity scenario, mocked acquisition, and salvage tests | The repository has a good base for adding meaningful failure and integrity coverage. |

The strongest design choice is the separation between workflow control and evidence storage. Preserve the serial evidence joins in [graph.py](../src/research_loop/graph.py). The combination of typed outputs, an explicit topology, and a small repository abstraction is appropriate for the present scale.

**Priorities.** P1 findings produce silently wrong, misleading, or leaked results; address them before treating any benchmark comparison or campaign synthesis as dependable. P2 findings block scale, weaken durable history, or need structural work, but they either fail visibly or affect only Postgres-backed and unattended use; address them during the next hardening/refactoring cycle. P3 findings need an unlikely failure or a self-authored input; fix them opportunistically. Findings 1–5 are P1, 6–13 are P2, and 14–15 are P3. These priorities concern the local lab and its results; this is not a claim of an exhaustive security audit.

**Quick fixes.** Several findings have fixes of a few to a few dozen lines that do not depend on the restructure. Land these first, each with its probe as a regression test. They remove the most misleading current behavior; they do not replace the structural solutions below. All but one are now implemented in the working tree with regression tests (91 tests pass); line links in this section point at the code as it was before those changes.

| Fix | Where | Finding | Status |
|---|---|---|---|
| Print evaluation reports with `include_errors=False`; inspect `report.failures` before marking the manifest completed; exit non-zero when any case failed | [benchmark.py](../src/research_loop/benchmark.py) | 1 | Done |
| Do not start another synthesis/verification round when the verifier's follow-ups select no deep-dive work; apply the same check in the legacy loop | [graph.py](../src/research_loop/graph.py), [async_orchestrator.py](../src/research_loop/async_orchestrator.py) | 2 | Held: changes research-graph-v1 behavior, which AGENTS.md freezes during benchmark work |
| Load verification.json into CompletedQuestion and add unsupported or major checks to the campaign synthesis prompt | [campaign.py](../src/research_loop/campaign.py) | 3 | Done |
| Record a hash of each question's rendered objective in run.json, and leave out (and report) completed questions whose objective no longer matches the spec | [campaign.py](../src/research_loop/campaign.py) | 3 | Done |
| Return `{}` (not applicable) from SupportedClaimRate and MajorErrorFreeRate when no claims were checked, from BlockedSourceCompliance when a case has no blocked URLs, and from EvalIntegrity when a case is not leakage-sensitive; AttachmentCitationCoverage already follows this pattern | [evals.py](../src/research_loop/evals.py) | 4 | Done |
| Report cost as unknown when any task with billed requests has no price; make `cost_usd` optional and have CostEfficiency skip unknown cost | [benchmark.py](../src/research_loop/benchmark.py) | 4 | Done, using the job's own spend ledger |
| Validate ResearchConfig bounds in `__post_init__`: positive concurrency and per-round limits, non-negative rounds, confidence within [0, 1] | [async_orchestrator.py](../src/research_loop/async_orchestrator.py) | 11 | Done |
| Reject `.`, `..`, and the reserved folder names `campaign` and `manifests` as campaign question IDs | [campaign.py](../src/research_loop/campaign.py) | 14 | Done |

The objective check replaced an earlier plan to compare whole-file spec hashes. The pilot showed why: p01 ran under a pilot.toml whose later edits changed only budgets, so a whole-file hash would have discarded evidence that still answers the same question. Budget differences are already visible through configuration fingerprints. Runs recorded before objective hashes fall back to the whole-file comparison, so p01 is now left out of pilot synthesis until it is rerun.

Two of these fixes change model-visible behavior: the held follow-up check would alter routing inside research-graph-v1 (not its edges), and the campaign prompt change alters synthesis inputs and the campaign synthesizer's instructions. Record both as behavior changes, following the versioning note under the proposed structure, and keep graph/legacy parity.

**Findings and solutions.**

1. **P1 — Benchmark failures can be presented as successful experiments, and provider error bodies can reach console output.**

   In [benchmark.py](../src/research_loop/benchmark.py), dataset evaluation returns a report, that report is printed, and the manifest is then marked completed. With the installed Pydantic Evals version, a task exception is recorded in the evaluation report instead of escaping this call, and `report.print` includes recorded errors by default.

   A local probe forced the only case to fail. The function returned normally with a manifest whose overall status was completed and whose sole run had status failed. A second probe raised a ModelHTTPError containing a dummy private-body marker. The marker appeared in captured console output despite include_input=False and include_output=False. The CLI's sanitized outer exception handler did not handle this path because the evaluator had already captured the exception.

   This undermines unattended experiment tracking and the repository's intent to keep protected inputs out of logs.

   **Solution:** the quick fixes stop the misleading status and the default error printing. Beyond them, distinguish completed, completed_with_failures, failed, and cancelled; define CLI exit behavior for unsuccessful cases; and render failure summaries using a deliberate safe projection. Preserve exception category, HTTP status where appropriate, task ID, and a correlation ID. Exclude raw provider messages and response bodies from the default printer. Persist safe per-case evaluation records before reporting completion.

   **Acceptance:** all-failed and partially-failed suites get correct terminal states and exit codes; a dummy provider-body marker never reaches stdout, stderr, exported traces, or the safe manifest; every scheduled case has a terminal result or a recorded cancellation.

2. **P1 — Typed evidence is not yet an enforced grounding contract.**

   [FinalReport and VerificationReport](../src/research_loop/schemas.py) accept empty or arbitrary claim-reference lists. Ordinary synthesis and verification depend on instructions to check those references. Only the [campaign synthesizer](../src/research_loop/agents.py) has an output validator enforcing reference existence.

   A scripted graph run returned a report citing does-not-exist and still completed successfully. That proves the runtime lacks a deterministic rejection gate; it does not imply that every real model would make this mistake.

   The same gap wastes paid calls. [route_verification](../src/research_loop/graph.py) checks only that the verifier returned follow-ups, not that any of them selects deep-dive work. A follow-up naming a question ID absent from the plan is later dropped by gap selection, yet synthesis and verification run again on an unchanged ledger. With max_verification_rounds=2, a scripted run made three synthesizer and three verifier calls and no deep dives; the [legacy loop](../src/research_loop/async_orchestrator.py) behaves identically. These are the two full-ledger calls, about $0.81 per round in the pilot. Likewise, a planner that returns zero questions leads through gap analysis, synthesis, and verification on an empty ledger to a succeeded job.

   SourceRef validates URL syntax or the presence of an attachment ID. It does not prove that an attachment exists in the run, that a URL was observed by a tool, that the locator is valid, or that an excerpt supports the claim. The verifier sees the scout-produced evidence and has no independent retrieval tools in its current invocation. It therefore audits consistency with supplied evidence, with the same factual limitations as that evidence.

   **Solution:** add output validators with typed dependencies containing the expected question ID, known claim IDs, known attachment/chunk IDs, and observed source IDs. Reject duplicate planner IDs, mismatched worker question IDs, unknown report references, and invalid verifier follow-ups. Validate structural relationships deterministically. Keep semantic support assessment explicit as a separate, fallible judgment.

   Introduce a small acquisition-observation record linking source identity, retrieval time, content digest, locator, and tool/task identity. Evidence should refer to an observation where the normalized tool stack can supply one. Treat quoted spans and paraphrases differently: exact excerpts can be checked against recorded chunks; paraphrases still require judgment. This has since landed as evidence version 2: quotes are checked against the research run's own tool output and the result is surfaced to the verifier, campaign synthesis, and a benchmark metric; observation records and locator checks have not. A URL and a high model confidence score are insufficient evidence of retrieval.

   Also separate **execution status** from **quality disposition**, for example succeeded + needs_review. Reaching the verification-round limit can validly finish execution while leaving major findings unresolved, and an empty plan can finish execution without researching anything. Both should be visible in ResearchOutcome, campaign inputs, and benchmark output.

   **Acceptance:** unknown IDs and fabricated attachment references trigger a bounded retry or explicit invalid-result status; zero checked claims is recorded as unassessed; every final claim reference resolves to the canonical ledger; unresolved major findings survive every export and aggregation step; follow-ups that select no work do not trigger another synthesis round; an empty plan cannot finish without an explicit quality disposition.

3. **P1 — Campaign synthesis discards verifier findings and accepts stale artifacts.**

   [CompletedQuestion and aggregate_campaign](../src/research_loop/campaign.py) load the report and ledger but omit verification.json. [synthesis_prompt](../src/research_loop/campaign.py) includes report text, caveats, and ledger content, so verifier-only warnings disappear before campaign synthesis. A local fixture's unsupported assertion reached the synthesis prompt while its major verifier warning did not. This happens on every campaign synthesis, not only on failure paths. In the pilot, the verifier flagged 11 of 45 checks, 5 of them major and unsupported; none of that reached campaign synthesis.

   The aggregator also accepts a completed run.json with the wrong campaign specification hash, so editing campaign.toml and re-aggregating combines evidence gathered under the old specification with the new one. Reporting mixed configuration fingerprints later does not validate that the evidence answers the current questions or matches the current publication window.

   **Solution:** include typed verification results in CompletedQuestion and the synthesis evidence view. Keep unsupported/contradicted findings visible and attributed. Validate the campaign ID, question text or hash, and campaign-spec hash before aggregation. Allow deliberate mixed-policy synthesis only through an explicit recorded policy.

   **Acceptance:** a major verifier warning is present in campaign synthesis inputs; stale-spec question outputs are rejected or reported as missing, never silently aggregated.

4. **P1 — Benchmark metrics report passes and complete costs they cannot support.**

   [Evaluation metrics](../src/research_loop/evals.py) largely use the run's own verification output. This measures internal groundedness as judged by the configured verifier. It is not an independent measure of factual correctness. Changing verifier policy can change the score even when answer quality is similar, and supported-claims-per-dollar is sensitive to how many small claims a model emits.

   Concrete issues include a perfect MajorErrorFreeRate when there are no checked claims (SupportedClaimRate scores the same case 0.0), unknown model costs being skipped by [_sum_usage](../src/research_loop/benchmark.py), and evaluation results being printed without a durable score artifact. A probe with one known $1 task and one unpriced task reported $1 as the aggregate, with no completeness flag. BlockedSourceCompliance and EvalIntegrity also report a pass when their audit is inapplicable to the case.

   **Solution:** persist one typed case result containing terminal status, raw metric values, applicability, denominators, cost completeness, integrity flags, and evaluator versions. Separate internal groundedness scores from independent reference/official evaluation. Record missing cost as unknown or partial rather than a complete dollar amount. Require verification coverage for claims about error-free output. Keep official grading distinct from the existing fast exact-match smoke metric.

   **Acceptance:** scores and audit flags can be read after process exit without reparsing terminal output or requiring raw protected tool content; unknown cost remains unknown; unassessed cases are not counted as verified successes.

5. **P1 — Source restrictions are instructions and retrospective heuristics, not tool-enforced access policy.**

   [ResearchConstraints](../src/research_loop/schemas.py) reaches prompts, but the constructed web and scholarly toolsets do not receive a blocked-source policy. The audit in [benchmark.py](../src/research_loop/benchmark.py) searches serialized tool arguments and results for URL substrings.

   A local probe showed that a blocked URL merely appearing in a search result is counted as a blocked access. Conversely, the fetch result does not consistently preserve the final redirect destination, which limits retrospective detection. Search-result exposure, attempted access, completed fetch, and use as evidence are different events.

   This is P1 for benchmark suites that declare blocked URLs, because the compliance metric can err in both directions. Campaigns pass no blocked URLs.

   **Solution:** pass a run-scoped source policy into acquisition tools. Enforce it before fetches and at each redirect. Emit typed acquisition events identifying the action, requested URL, final URL, document ID, and policy decision. Audit evidence eligibility separately from network access. Define URL normalization and match rules explicitly rather than case-folding every URL path and using substring tests.

   This can be enforced most strongly in the normalized benchmark lane. Provider-native acquisition needs a documented capability boundary because the application may not control every underlying network action.

   **Acceptance:** blocked direct and redirected fetches perform no prohibited request in mocked transport tests; seeing a URL in search results is recorded separately; forbidden ledger sources cannot silently become accepted report evidence; exported metrics distinguish observed compliance from unobserved activity.

6. **P2 — The canonical evidence IDs are not the IDs stored in task outputs.**

   [Agent execution](../src/research_loop/async_orchestrator.py) persists the worker's output before [EvidenceLedger.add](../src/research_loop/ledger.py) rewrites claim IDs. Graph joins persist no canonical ledger snapshot or explicit remapping.

   In a synthetic run, the stored task output contained synthetic-claim-1 while the final report cited synthetic-q1/synthetic-claim-1. That reference was absent from the stored task-output IDs. Campaign exports happen to write the canonical in-memory ledger, but ordinary Postgres jobs do not get that artifact.

   The data may be reconstructible by replaying normalization in the right order; the database does not store the authoritative mapping directly. This weakens the stated durable-history boundary and makes later analysis depend on implementation details. The ledger is also append-only by convention: its public results dictionary and returned model objects remain mutable. Canonical committed records should be protected from subsequent caller mutation.

   This is P2 because the paths in current use keep the canonical ledger: campaign exports write it, and in-memory benchmark runs have no durable history to lose. Postgres-backed history is what lacks it.

   **Solution:** choose one canonicalization boundary and persist its exact result. The existing serial graph record steps are a natural place. Have the ledger return a committed result with stable claim IDs and preserve the originating task/result ID. Add a repository operation for canonical research results, or an explicit mapping plus an immutable ledger snapshot. A small Postgres table or JSON document is sufficient; event sourcing is unnecessary.

   Keep raw model output distinct if it is useful for diagnostics, but make every report reference resolvable through the canonical record. Avoid relying on timestamps to recover provenance.

   **Acceptance:** reload a completed job after process exit and resolve every final-report and contradiction reference without rerunning agent logic; repeated ingestion is idempotent; duplicate worker-local IDs remain unambiguous.

7. **P2 — Run cancellation is not a persisted terminal state, and runs have no deadline.**

   The main lifecycles catch Exception in [orchestrator.py](../src/research_loop/orchestrator.py) and [async_orchestrator.py](../src/research_loop/async_orchestrator.py). asyncio.CancelledError bypasses those handlers. A local cancellation probe left both job and task marked running while the finally block cleared spend state.

   A database or process interruption also has no reconciliation mechanism for stale running records. The code correctly avoids claiming crash-resumable graph execution, but it still needs an accurate account of abandoned work. Nothing bounds total run time either: a run that stops making progress, such as the zero-concurrency probe in finding 11, waits indefinitely. The plain-async legacy implementation uses asyncio.gather without explicit sibling draining on ordinary failure; its failure behavior needs coverage alongside the graph.

   **Solution:** introduce a run-scoped lifecycle object holding identity, deadlines, admitted calls, spend accounting, and task/service lifetimes. Persist cancellation explicitly, drain or cancel owned work, and preserve the primary error if cleanup persistence fails. Use a bounded cleanup window, then re-raise cancellation. Add a deliberate stale-run reconciliation command or lease policy; do not automatically rerun paid calls without an idempotency design.

   **Acceptance:** cancellation during an agent call and between graph stages leaves terminal records; a run that exceeds its deadline reaches a terminal state; no owned model task remains alive after the run returns; primary exceptions survive cleanup failures; stale process-abandoned records are distinguishable from live work.

8. **P2 — Repeated full-ledger prompts are already a demonstrated scalability bottleneck.**

   This finding confirms the measurements already documented in [PROMPT_SIZES.md](../campaigns/long_horizon_agentic_se/PROMPT_SIZES.md) rather than adding new ones. It is P2 because the failure is loud: campaign synthesis rejects an oversized prompt before any paid call. It still blocks full-campaign synthesis. The current pilot artifacts reproduce those measurements:

   | Current pilot measurement | Value |
   |---|---:|
   | Completed campaign questions | 1 |
   | Research results / claims | 4 / 45 |
   | Serialized per-question evidence ledger | 97,291 characters |
   | Same ledger with null fields omitted | 86,421 characters |
   | Reduction from omitting nulls | 11.2% |
   | Campaign synthesis prompt | 82,719 characters |
   | Question/report portion | 23,728 characters |
   | Evidence portion | 57,910 characters |
   | Campaign prompt cap | 360,000 characters |

   [Synthesis and verification](../src/research_loop/async_orchestrator.py) serialize the full ledger again, and verification adds the report. Gap analysis also receives all results. Campaign synthesis repeats source metadata and includes both reports and their underlying claims. A straight-line projection of eleven pilot-sized questions is roughly 0.9 million characters, above the configured cap. Carrying verifier findings into campaign synthesis (finding 3) adds about 5,800 characters for the pilot question. The token budget binds as well: PROMPT_SIZES.md estimates about 367,000 Opus input tokens per request for eleven questions, against a 400,000-token synthesis budget that must also cover output and up to two retries. That projection is not a measurement of eleven completed questions, but the single-question measurement is enough to establish the current capacity problem.

   The local pilot report also records large cumulative scout input usage from repeatedly sending tool history. Fetch tools return document prefixes, and salvage keeps bounded oldest-first result prefixes. Increasing loop limits can therefore increase token cost while still failing to expose the relevant later passages.

   **Solution, in order:** first build a shared prompt projection with omitted nulls, one source table, compact claim records, and explicit verification/contradiction metadata. Keep the full ledger for storage. Measure the serialized prompt before each expensive stage and reserve room for the output schema, output tokens, and expected retries.

   Next expose document chunks with stable IDs, paging, and search so agents can retrieve the relevant section rather than repeatedly fetching the first 12,000 characters. Paging by character offset, with a per-job document memo, has since landed as fetch version 2; stable chunk IDs and in-document search have not. Select salvage evidence by relevance and provenance instead of only arrival order. Make tool-history compaction an explicit, measured acquisition/prompt-version change.

   For campaign synthesis, prefer a bounded index of claims/findings plus local retrieval of the exact supporting records. If the complete indexed view still cannot fit, use staged campaign synthesis with explicit claim mappings and preserved contradictions. It can reuse the existing campaign synthesizer; additional agent roles are not required.

   **Acceptance:** an eleven-question representative fixture fits the planned synthesis path with output/retry headroom; material claims and contradictory evidence remain retrievable; a citation resolves to the same source after compaction; record prompt size, token usage, truncation, retrieval coverage, and cost before and after. Compare quality, not just smaller payloads.

9. **P2 — Experiment identity does not capture everything that changes results.**

   [Experiment manifests](../src/research_loop/experiment.py) record useful policy and environment information, but they hash the suite file rather than dataset bytes, omit several extraction/runtime dependency versions, and record only a dirty flag for uncommitted code. Two materially different dirty working trees can therefore share the same recorded commit and effective configuration fingerprint. The benchmark fingerprint also does not fully describe the effective ResearchConfig or prompt templates.

   **Solution:** add dataset and attachment digests, prompt/acquisition/runtime schema versions, effective run configuration, and a reproducible environment snapshot. For dirty trees, record an explicit uncommitted-source fingerprint that excludes secrets and runtime data, or require a commit for named comparison runs. Use paired cases, repeat runs where variance matters, and record uncertainty.

   **Acceptance:** changing dataset bytes or effective runtime behavior changes experiment identity; two different dirty trees never share a fingerprint.

10. **P2 — The runtime depends too heavily on legacy orchestration, and configuration has multiple sources of truth.**

    [ResearchLoop inherits AsyncResearchLoop](../src/research_loop/orchestrator.py), while [ResearchGraphDeps](../src/research_loop/graph.py) names that concrete legacy class. The graph calls its private methods and agent attributes. The 739-line base class owns model execution, budget accounting, repository transitions, tool construction, environment settings, prompt construction, salvage, selection rules, and the legacy algorithm.

    The documented Run lifecycle owner has no single corresponding object in this repository. Similar lifecycle logic appears in graph run, legacy run, and single-agent jobs. The campaign adds more run and artifact logic and imports private experiment helpers. These are the structural pressure points.

    Settings are passed into benchmark and campaign functions, but [AsyncResearchLoop.__init__](../src/research_loop/async_orchestrator.py) reloads environment settings independently. A caller-supplied cache location or acquisition credential configuration is consequently not the sole effective configuration. Policy factories also read environment variables while supporting a separate overrides mapping. Diagnostics check enabled providers, but execution itself is not bound to that same settings snapshot.

    **Solution:** resolve and validate configuration once at the application boundary. Construct a run-local runtime by composition. Keep the public facade and legacy interface as adapters over shared typed execution/operations. Separate prompt projection from model execution. Supply the graph with a small typed operations interface rather than the legacy loop object.

    Do this after capturing behavior with stronger contracts. Moving code into directories without clarifying ownership would preserve the same coupling.

    **Acceptance:** runtime internals do not reload environment variables; one explicit configuration snapshot explains every effective route and acquisition setting; graph nodes use public typed operations; graph and legacy share execution/lifecycle services while retaining separate topology implementations.

11. **P2 — Configuration and domain validation permit invalid or ambiguous work.**

    [ResearchConfig](../src/research_loop/async_orchestrator.py) and ModelRoute use ordinary dataclasses without bounds validation. ResearchConfig(max_parallel_scouts=0) is accepted, and the run then waits indefinitely on the scout semaphore; a synthetic probe was still waiting after five seconds, and in a real run the planner call has already been paid for. ResearchPlan accepts duplicate question IDs, and the graph later constructs a dictionary keyed by ID. Planner question-range guidance is a prompt, not an enforced maximum.

    Campaign configuration is an untyped dictionary with selective manual validation. A dry run does not exercise all fields later indexed by execution. Several fields in the domain are advisory or unused in control flow, including plan stop_conditions and gap-analysis resolved_question_ids. Those fields look more operational than they are.

    **Solution:** validate concurrency, rounds, finite positive budgets, token/request limits, planner ranges, complete role routes, and unique IDs before creating a job. Treat zero as valid only where it has a defined meaning, such as zero verification follow-up rounds. Use a typed campaign specification, and make dry-run build the same effective RunSpec as a real invocation.

    Make advisory fields explicit in documentation/schema descriptions, or remove them in an intentional schema revision. Enforce work-count bounds independently of model cooperation.

    **Acceptance:** invalid campaign/configuration inputs fail locally before persistence or provider calls; duplicate question IDs cannot enter the graph; dry-run catches missing execution fields; planner output cannot create unlimited queued work.

12. **P2 — Acquisition and attachment handling need a shared service boundary and stronger resource contracts.**

    [web.py](../src/research_loop/web.py) imports private cache, rate, and HTTP helpers from scholar.py. These helpers are shared infrastructure housed inside one domain adapter. Clients are repeatedly created within agent/request execution, and resource/concurrency behavior is spread across tool construction and provider code.

    The public fetcher already does several good things: HTTPS-only URLs, checks on redirects, public-IP checks, and a decoded-byte cap. However, [DNS validation](../src/research_loop/acquisition.py) and the HTTP connection resolve independently. That cannot guarantee the connected destination stays public. In metadata requests, [the response-size check](../src/research_loop/scholar.py) occurs after the response has been buffered. Scholarly year filters are applied to OpenAlex but not equivalently to arXiv/Crossref requests.

    [Attachment ingestion](../src/research_loop/attachments.py) runs synchronously from async run methods. Parsing large documents can block other work on the event loop. Search retokenizes all candidate chunks each time and uses an ASCII-oriented tokenizer. [Multimodal prompt construction](../src/research_loop/attachments.py) rereads the source path later, so changed file bytes can differ from the earlier recorded digest.

    **Solution:** extract shared HTTP/cache/rate services and inject one controlled service bundle into each run. Stream metadata responses with byte limits; scope shared concurrency explicitly; normalize filter semantics and response provenance. Enforce destination safety in the connection path or through a configured egress boundary, with redirect tests. Reuse connections while keeping run constraints separate.

    Offload blocking extraction from the event loop with bounded concurrency; preserve a verified byte snapshot or revalidate the digest before multimodal submission. Cache chunk token statistics and document the language coverage of local retrieval. Keep corpus/document quotas and truncation flags explicit. A vector database is not needed for these improvements.

    **Acceptance:** oversized responses stop while streaming; date-window fixtures behave consistently across providers; a changed attachment cannot be submitted under an old hash; parsing does not stall an independent async task; source restrictions remain active when connections are pooled.

13. **P2 — The tests provide useful confidence, but the most important contracts are under-tested.**

    The 76 passing tests exercise many useful details. However, the [parity fingerprint](../src/research_loop/parity.py) omits claim statements, evidence excerpts, source metadata, and contradictions. Changing a claim statement and excerpt in a copied outcome still produced no parity differences in a local probe.

    The parity test and SyntheticResearchLoop override _run_agent, bypassing much of the production executor. Budget unit tests cover some executor behavior, but the main synthetic path cannot prove that the complete lifecycle, cost accounting, and tool assembly work together. Repository tests exercise memory behavior and fake migration connections; they do not constitute a Postgres transaction/failure integration suite.

    The tracked repository contains no dependency lock or CI configuration. Installation is oriented toward editable checkout use. Migrations and the default campaign are found through repository-relative filesystem assumptions. Wheel installation outside the source checkout was not tested in this review; the packaging tools were not available locally.

    **Solution:** inject fake models/acquisition clients below the executor so deterministic end-to-end tests exercise real task and usage persistence. Extend parity to canonical full evidence with only genuinely nondeterministic fields excluded. Cover cancellation, failed branches, duplicate IDs, empty plans, invalid follow-ups, exhausted budgets, unresolved verification, and interrupted artifact publication.

    Add a small disposable-Postgres integration gate for migrations, canonical evidence, terminal states, transaction rollback, and idempotency. Add locked CI installation, targeted lint/type checking at service interfaces, and an install-from-wheel smoke test with packaged resources. Keep paid provider smoke tests separate and explicitly invoked.

    **Acceptance:** the confirmed probes in this report become regression tests; evidence changes are caught by parity; both repository implementations satisfy the same contract; a built artifact can run diagnostics and locate required resources outside the checkout.

14. **P3 — Campaign artifacts are not published atomically, and question IDs can name unsafe paths.**

    [run_campaign](../src/research_loop/campaign.py) overwrites the question folder file by file and writes run.json last. During a rerun, the previous completed marker remains in place. A simulated failure after report.json was overwritten left a new report associated with the old job ID; the aggregator accepted it as completed. This is P3 because the writes happen synchronously after the run has already succeeded, so mixing attempts needs a crash or I/O error within that short window.

    The question-ID check rejects slashes but accepts '..'. That identifier can escape the intended question directory. I confirmed it is accepted by load_campaign; no escaped writes were performed. The campaign specification is written by the operator, so this is a self-inflicted hazard; the quick fix closes it.

    **Solution:** treat each attempt as an immutable artifact bundle under campaign ID / question ID / run ID. Write to a staging directory, include content hashes and schema versions, and publish a small current-run pointer only after the bundle is complete. Validate every file digest before aggregation. Validate path components and reserve internal directory names.

    **Acceptance:** an interrupted rerun leaves the previous complete bundle intact; mismatched-content bundles are rejected; '.', '..', separators, and reserved folder names cannot become question directories.

15. **P3 — Budget accounting is separate from admission control.**

    Budget checks use completed spend. Concurrent calls can each see the same remaining allowance. The soft-cap behavior is documented, so this is not a hidden promise of a hard cap, but the reserved synthesis budget is not a guaranteed reservation under concurrency. Salvage can also consume the finishing reserve. This is P3 because the soft cap is documented and the current campaign runs two scouts in parallel.

    **Solution:** ModelPolicy should continue to define limits. A run-local budget account should atomically reserve admissible allowances before starting calls and reconcile actual usage afterward. This can protect the finishing reserve, at some cost to concurrency, so specify what a call reserves. Reserving each call's full route cost cap is simple and safe but pessimistic: parallel scouts each hold their whole cap, so admission refuses or serializes work that would have fit. Reserving an estimate and reconciling afterward admits more work but can still overshoot. Guarding only the finishing reserve may be enough at current concurrency. Provider-side limits remain the external spending backstop because reported usage arrives after requests execute.

    **Acceptance:** simultaneous calls cannot both allocate the same reserved allowance; the chosen reservation rule and its effect on scout concurrency are documented.

**The proposed structure is a moderate extraction of responsibilities.** The initial step should add only a few modules, preserving existing public imports. Further folder grouping can follow when ownership is stable.

~~~mermaid
flowchart TD
    CLI["CLI / benchmark / campaign application"]
    Facade["ResearchLoop facade"]
    Run["ResearchRun: identity, lifecycle, budget account, services"]
    Graph["research-graph-v1"]
    Ops["Typed research operations + prompt views"]
    Executor["AgentExecutor"]
    Agents["PydanticAI Agents"]
    Policy["ModelPolicy"]
    Ledger["EvidenceLedger: serial canonical commits"]
    Acquisition["Acquisition services + source policy"]
    Repository["ResearchRepository / Postgres"]
    CLI --> Facade
    Facade --> Run
    Run --> Graph
    Graph --> Ops
    Ops --> Executor
    Policy --> Executor
    Policy --> Run
    Executor --> Agents
    Executor --> Acquisition
    Ops --> Ledger
    Ledger --> Repository
    Run --> Repository
    Executor --> Repository
~~~

The arrow from the ledger to persistence represents the serial commit operation coordinating both; the ledger need not own a database connection. PydanticAI retains model/tool semantics, the graph retains topology, ModelPolicy retains selection and limits, and Postgres remains the durable history.

| Boundary | Concrete responsibility | Suggested initial location |
|---|---|---|
| ResearchLoop | Compatible public entry point; constructs a run | Existing orchestrator.py |
| ResearchRun | One execution's identity, terminal state, task/service lifetime, policy-driven budget accounting | New run.py |
| AgentExecutor | Executes an agent; captures usage/messages; emits a typed result envelope and safe telemetry | New execution.py |
| ResearchOperations | Scout/deep-dive/synthesis/verification operations consumed by both graph and legacy control flow | New operations.py |
| Prompt views | Deterministic evidence projections and input-size planning | New prompts.py |
| EvidenceLedger | Validated canonical evidence and stable IDs | Existing ledger.py, extended deliberately |
| Acquisition services | HTTP clients, cache, rate limits, policy decisions, document observations | Extract common helpers before regrouping web/scholar files |
| Experiment results | Safe durable evaluation records and aggregates | Existing experiment/evals modules, with explicit public helpers |
| Campaign artifacts | Typed config, immutable attempt bundles, verification-aware aggregation | Extract from campaign.py once integrity rules are tested |
| CLI | Parsing, safe rendering, exit codes | Thin adapters around application functions |

Keep graph state small. Run-local ledger and service handles remain dependencies. Parallel workers return typed values, and serial join/record steps canonicalize and persist results. Preserve LegacyResearchLoop until expanded parity and compatibility coverage justify retiring it.

The graph version describes topology. Also record evidence-schema, prompt, acquisition, runtime, and evaluator versions so a behavior-changing cleanup is visible even when the graph edges remain unchanged. Separate behavior-preserving extraction commits from semantic changes and benchmark them under distinct configurations.

**Implementation roadmap.** The estimates below are rough focused engineering effort for someone familiar with this code. They are not delivery commitments and exclude waiting for provider access or completing paid evaluation campaigns.

| Sequence | Work package | Depends on | Estimated effort | Exit gate |
|---|---|---|---|---|
| 0 | Quick fixes listed above, each with its probe as a regression test | Current baseline | 1–2 days | The corresponding probes pass as regression tests |
| A | Per-case terminal states, safe failure projection, durable case scores | 0 | 2–4 days | Failed/partial suites, private-body sentinel, and score reload tests pass |
| B | Canonical evidence persistence, structural reference validation, quality disposition | A's result semantics | 4–6 days | Every persisted final reference resolves; invalid refs retry/fail predictably |
| C | Typed campaign spec, verification-aware synthesis view, immutable artifact bundles | 0; can start alongside B | 2–4 days | Interrupted rerun and stale-spec probes are rejected or isolated correctly |
| D | Run lifecycle, cancellation, run deadline, validated RunSpec; budget reservation (P3) can follow | B/C contracts defined | 3–5 days | Terminal states are reliable; concurrent allocations respect the reserve once reservation lands |
| E | Extract shared executor/operations and inject resolved settings | D | 3–5 days | Graph/legacy topology parity passes through the real executor with fake models |
| F | Compact evidence views, prompt budgets, paged document access, bounded campaign synthesis | B and E | 4–7 days | Representative eleven-question fixture fits; citation/contradiction coverage preserved |
| G | Acquisition policy enforcement, streaming limits, transport safety, attachment snapshot integrity | D/E; can precede F's paging | 3–5 days | Redirect/source-policy, oversize, filter, and changed-file fixtures pass |
| H | Reproducible experiments, Postgres integration gate, locked CI and packaged resources | A/B/E, then maintain continuously | 3–5 days | Results reload correctly, dataset changes alter identity, install smoke passes |

This is roughly **five to eight focused engineer-weeks** if performed mostly sequentially, with a useful trustworthy local workflow delivered incrementally. The A–H estimates predate the quick-fix split; A and C shrink once those fixes land. After the quick fixes, the highest-value work is B's reference validation and quality disposition, then A's durable case records. The full reorganization can follow those correctness contracts.

A practical first week should deliver the quick fixes with their regression tests, then explicit per-case terminal states and persisted evaluation results. It should also preserve the current pilot inputs as a comparison fixture containing only authorized local data; protected material must remain outside tracked fixtures.

The next milestones are:

1. **A dependable single question.** Run with injected settings; validate the plan and references; persist the canonical ledger; cancel safely; distinguish execution completion from verification quality. Demonstrate reloading a report and resolving its citations from Postgres.
2. **A dependable campaign.** Publish immutable attempt bundles, validate identity/digests, preserve verification findings, and show that interruption cannot mix attempts. Make the eleven-question synthesis path succeed with explicit input/output/retry budgets.
3. **A dependable comparison.** Pin datasets and environment, persist scores and applicability, report failures and unknown cost, and compare policies on paired cases with fixed acquisition and evaluator conditions.
4. **Measured optimization.** Compare the original pilot and the new evidence view using prompt size, cumulative tokens, latency by stage, tool failures, retrieval coverage, verification coverage, and end quality. Increase concurrency or research depth only after these measurements show a benefit.

**What I would defer.** New workflow frameworks, more agent roles, a learned router, vector infrastructure, and wholesale graph replacement do not have supporting evidence from this review. The current graph already expresses the required control flow. Likewise, splitting every large module immediately would consume effort before the integrity boundaries are settled. Keep the fast in-memory path, the explicit normalized/multimodal distinction, the legacy parity baseline, and the thin Postgres adapter.

**Evidence from local probes.**

| Probe | Observed current behavior |
|---|---|
| Force the only benchmark task to fail | Overall manifest completed; individual run failed |
| Raise a fake provider exception containing a dummy private-body marker | Marker appears in captured console output |
| Cancel a single-agent job during execution | Job and task remain running; spend state is cleared |
| Return a final report with an unknown claim reference | Graph returns successfully with the unknown reference |
| Return a verifier follow-up for a question ID absent from the plan, with max_verification_rounds=2 | Graph and legacy each make three synthesizer and three verifier calls and no deep dives |
| Return a plan with zero questions | Graph and legacy finish as succeeded after synthesis and verification on an empty ledger |
| Compare stored task claim IDs with final report references | Canonical report reference absent from raw persisted task IDs |
| Aggregate a fixture with a mismatched campaign-spec hash | Fixture is accepted |
| Add a major verifier-only warning to a completed question | Report assertion enters campaign prompt; warning does not |
| Interrupt a question rerun after report.json is overwritten | Aggregator accepts new report with old job ID |
| Validate campaign question ID '..' | Accepted |
| Change claim statement and evidence excerpt in a copied outcome | Parity reports no difference |
| Combine one $1 task with one unpriced task | Reported aggregate is $1 with no completeness flag |
| Score major-error freedom with no checked claims | Score is 1.0 |
| Put a blocked URL only in a search result | Recorded as blocked access |
| Run the synthetic loop with zero scout concurrency | Still waiting after five seconds; nothing ends the run |
| Construct a plan with duplicate question IDs | Accepted |
| Measure the existing pilot's campaign synthesis prompt | 82,719 characters against a 360,000-character campaign cap |

These probes establish specific code-path behaviors, not a measured rate of real-model mistakes. The performance measurements cover the existing one-question pilot. The roadmap's larger-campaign capacity estimates are projections. Network safety, production database recovery, wheel installation, and real model quality require the corresponding follow-up gates described above.
