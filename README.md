# Research Loop

Research Loop is a framework for agentic research. You give it a question. A team of AI agents plans the work, researches the parts in parallel with web, scholarly, and file tools, and writes a report. Every claim in the report cites the evidence it rests on, and a separate agent checks the report before it comes back to you.

It is built on [PydanticAI](https://ai.pydantic.dev) for the agents and Pydantic Graph for the workflow.

## Run it

```bash
make setup && source .venv/bin/activate
pytest -q                                          # offline tests
python examples/run_research.py "Your question"    # scripted models: free, no network
python examples/run_research.py "Your question" --policy quality --paid   # real models
```

Before your first paid run, set up provider keys with [docs/setup.md](docs/setup.md) and check them with `research-diagnose --smoke`.

From Python:

```python
from research_loop import ResearchLoop, get_policy

loop = ResearchLoop(get_policy("quality"))
outcome = await loop.run("How do long-horizon coding agents recover from errors?")

outcome.report          # the answer, with each claim citing ledger claim IDs
outcome.verification    # the verifier's check of each claim
outcome.ledger          # all the evidence gathered
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

**A finished run isn't a correct run.** Check `review_reasons`. It lists missing evidence, unchecked claims, unsupported claims, and research the verifier still wanted.

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

## License

MIT. See [LICENSE](LICENSE).
