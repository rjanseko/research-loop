# Research Loop

Research Loop is a framework for agentic research. You give it a question. A team of AI agents plans the work, researches the parts in parallel with web, scholarly, and file tools, and writes a report. Every claim in the report cites the evidence it rests on, and a separate agent checks the report before it comes back to you.

It is built on [PydanticAI](https://ai.pydantic.dev) for the agents and Pydantic Graph for the workflow.

## Run it

```bash
make setup && source .venv/bin/activate
pytest -q                                          # offline tests
python examples/run_research.py "Your question"    # scripted models: free, no network
python examples/run_research.py "Your question" --policy quality --paid   # real models
python examples/run_research.py "Your question" --policy quality --paid --persist   # and store the job in Postgres
```

Before your first paid run, set up provider keys and network access with [docs/setup.md](docs/setup.md#preparing-for-paid-runs) and check them with `research-diagnose --smoke`.

From Python:

```python
from research_loop import ResearchLoop, get_policy

loop = ResearchLoop(get_policy("quality"))
outcome = await loop.run("How do long-horizon coding agents recover from errors?")

outcome.report          # the answer, with each claim citing ledger claim IDs
outcome.verification    # the verifier's check of each claim
outcome.ledger          # all the evidence gathered
outcome.sources         # what the report's inline [sN] citations name
outcome.review_reasons  # anything left unresolved; empty means no check flagged a problem
```

You steer a run with `ResearchConstraints`: files for the agents to read, sources they must not use, and notes they should follow.

```python
from research_loop import ResearchConstraints

outcome = await loop.run(
    "Check the claims in this report against current primary sources.",
    constraints=ResearchConstraints(
        attachment_paths=["report.pdf", "figures.xlsx"],
        blocked_urls=["https://example.com/answer-key"],
        notes=["Prefer peer-reviewed sources published since 2024."],
    ),
)
```

## How a run works

<p align="center">
  <img src="docs/assets/research-graph.svg" width="100%" alt="Animated walk through one run: plan, parallel scouts, join, gap analysis, parallel deep dives, synthesize, verify, one verification round, done">
</p>

Six agents take turns, each with one job:

| Agent | Job | Returns |
|---|---|---|
| Planner | Split the objective into research questions | `ResearchPlan` |
| Scout | Research one question with tools; all questions run in parallel | `ResearchResult` |
| Gap analyst | Find weak spots: low confidence, missing primary sources, contradictions | `GapAnalysis` |
| Deep dive | Research one weak spot more thoroughly, also in parallel | `ResearchResult` |
| Synthesizer | Write the report from the gathered evidence | `FinalReport` |
| Verifier | Check each report claim against its evidence; may ask for more research | `VerificationReport` |

If the verifier asks for more research and rounds remain, the follow-ups become deep dives, and the synthesizer and verifier run again.

### The graph

This is `research-graph-v1` as `graph.py` builds it, with each box named after its step. Double-edged boxes run once per question or gap, in parallel; every other step runs once. Diamonds are decisions, and their edge labels are the branch names in the code.

```mermaid
flowchart TB
    start(["Start"]) --> plan["Plan research<br/><i>planner</i>"]
    plan -->|"one work item per question"| scout[["Scout question<br/><i>scout</i>"]]
    scout -->|"join"| record_scouts["Record scout evidence<br/><i>in plan order</i>"]
    record_scouts --> analyze_gaps["Analyze evidence gaps<br/><i>gap_analyst</i>"]
    analyze_gaps --> initial_gap_decision{"Resolve material gaps<br/>before synthesis"}
    initial_gap_decision -->|"material gaps"| prepare_initial["Prepare initial deep dives"]
    prepare_initial -->|"one work item per gap"| initial_deep_dive[["Initial deep dive<br/><i>deep_dive</i>"]]
    initial_deep_dive -->|"join"| record_initial["Record initial deep-dive evidence<br/><i>in plan order</i>"]
    record_initial --> synthesize
    initial_gap_decision -->|"evidence sufficient"| synthesize["Synthesize report<br/><i>synthesizer</i>"]
    synthesize --> verify["Verify report<br/><i>verifier</i>"]
    verify --> route["Route verification result"]
    route --> verification_decision{"Finish or research<br/>verifier follow-ups"}
    verification_decision -->|"complete"| finalize["Finalize research"]
    finalize --> finish(["End"])
    verification_decision -->|"research follow-ups"| prepare_verification["Prepare verification deep dives"]
    prepare_verification -->|"one work item per follow-up"| verification_deep_dive[["Verification deep dive<br/><i>deep_dive</i>"]]
    verification_deep_dive -->|"join"| record_verification["Record verification evidence<br/><i>in plan order</i>"]
    record_verification --> synthesize

    classDef planner stroke:#6366f1,stroke-width:2px
    classDef scout stroke:#14b8a6,stroke-width:2px
    classDef join stroke:#64748b,stroke-width:2px
    classDef gap stroke:#8b5cf6,stroke-width:2px
    classDef deep stroke:#0ea5e9,stroke-width:2px
    classDef synth stroke:#ec4899,stroke-width:2px
    classDef verify stroke:#10b981,stroke-width:2px
    classDef done stroke:#eab308,stroke-width:2px
    class plan planner
    class scout scout
    class record_scouts,record_initial,record_verification,prepare_initial,prepare_verification,route join
    class analyze_gaps gap
    class initial_deep_dive,verification_deep_dive deep
    class synthesize synth
    class verify verify
    class start,finalize,finish done
```

`research-graph` prints the executable graph, and [docs/graph.md](docs/graph.md) explains every step.

## Example: one question through the graph

The question below shows the loop at full stretch. It needs scholarly search. Its sources mix preprints, published papers, and vendor posts. And its evidence disagrees, so it takes every branch of the graph. The outputs are illustrative and shortened, but every ID, route, and decision shown is what the code does with them. To run this question for real, use `python examples/readme_example.py --paid`: it runs the `quality` policy under a $4 total cap with fewer questions, deep dives, and verification rounds, and saves the report, verification, and the exact configuration as JSON.

```python
outcome = await ResearchLoop(get_policy("quality")).run(
    "Is SWE-bench Verified still a trustworthy measure of coding-agent progress?",
    constraints=ResearchConstraints(notes=[
        "Keep preprints and published papers distinct.",
        "Label scores a vendor reports about its own model as vendor claims.",
    ]),
)
```

```mermaid
flowchart TB
    start(["Is SWE-bench Verified still a trustworthy<br/>measure of coding-agent progress?"])
    start --> plan["Plan research<br/><i>four questions</i>"]
    plan --> s1[["Scout question<br/>q1: how Verified was built"]]
    plan --> s2[["Scout question<br/>q2: leakage and weak tests"]]
    plan --> s3[["Scout question<br/>q3: vendor vs. independent scores"]]
    plan --> s4[["Scout question<br/>q4: newer benchmarks, on cheap_scout"]]
    s1 -->|"confidence 0.90"| record_scouts
    s2 -->|"0.55, sources disagree"| record_scouts
    s3 -->|"0.75, vendor posts only"| record_scouts
    s4 -->|"0.80, finished first"| record_scouts
    record_scouts["Record scout evidence<br/>q1/c1 … q4/c2, in plan order"] --> analyze_gaps["Analyze evidence gaps"]
    analyze_gaps -->|"material gaps"| prepare_initial["Prepare initial deep dives<br/>q2 contradiction, severity 5<br/>q3 missing primary source, severity 4"]
    prepare_initial --> d2[["Initial deep dive<br/>q2"]]
    prepare_initial --> d3[["Initial deep dive<br/>q3"]]
    d2 --> record_initial["Record initial deep-dive evidence<br/>q2/c1~2, q2/c2~2, q3/c1~2"]
    d3 --> record_initial
    record_initial --> synth1["Synthesize report<br/>cites q1/c1, q2/c1~2, q3/c2, …"]
    synth1 --> verify1["Verify report<br/>q3/c2 unsupported, major"]
    verify1 --> route1["Route verification result<br/>round 1 of 2"]
    route1 -->|"research follow-ups"| prepare_verification["Prepare verification deep dives<br/>q3 missing evidence, severity 4"]
    prepare_verification --> d3b[["Verification deep dive<br/>q3, attempt 1, on alternate_deep_dive"]]
    d3b --> record_verification["Record verification evidence<br/>q3/c1~3"]
    record_verification --> synth2["Synthesize report<br/>cites q3/c1~3 instead of q3/c2"]
    synth2 --> verify2["Verify report<br/>every check supported"]
    verify2 --> route2["Route verification result<br/>no research requested"]
    route2 -->|"complete"| finalize["Finalize research"]
    finalize --> finish(["Report, verification, ledger<br/>review_reasons = []"])

    classDef planner stroke:#6366f1,stroke-width:2px
    classDef scout stroke:#14b8a6,stroke-width:2px
    classDef join stroke:#64748b,stroke-width:2px
    classDef gap stroke:#8b5cf6,stroke-width:2px
    classDef deep stroke:#0ea5e9,stroke-width:2px
    classDef synth stroke:#ec4899,stroke-width:2px
    classDef verify stroke:#10b981,stroke-width:2px
    classDef done stroke:#eab308,stroke-width:2px
    class plan planner
    class s1,s2,s3,s4 scout
    class record_scouts,record_initial,record_verification,prepare_initial,prepare_verification,route1,route2 join
    class analyze_gaps gap
    class d2,d3,d3b deep
    class synth1,synth2 synth
    class verify1,verify2 verify
    class start,finalize,finish done
```

| Step | Agent | What happens to this question |
|---|---|---|
| Plan research | `planner` | Splits the objective into four questions; the `quality` policy aims for six to ten, shortened here. q1 and q2 require primary sources. q4 is marked `expected_difficulty: "low"` and doesn't require them. |
| Scout question (×4, in parallel) | `scout`; q4 on `cheap_scout` | All four start together (there are eight slots). q4 goes to the cheaper model because it is easy and needs no primary sources, and it finishes first. q2 finds two analyses that disagree, records the contradiction, and reports confidence 0.55. q3 finds only vendor launch posts, and one quote it gives appears in nothing its tools returned, so code marks it `quote_check: not_found`. |
| Record scout evidence | Code | Adds the four results in plan order, whatever order they finished in, and names their claims `q1/c1` to `q4/c2`. |
| Analyze evidence gaps | `gap_analyst`, then code | The analyst names two gaps: the contradiction on q2 (severity 5) and missing primary sources on q3 (severity 4). Code adds a `low_confidence` gap for q2, because 0.55 is below `min_scout_confidence` (0.70). It keeps one gap per question, the most severe or, on a tie, the first. Both gaps fit under `max_deep_dives_per_round` (4), so both are material. |
| Prepare initial deep dives, Initial deep dive (×2, in parallel), Record initial deep-dive evidence | `deep_dive` | Each deep dive gets its question and its gap. Both workers number their claims from `c1` again, so the ledger renames them `q2/c1~2`, `q2/c2~2`, and `q3/c1~2`, and nothing is overwritten. Each task records the gap-analysis task as its parent. |
| Synthesize report | `synthesizer` | Writes the report from the ledger. Every statement cites claim IDs, and a citation to an ID that isn't in the ledger gets one retry. |
| Verify report | `verifier` | Sees the report and the claims it cites. The statement about vendor scores rests only on `q3/c2`, whose quote was not found, so the verifier rates it unsupported and major and asks for more research on q3. |
| Route verification result | Code | The follow-up names a planned question and round 1 of `max_verification_rounds` (2) is still open, so the run goes back to research. |
| Prepare verification deep dives, Verification deep dive, Record verification evidence | `deep_dive` on `alternate_deep_dive` | A different model looks for an independent run of the same models. Its claim becomes `q3/c1~3`, and the verifier task is its parent. |
| Synthesize report, Verify report, Route verification result, Finalize research | `synthesizer`, `verifier` | The new report cites `q3/c1~3` instead of `q3/c2`. Every check is supported and nothing more is requested, so the run finishes with an empty `review_reasons`. |

<details>
<summary>The plan (<code>ResearchPlan</code>, defaults left out)</summary>

```json
{
  "objective": "Is SWE-bench Verified still a trustworthy measure of coding-agent progress?",
  "questions": [
    {"id": "q1", "question": "How was SWE-bench Verified built from SWE-bench, and what does it measure?",
     "priority": 5, "requires_primary_sources": true},
    {"id": "q2", "question": "What independent evidence shows solution leakage, weak tests, or training-data contamination in SWE-bench tasks?",
     "priority": 5, "requires_primary_sources": true},
    {"id": "q3", "question": "How do scores vendors report for their own models compare with independent runs of the same models?",
     "priority": 4},
    {"id": "q4", "question": "Which newer benchmarks were proposed to address these problems?",
     "priority": 2, "expected_difficulty": "low"}
  ]
}
```

</details>

<details>
<summary>Two claims in the ledger (<code>Claim</code>): one clean, one whose quote was not found</summary>

The first cites the SWE-bench paper as the preprint it is. The second cites a vendor post the scout did fetch, so its source is `observed`, but the quoted words appear in nothing the tools returned, so its quote is `not_found`. Code sets both marks; the model can't.

```json
{
  "id": "q1/c2",
  "statement": "SWE-bench builds its tasks from real GitHub issues and the pull requests that resolved them.",
  "confidence": 0.9,
  "evidence": [{
    "source": {"url": "https://arxiv.org/abs/2310.06770", "arxiv_id": "2310.06770",
               "title": "SWE-bench: Can Language Models Resolve Real-World GitHub Issues?",
               "source_type": "paper", "publication_status": "preprint"},
    "excerpt": "Each task pairs an issue with the repository at that commit; the tests from the resolving pull request judge a fix.",
    "confidence": 0.9,
    "source_check": "observed"
  }]
}
```

```json
{
  "id": "q3/c2",
  "statement": "Vendor-reported scores run ahead of independent runs of the same models.",
  "confidence": 0.7,
  "evidence": [{
    "source": {"url": "https://vendor.example/blog/model-launch", "title": "Model launch post",
               "source_type": "official", "publication_status": "vendor_technical_report"},
    "excerpt": "The vendor reports a higher score than public leaderboards show.",
    "quote": "resolves more issues than any model we have tested",
    "confidence": 0.7,
    "quote_check": "not_found",
    "source_check": "observed"
  }]
}
```

</details>

<details>
<summary>Gap selection before the first deep dives</summary>

| Question | Reason | Severity | Raised by | Deep dive? |
|---|---|---|---|---|
| q2 | `contradiction` | 5 | Gap analyst | Yes: kept first on the tie |
| q2 | `low_confidence` | 5 | Code: 0.55 < 0.70 | No: one gap per question |
| q3 | `missing_primary_source` | 4 | Gap analyst | Yes |

</details>

<details>
<summary>The first verification (<code>VerificationReport</code>, passing checks left out)</summary>

```json
{
  "needs_research": true,
  "checks": [
    {"statement": "Vendor-reported scores run ahead of independent runs of the same models.",
     "claim_ids": ["q3/c2"], "supported": false, "severity": "major",
     "explanation": "The only evidence is a quote no research tool returned."}
  ],
  "followups": [
    {"question_id": "q3", "reason": "missing_evidence", "severity": 4,
     "followup": "Find an independent run of the same models under matched conditions."}
  ]
}
```

</details>

Why this question makes a good showcase:

- **It splits cleanly.** Four independent questions run in parallel, and the easy one goes to a cheaper model.
- **Its sources are uneven.** Papers, preprints, and vendor posts sit side by side, so publication status and the quote and source checks matter.
- **Its evidence disagrees.** The contradiction and the low confidence both become deep dives, most severe first.
- **Its weakest claim gets caught.** A quote no tool returned sinks a report statement, and a verification round with a different model replaces it.

## The data

Everything an agent returns is a typed Pydantic model (`schemas.py`). These are the ones that matter:

```mermaid
classDiagram
    direction LR
    class ResearchPlan {
        objective
        questions: list~ResearchQuestion~
    }
    class ResearchQuestion {
        id: "q1"
        question
        priority
        requires_primary_sources
    }
    class ResearchResult {
        question_id
        conclusion
        claims: list~Claim~
        contradictions
        confidence
    }
    class Claim {
        id: "q1/c1"
        statement
        evidence: list~Evidence~
        confidence
    }
    class Evidence {
        source: SourceRef
        excerpt
        quote
        quote_check
        source_check
    }
    class SourceRef {
        title
        url, doi, arxiv_id
        locator
        publication_status
    }
    class FinalReport {
        answer
        claims: list~ReportClaim~
        caveats
    }
    class ReportClaim {
        statement
        claim_ids
    }
    class VerificationReport {
        checks: list~ClaimCheck~
        needs_research
        followups: list~Gap~
    }
    class ClaimCheck {
        claim_ids
        supported
        severity
    }
    ResearchPlan *-- ResearchQuestion
    ResearchQuestion <.. ResearchResult : answers
    ResearchResult *-- Claim
    Claim *-- Evidence
    Evidence *-- SourceRef
    FinalReport *-- ReportClaim
    ReportClaim ..> Claim : cites by ID
    VerificationReport *-- ClaimCheck
    ClaimCheck ..> Claim : checks
```

<p align="center">
  <img src="docs/assets/evidence-check.svg" width="100%" alt="Animated evidence check: a scout records two quotes from a tool result, code marks one verified and one not found, the evidence enters the ledger as claim q1/c1, a report claim cites q1/c1, and the verifier checks it against that evidence">
</p>

The `EvidenceLedger` (`ledger.py`) collects every `ResearchResult` from a run, in plan order, and gives each claim a unique ID such as `q3/c2`. A report can cite only claims that exist in the ledger. That is how you get from any sentence in the report back to the passage and source behind it.

## Ideas you need to know

**The graph decides what happens next; the harness does the work.** `graph.py` holds only the order of steps. `AsyncResearchLoop` in `async_orchestrator.py` runs each agent call: it picks the model, enforces budgets, records the call, and checks the output. Prompts and role calls live in the harness and in `agents.py`, never in graph steps.

**Parallel agents never write shared state.** Each scout or deep dive returns its result as a value. One step then adds all the results to the ledger, sorted into plan order, so the evidence comes out the same no matter which agent finished first.

<p align="center">
  <img src="docs/assets/fan-out-join.svg" width="100%" alt="Animated fan-out and join: five questions share three slots, finish out of order, and are recorded in plan order">
</p>

**Outputs are checked against the run, not just parsed.** Validators reject a plan with duplicate question IDs, a result filed under the wrong question, or a report citing a claim that doesn't exist. A failing output gets one retry with the problem explained, then the run fails. Separately, plain code marks each quote `verified` if it appears in what the tools actually returned, and each source `observed` if its URL or DOI appeared there. The model can't set these marks.

**A finished run isn't a correct run.** Check `review_reasons`. It lists missing evidence, unchecked claims, unsupported claims, research the verifier still wanted, and web and scholarly tools that mostly could not reach their sources.

**Models and budgets are configuration.** A `ModelPolicy` (`policy.py`) maps each role to a model and per-call limits on requests, tool calls, tokens, and dollars. Presets are `quality`, `breadth`, `glm-heavy`, and `synthetic`. A `ResearchConfig` sets how much work the loop does: parallelism, deep dives per round, verification rounds, and deadline. The only built-in spending limit is per call; set `job_cost_limit` if you want a cap per run.

**The model can change per question.** A question that needs images goes to the policy's multimodal scout. An easy question that doesn't need primary sources goes to a cheaper scout. Deep dives in a verification round switch to an alternate model. Each of these routes is optional; without it, the regular scout or deep-dive model is used.

**Running out of budget can end gracefully.** By default, a scout or deep dive that hits its limits fails the run. With `salvage_exhausted_research=True`, it instead gets one extra call without tools to write up what it already found, and its quotes are still checked against the tool output it gathered.

**Providers are checked before anything runs.** Each provider needs its own API key in the environment or `.env`. `RESEARCH_ENABLED_PROVIDERS` limits which ones are used, and a `RESEARCH_*_MODEL` override must be written as `provider:model`, or startup fails before any money is spent. [docs/setup.md](docs/setup.md) lists the variables.

**Tools are bounded and sandboxed.** Fetches only reach public HTTPS addresses, download at most 5 MB, and return text in pages of 12,000 characters. Sources in `blocked_urls` are refused at the fetch, at every redirect, and again if evidence cites one. In `normalized` tool mode every model uses the same search tools, so comparing models doesn't also compare search engines.

**Your files are sources too.** Attachments can be PDF, Word, CSV, Excel, HTML, text, JSON, or images (`attachments.py`). Each one is extracted, hashed, and split into chunks before any model sees it, and agents read it through attachment tools by ID, never by your file path. Citations to attachments are checked like any other. In the default `normalized` attachment mode models get extracted text only; `multimodal` also sends the original images and scanned PDFs to the model.

**Results are reproducible on purpose.** The graph is versioned (`research-graph-v1`) and never changes silently. Benchmarks write a manifest with the commit, the policy, the configuration, and a fingerprint of every prompt. The fetch cache can record a run and replay it offline.

**Storage is optional.** Without a database, runs live in memory. With Postgres, every job, agent call, and tool call is kept with its cost and lineage (`research-db migrate` sets up the tables).

## Where things are

The code is layered: each layer uses only the layers below it.

| Layer | Modules in `src/research_loop/` |
|---|---|
| Commands | `main()` in `benchmark.py`, `long_horizon.py`, `diagnose.py`, `db.py`; `graph_cli.py` |
| Workflows built on the loop | `benchmark.py`, `benchmarks/`, `evals.py`, `experiment.py`, `long_horizon.py`, `citations.py`, `diagnose.py` |
| The research loop | `orchestrator.py` (the public `ResearchLoop`), `graph.py`, `async_orchestrator.py`, `agents.py` |
| Core types and rules | `schemas.py`, `ledger.py`, `quotes.py`, `policy.py` |
| Outside world | `tools.py`, `web.py`, `scholar.py`, `acquisition.py`, `attachments.py`, `repository.py`, `db.py`, `telemetry.py`, `observability.py`, `settings.py` |

| Command | What it does |
|---|---|
| `research-diagnose` | Checks your setup; `--smoke` makes one small paid call per model |
| `research-bench SUITE` | Runs a benchmark suite (BrowseComp, DeepResearch Bench, GAIA, and others) |
| `research-long-horizon` | Runs a multi-question study and synthesizes the results |
| `research-db` | Applies migrations and closes out runs a crashed process left open |
| `research-graph` | Prints the workflow graph as Mermaid |

## Read more

- [docs/guide.md](docs/guide.md): the full tour, covering every setting, check, and stored field
- [docs/setup.md](docs/setup.md): keys, models, Postgres, tracing
- [docs/graph.md](docs/graph.md): the workflow graph and its parity with the legacy loop
- [docs/acquisition.md](docs/acquisition.md) and [docs/attachments.md](docs/attachments.md): tools, caching, and file handling
- [docs/benchmarks.md](docs/benchmarks.md): benchmark lanes and metrics
- [long_horizon/agentic_se/README.md](long_horizon/agentic_se/README.md): the first long-horizon study
- [AGENTS.md](AGENTS.md): rules for changing the code
- [CONTRIBUTING.md](CONTRIBUTING.md): how to send a pull request

## License

MIT. See [LICENSE](LICENSE). Contributions are welcome; see [CONTRIBUTING.md](CONTRIBUTING.md).
