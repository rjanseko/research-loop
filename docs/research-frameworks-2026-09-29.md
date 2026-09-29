# Research frameworks: how to improve Scout's reports and measure progress

**29 September 2026.** Continuation of the [architectural review](architectural-review-2026-09-29.md), based on Scout checkout `claude/citations-synthesis` at `626be7f` and primary papers, code, and project documentation. This is a design assessment, not an experiment on Scout. No model or paid-service calls were made. Published scores below describe each project's own tasks, corpus, models, and evaluation; they are not forecasts for Scout.

## Executive assessment

Scout already has the right broad shape for a bounded research task: a plan, parallel scouts, a checked evidence ledger, optional gap research, and one final synthesizer. The most promising gains come from making *the evidence funnel* observable and improving the relationship between the final prose and the precise passages cited. Replacing PydanticAI, introducing another permanent agent role, or copying a scientific-paper datastore would not address Scout's current provenance and evaluation weaknesses.

Four outside ideas deserve focused tests:

1. **PaperQA2:** measure where decisive sources and facts disappear between search, reading, evidence selection, and attribution; select passages within a token budget only after preserving source identity.
2. **STORM:** for broad topics, plan from distinct perspectives and build a source-backed outline before drafting. Do this inside the existing plan and synthesis boundary.
3. **ALCE and FActScore:** judge each factual unit against *its displayed citation*, not the set of evidence attached to a broad claim. Score missing claims and unnecessary citations separately.
4. **DeepResearchGym and BEIR:** use stable retrieval inputs and explicit relevance labels to debug search/ranking. Neither measures the quality of a completed report by itself.

The order matters. A passage selector trained on today's loosely identified evidence could rank the wrong text very efficiently. A better report judge applied to only the answer body would still miss the executive summary. The [current architectural review](architectural-review-2026-09-29.md#synthesis-and-support-e1-and-s1) documents these local failures; the framework work should build on those repairs.

## What the projects actually contribute

| Project | Quality mechanism and published evidence | What Scout can borrow | Transfer limit |
| --- | --- | --- | --- |
| [PaperQA2](https://arxiv.org/abs/2409.13740), [implementation](https://github.com/Future-House/paper-qa), [engineering account](https://www.futurehouse.org/research/engineering-blog-journey-to-superhuman-performance-on-scientific-tasks) | Agentic paper search, candidate-chunk ranking, query-specific evidence summaries, citation traversal, and answer generation. FutureHouse reports source-paper recall at successive funnel stages on LitQA2, as well as multiple-choice precision and accuracy. | Stage-level source/fact recall; bounded candidate passage selection; scholarly citation traversal as a later, separate ablation. | LitQA2 is scientific multiple-choice QA, not a general cited report. Its correct-paper labels do not establish paragraph-level support or reader usefulness. Traversal can amplify citation-network bias. |
| [STORM](https://aclanthology.org/2024.naacl-long.347/), [implementation](https://github.com/stanford-oval/storm) | Perspective-guided questions and a curated outline before drafting. On FreshWiki, editors judged its articles organized 25 percentage points more often, with a 10% increase in broad coverage than its outline-driven retrieval baseline. | Perspective coverage in `ResearchPlan`; an evidence-backed outline for broad questions. | Wikipedia-style topic articles differ from decision-oriented reports. The authors also identify source bias transfer and unrelated facts being over-associated. Extra perspectives can create irrelevant breadth. |
| [OpenScholar](https://www.nature.com/articles/s41586-025-10072-4), [implementation](https://github.com/AkariAsai/OpenScholar) | Scientific passage retrieval/reranking, feedback-guided revision, and citation verification. ScholarQABench combines citation checks, expert rubrics, and human judgments; its 2,967 expert queries include 208 long-form answers. The paper's component ablations found losses when reranking, feedback, or verification was removed. | Evaluate reranking, coverage feedback, and citation verification as **separate** interventions; use multidimensional human checks. | Built around a 45-million-paper open-access datastore and scientific questions. Its reported model wins and expert preferences do not transfer to Scout's live web tasks. The authors acknowledge small long-form human sets and that usefulness judgments can underweight factual/citation defects. |
| [LangChain Open Deep Research](https://www.langchain.com/blog/open-deep-research), [implementation](https://github.com/langchain-ai/open_deep_research) | A scope brief, parallel subresearch with compressed findings, supervisor gap checks, then one report-writing pass. The team abandoned parallel section writing after seeing disjoint reports. | Keep Scout's parallel research and single synthesis; measure whether its compact ledger loses decisive detail. | Architecture experience, not controlled evidence that LangGraph or a supervisor improves Scout. A new supervisor would duplicate Scout's planner/gap analyzer without first proving a need. |
| [ALCE](https://aclanthology.org/2023.emnlp-main.398/), [evaluation code](https://github.com/princeton-nlp/ALCE/blob/main/eval.py) | Evaluates fluency, correctness, and citations on ASQA, QAMPARI, and ELI5. Its citation recall checks whether each sentence is fully supported by the cited set; precision penalizes citations that fail to support or add nothing to that set. | An evaluator over the *rendered* report, with sentence/atomic-fact support and citation necessity kept distinct. | Entailment-model metrics need calibration against human labels. A sentence may contain multiple independently checkable facts, so sentence-level recall alone is coarse. |
| [FActScore](https://aclanthology.org/2023.emnlp-main.741/) | Splits long-form output into atomic facts and calculates the supported share. It was developed and validated chiefly on people biographies. | Atomic-fact audit of summary, answer, tables, and caveats; count supported facts as well as their percentage. | A terse, heavily abstaining report can achieve high precision while omitting the user's requested facts. The original knowledge-source setup is not Scout's citation contract. |
| [DeepResearch Bench II](https://github.com/imlrz/DeepResearch-Bench-II), [paper](https://arxiv.org/abs/2601.08536) | Expert-report-derived rubrics for information recall, analysis, and presentation: 9,430 rubric items in the released benchmark. | Continue case-level, dimension-level report evaluation and diagnose which workflow stage lost a point. | A rubric point being present does not prove its source supports it. Public or repeatedly inspected tasks are development data; Scout's `drb2-task8` is known to be contaminated by earlier model-visible material. |
| [DeepResearchGym](https://www.deepresearchgym.ai/), [paper](https://arxiv.org/abs/2505.19253) | Stable search over fixed ClueWeb22/FineWeb corpora and separate judgments for user alignment, retrieval faithfulness, and report quality. | Evaluation-only search adapter or, more simply, recorded search fixtures for paired retrieval tests. | Static web corpora can miss current, official, or scholarly material. Its public API's data policy must be checked before sending private study queries. |
| [BEIR](https://github.com/beir-cellar/beir) | Relevance-labeled retrieval tasks and metrics such as nDCG, recall, and precision at rank cutoffs. | A metric format for Scout-specific source and passage relevance labels. | A high nDCG score is not a supported answer. BEIR's domains and relevance judgments cannot stand in for Scout report evaluation. |

These systems point to a common pattern: strong long-form output needs enough *relevant evidence*, a way to preserve its meaning during compression and synthesis, and evaluation that detects both omissions and unsupported additions. That pattern is an inference from their methods, not a measured ranking of frameworks.

### Framework fit for evaluation and optimization

[Ragas](https://docs.ragas.io/en/latest/concepts/metrics/available_metrics/) is useful as a metric taxonomy: its [context recall](https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/context_recall/) asks whether required information was retrieved, and [faithfulness](https://github.com/vibrantlabsai/ragas/blob/main/docs/concepts/metrics/available_metrics/faithfulness.md) asks whether response claims follow from retrieved context. Scout needs the narrower question of whether each claim follows from the *passage it actually cites*. Implement and calibrate that contract in Scout's existing evaluation lane before adding a second evaluation dependency.

[Pydantic Evals](https://pydantic.dev/docs/ai/evals/evals/) already matches this repository's typed Python evaluators; [`quality.py`](../src/research_loop/quality.py) uses its `Evaluator` interface. It can collect per-case report and trajectory measures, while [Logfire experiments](https://pydantic.dev/docs/logfire/evaluate/datasets-and-experiments/) compare cases, evaluator results, errors, duration, tokens, and cost. Postgres remains the durable record of runs. The missing piece is a single rendered-report projection shared by rubric, quality, and support evaluators, plus a stage funnel tied to saved run IDs.

[DSPy optimizers](https://dspy.ai/3.2.0/learn/optimization/overview/) can propose instruction or prompt variants against a chosen metric and development set. Applying one now would optimize toward a judge that still misses the summary and can accept mismatched citation support. After calibration, an offline optimizer trial could generate variants for the *existing* planner or scout instructions; each candidate would then be frozen in `prompts.py`/`agents.py` and tested on untouched cases with the normal study controls. There is no present case for importing its runtime into Scout.

## Where Scout currently loses information

The relevant local flow is `WebSearch`/`SearchChain` and four scout tools in [`tools.py`](../src/research_loop/tools.py), typed `ResearchResult` values in [`schemas.py`](../src/research_loop/schemas.py), `EvidenceLedger` in [`evidence.py`](../src/research_loop/evidence.py), and `_Run` in [`scout.py`](../src/research_loop/scout.py). The current ledger makes checked research available to synthesis; `EvidenceLedger.passages()` sends supporting evidence as citable passages. The end report is evaluated through different projections in [`evals.py`](../src/research_loop/evals.py), [`quality.py`](../src/research_loop/quality.py), and [`audit.py`](../src/research_loop/audit.py).

The next study should answer **where a required fact was lost**, not only whether the final rubric point is absent. Use a stage funnel for each frozen case and a small set of manually identified decisive facts and sources:

| Stage | Question | Observable measure |
| --- | --- | --- |
| Discovery | Did the search/scholar results contain a decisive source? | Decisive-source recall among returned result IDs; query count and empty/error share. |
| Acquisition | Did Scout retrieve the relevant part of that source? | Decisive-passage recall in fetched text; source access tier and failed/blocked fetches. |
| Evidence | Did a scout record the fact with a correct quote and stance? | Verified, source-located fact recall; numeric/entity quote fidelity; contradiction retention. |
| Selection | Did the decisive passage reach the synthesizer? | Decisive-passage recall in the sent context; passage diversity; context tokens. |
| Synthesis | Did the reader-visible report state the fact accurately and cite that passage? | Correct atomic facts, material omissions, citation recall and precision, directness. |
| Delivery | Was a usable report produced under the run's limits? | Completion and partial-report rate, total elapsed time, known charge, uncertain charge, retries and 429s. |

This extends PaperQA2's source-paper funnel to Scout's end-to-end report. It also prevents a final score from hiding very different failures: an omitted search result, an unread page, a dropped scout result, and a synthesizer omission need different fixes. Do not infer recall from `coverage_states()` alone: today a claim naming a coverage ID can mark it covered without proving the requested fact.

## A stronger report-quality contract

Scout needs a common representation of what the **reader actually sees**. The current historical rubric reader omits `executive_summary`, while the support audit consumes `ReportClaim` values and can inspect evidence beyond the displayed citation. The v8 Anthropic pointer check establishes that a citation points to a passage sent to the model; it does not establish that every factual unit in the cited text block is entailed by that passage. The local review reproduces this distinction.

Define a versioned `ReportAssertion` projection from the rendered report, without immediately changing the run's roles or stored legacy grades. Each assertion should carry a location (summary, answer, table cell, caveat), exact text span, atomic factual propositions, exact displayed citation IDs, their source-passage locators, and whether the assertion is a fact, interpretation, or framing. Code can establish locations, pointers, and provenance; semantic support needs a calibrated human or model judgment. This is the evaluation contract to test, not a claim that all atomic extraction can be made deterministic.

Score at least five distinct properties:

1. **Request fulfillment:** Was each required fact or comparison answered, or explicitly marked unestablished? Count material omissions. A report can be impeccably cited and still fail the user's question.
2. **Factual support:** For each atomic proposition, does the *displayed passage or cited set* support it? Record `supported`, `contradicted`, `unverifiable`, and `not checked`. Count both the supported share and the number of correct requested facts, following the lesson of FActScore.
3. **Citation recall:** What share of citation-worthy propositions have complete support from their cited passages? Count uncited summary and table facts too. ALCE's sentence measure is a starting point; use atomic facts for compound sentences.
4. **Citation precision:** For each displayed citation, does it support a relevant part of its assertion, and does it add information the other citations do not? A valid pointer can still be irrelevant or redundant, as ALCE's [evaluation logic](https://github.com/princeton-nlp/ALCE/blob/main/eval.py) makes explicit.
5. **Reader utility:** Organization, directness, comparison quality, useful caveats, and clarity, judged separately from provenance. STORM and OpenScholar measure these with human/editor assessments as well as automated checks.

Report these as a **vector**, not one unqualified quality number. Otherwise a verbose system may gain rubric coverage while increasing false facts and cost, and a terse system may raise precision by saying little. Include failure and partial-run rates in every aggregate. Annotate a human reference sample first and publish agreement and disagreements by defect type; model judges are measurement instruments, not ground truth. Historical `JUDGE_VERSION` grades should remain comparable only within their frozen reader/prompt rules. A new rendered-report evaluator needs its own version and calibration.

## Experiments that fit the existing architecture

### 1. Build the observation layer before changing generation

Create a small, frozen set of development questions spanning a direct fact, a structured comparison, a contested question, and a broad synthesis. For each, mark a few decisive facts and primary sources; retain exact source and passage locators. Record the stage funnel above on existing runs where artifacts permit, and mark missing telemetry as unknown instead of imputing success. Include `drb2-task8` only as development data because of the documented contamination. Keep the repository's held-out cases sealed for a final confirmation. This is mainly offline work; it requires neither a new service nor a model role.

The first implementation dependency is the evidence identity and report-assertion repair described in the [architecture review](architectural-review-2026-09-29.md#workflow-review-and-ranked-findings). In particular, numeric punctuation and signs must survive quote verification, and a report assertion must be graded against its displayed passage. Until then, a higher retrieval score can mask an attribution defect.

### 2. Passage selection, on one fixed ledger at a time

Compare today's all-passages context with an optional selector using the *same stored ledger and synthesizer*. Start with deterministic lexical relevance to the question and coverage item, a cap per source, and explicit preservation of contradicting passages. Keep source-located text and quote checks attached. The selector belongs between `EvidenceLedger.passages()` and `_Run._synthesize()`; it is not another scout or a vector database. PaperQA2 and OpenScholar motivate the test, but Scout should earn the extra ranking complexity with an ablation.

Before any report-quality comparison, test whether the selected set retains manually marked decisive passages and opposing evidence. Then run paired syntheses and measure context tokens, citation support, material omissions, cost, and time. A candidate that saves tokens but drops a decisive passage fails even if its prose looks smoother. Try semantic reranking only if this simpler baseline has a documented failure, and price its model or service calls explicitly.

### 3. Perspective coverage and outline, only for broad tasks

Compare the current planner with a version whose existing `ResearchPlan.coverage` asks for distinct perspectives, stakeholders, methods, or competing explanations *when relevant*. Have the synthesizer derive an outline from coverage items with verified passage IDs. Do not add persona agents. Freeze the search plan when testing the outline alone; freeze the synthesizer configuration when testing planning alone. Measure missing dimensions, unsupported associations, source diversity, length, cost, and reader organization. STORM's gains make this plausible, while its reported bias and over-association warn against treating added breadth as automatic quality.

### 4. Scholarly citation traversal as a narrow search tool trial

For literature cases where a known useful paper's references are available, test one bounded citation-expansion step against the same search budget. Record how often it discovers a decisive paper *not already found*, and whether the paper's relevant passage is actually acquired and cited. PaperQA2's LitQA2 source-paper recall gives a rationale; it does not establish value for Scout's general web cases. Keep ordinary `scholar_search`/`scholar_get` and the plan/ledger boundary. Avoid promoting cited-paper popularity into an authority score.

### 5. Evaluation-only stable retrieval

Replay recorded `WebSearch` responses through Scout's existing search interface, or test a `WebSearch` adapter over DeepResearchGym when its corpus fits the frozen question. Pair arms on the same results and page snapshots so search drift cannot masquerade as a prompt/model effect. Use Scout-specific relevance labels and BEIR-style recall/rank metrics for retrieval. Confirm any promising result on the live engines, because static corpora change the task. This is especially useful for regression tests and passage-ranker ablations, not a replacement production search engine.

## How to decide whether a change helped

Use the repository's [evaluation protocol](evaluation.md): one changed factor per comparison, stored plans for scout comparisons, stored ledgers for synthesis comparisons, versioned prompts and evaluators, paired replicates, and failed runs in the denominator. Write the decision rule and hard dollar ceiling in a study spec before a paid run; run its dry and cheap checks per `AGENTS.md`. No paid comparison is proposed as approved by this report.

For each candidate, require **no new decisive factual defect or source-attribution regression**, then look for a material gain in the dimension the change targets: decisive-passage recall for retrieval, required-fact coverage for planning, or citation support and reader utility for synthesis. Show the case-level results and the cost/time tradeoff; avoid selecting a default from one run per arm or a single averaged score. Use human review on disputed decisive facts and on judge disagreements. Reserve held-out DRB2 cases for a final check after development choices are frozen.

Progress should be tracked at three levels:

- **Component:** search hit/empty rates, passage recall, exact-quote fidelity, selector retention, context tokens.
- **Report:** requested facts answered, unsupported atomic facts, citation recall/precision, directness and organization.
- **Operation:** completion, partial reports, deadline/budget stops, settled and uncertain spend, latency, retry/429 incidence.

These levels make a change interpretable. A better report after a retrieval change should have a visible path through improved source/passage recall; a more polished report with unchanged evidence and more false statements is a regression. A lower cost with a higher partial-run rate is not a clean efficiency gain.

## Recommendation

First implement a common source-passage and rendered-assertion contract, then instrument the evidence funnel and human-label a small reference set. The first generation ablation should be bounded passage selection on fixed ledgers; the second should be perspective/outline planning on broad questions. Keep citation traversal and stable-corpus retrieval as narrower trials. Use ALCE/FActScore ideas in the **offline evaluator**, not as a new runtime validator role. Preserve the existing PydanticAI workflow and Postgres/Logfire ownership while the tests reveal where Scout actually loses quality.

The central unresolved question is empirical: do Scout's broad reports fail mainly because the right evidence was never acquired, because it was lost before synthesis, or because the synthesizer failed to use it? The stage funnel is designed to answer that before further architectural expansion.
