# Research Loop audit addendum: budget, evaluation, and storage edges

**Date:** 27 September 2026  
**Base commit:** `1f9233a3d68b3fa1ddda3a1cae0805fbd9b1feec`  
**Relationship to the first audit:** Supplementary findings for Claude; the original report is unchanged.

## Handoff to Claude

Continue implementing the first audit. Add **C01** below to the budget work: correcting the study-wide ceiling alone does not correct the separate per-request reservation problem. Include **C04** in persistence work if the success-write failure path is still unchanged. Schedule **C02** and **C03** with evaluation changes, preserving historical evaluator versions.

These findings were reproduced against the base commit, not Claude's unmerged implementation. A fresh fetch showed that `origin/master` still pointed to that commit at the start of this continuation. Recheck affected functions on the implementation branch before changing them. No production code was edited or committed during this audit.

## Results of this bounded continuation

Four additional offline probes were executed. They used the locked environment, including `pydantic-ai==2.48.0` and `genai-prices==0.1.8`. No provider calls, live billing tests, or paid model runs were made. The previous 272-test baseline was not repeated because the source commit had not changed.

| ID | Priority | Observation | Relationship to first audit |
|---|---|---|---|
| C01 | P1 | Long-context output pricing can exceed the per-request reservation even when usage stays within token bounds. | New concrete defect in `StudyBudget.reserve`; distinct from F05's outer study ceiling. |
| C02 | P2 | A quality judgment may mark a critical claim supported without a packet source and accept empty factual report anchors. | Extends F11's evaluator-contract concerns. |
| C03 | P2 | Rubric grading includes inventory statements absent from the displayed report and excludes the displayed executive summary. | Extends F01's representation mismatch; partly deliberate historical compatibility. |
| C04 | P2 | A failed success-record write discards a completed scout result and leaves its call marked running. | Reproduces a previously static concern under F09. |

## C01 — The request reservation samples output pricing without the input tier

**Code:** [study_budget.py:88–107](https://github.com/rjanseko/research-loop/blob/1f9233a3d68b3fa1ddda3a1cae0805fbd9b1feec/src/research_loop/study_budget.py#L88-L107), [settlement:126–134](https://github.com/rjanseko/research-loop/blob/1f9233a3d68b3fa1ddda3a1cae0805fbd9b1feec/src/research_loop/study_budget.py#L126-L134).

The guard samples input prices at 100,000 and 1,000,000 tokens and selects the larger rate. However, it calculates the output rate from `RequestUsage(output_tokens=100_000)` with no input tokens. When output pricing depends on input length, that sample selects the short-context tier. A conservative input-token bound does not compensate reliably for this underpriced output.

The following is an offline simulation using the installed pricing library's entry for `google:gemini-2.5-pro`. It is not a statement about a live invoice or a newly verified current vendor price.

| Quantity | Value |
|---|---:|
| Command cap | $1.05 |
| Calculated input-token bound | 304,099 |
| Simulated input usage | 300,000 |
| Output cap and simulated output usage | 24,000 |
| Reservation accepted before dispatch | $1.0002475 |
| Same library's joint input/output price | $1.11 |
| Budget total after settlement | $1.1100000 |

The simulated input is below the guard's bound and the output equals its explicit cap. Nevertheless, settlement exceeds the dollar cap. This isolates a pricing-tier defect rather than a token-estimation failure. The default model lineup was not shown to trigger it; the configuration accepts Google models, making it relevant to supported alternative lineups.

**Recommended fix:** Price input and output together under the applicable context tier when constructing a reservation. For monotonic pricing, calculate the joint charge at the input bound and output cap. If arbitrary pricing schedules are supported, explicitly consider tier boundaries and other applicable charge dimensions; do not assume independently sampled rates compose into an upper bound. Retain conservative treatment of uncertain usage. Record reservation overruns as integrity failures: discovering the overrun during settlement cannot undo an already dispatched charge.

**Regression criteria:** Below, at, and above every relevant tier boundary, the reservation covers the price returned by the same authoritative pricing function for admissible usage. Include large output caps, cached-input accounting where supported, and simultaneous reservations. Bump `BUDGET_POLICY_VERSION` and preserve the pricing-data version used by the run.

**Coordination:** Implement this in the request guard alongside, but separately from, F05's parent-study budget fix. Either layer can fail while the other works correctly.

## C02 — Quality verdicts can lack their required evidence basis

**Code:** [quality.py:160–191](https://github.com/rjanseko/research-loop/blob/1f9233a3d68b3fa1ddda3a1cae0805fbd9b1feec/src/research_loop/quality.py#L160-L191).

The quality validator correctly enforces dimension completeness, fact-target completeness, known source IDs, relevant sources for correct/incorrect fact-target verdicts, and a prohibition on the strongest overall rating when a decisive claim is contradicted. These are useful checks.

The remaining gaps are specific:

- Additional critical claims can be labeled `supported` or `contradicted` with an empty `source_ids` list. An empty list contains no unknown IDs, so the existing known-ID check passes.
- A fact target labeled `correct` can have an empty `report_anchor`.
- Dimension anchors need only be nonempty; `_validate` receives no report and cannot check that the text appears in it.

**Reproduced:** `_validate` accepted an overall level 3 judgment with a supported additional critical claim carrying no source IDs, empty fact-check anchors, and dimension-anchor text with no checked connection to a report. This demonstrates missing validation, not that a real judge commonly produces this output or that its factual verdict is necessarily false.

**Recommended fix:** Require packet-source references for resolved additional critical-claim verdicts. Require an actual report location for factual judgments about an asserted statement; handle `omitted` and `unresolved` explicitly. Prefer a stable assertion/block ID or exact quoted substring plus location over unconstrained paraphrased anchors. Validation should establish that the cited source and report passage exist; semantic adequacy still needs the judge or human review.

**Regression criteria:** A resolved critical verdict without a packet source is rejected or downgraded to unresolved. A supposedly quoted report passage that is absent is rejected. Legitimate omission verdicts remain possible without inventing text. Update `QUALITY_VERSION` when the grading contract changes; do not silently reinterpret stored assessments.

## C03 — Historical rubric scores evaluate a different text surface

**Code:** [evals.py:134 onward](https://github.com/rjanseko/research-loop/blob/1f9233a3d68b3fa1ddda3a1cae0805fbd9b1feec/src/research_loop/evals.py#L134), [render.py](https://github.com/rjanseko/research-loop/blob/1f9233a3d68b3fa1ddda3a1cae0805fbd9b1feec/src/research_loop/render.py).

`reader_text` appends all `FinalReport.claims` as “Key statements” and intentionally excludes `executive_summary` to preserve the earlier grading baseline. The ordinary renderer displays the executive summary and does not display every claim-list statement as a key statement. It may show weak statements separately when checks identify them, which does not make the two surfaces equivalent.

**Reproduced with distinct marker text:** A statement appearing only in the claim inventory was present in the grading input but absent from the rendered report. A statement appearing only in the executive summary was present in the rendered report but absent from grading input.

**Implication:** A rubric judge can award credit for content not delivered to the reader, or miss incorrect/contradictory content in the delivered summary. This is particularly important while Claude changes F01's report representation. A higher historical rubric score need not mean a better displayed report.

**Recommended fix:** Preserve the historical grader as an explicitly named legacy projection. Introduce a new version that grades the same reader-visible artifact used in production, with well-defined handling of provenance and review annotations. If inventory quality is useful, score it separately. Do not alter `reader_text` while keeping `JUDGE_VERSION=2` and claim old scores remain directly comparable.

**Regression criteria:** The new reader-quality projection includes factual text from summaries, answer paragraphs, tables, and caveats. Hidden inventory assertions do not earn reader-content credit. Stored grades identify the exact report projection and evaluator version. Compare old and new projections separately during migration.

## C04 — A storage failure after successful inference loses the result

**Code:** [scout.py:411–456](https://github.com/rjanseko/research-loop/blob/1f9233a3d68b3fa1ddda3a1cae0805fbd9b1feec/src/research_loop/scout.py#L411-L456), particularly the success write outside the exception-recording block.

The first audit identified this path statically. This continuation injected a `MemoryStore` implementation whose `finish_call(status="succeeded")` raises after a scripted model returns a valid research result. The real `_call` and `_scout` control flow then ran.

| Observed field | Result |
|---|---|
| Model's returned claims | One completed finding |
| `_scout` result claims | Zero |
| Cutoff reason | `unexpected error RuntimeError` |
| Stored call status | `running` |

The success-write exception occurs after `_call`'s error-recording `try` block. `_scout` catches it as an unexpected error and returns a cutoff, discarding the already computed output. This is separate from F09's cancellation-before-aggregation issue. It is not a PostgreSQL fault test: the failure was injected through the real storage interface using memory.

**Recommended fix:** Distinguish model execution failure from persistence failure. Preserve a completed checked result in orchestrator-owned state even if storage fails; surface a persistence error that prevents a false claim of durable completion. Use stable call IDs for idempotent retry and a bounded recovery artifact when needed. Do not conceal the database outage, repeatedly dispatch the model, or mark a write durably successful without confirmation.

**Regression criteria:** A successful result remains available after a failed write. Retrying persistence does not rerun inference or double-count usage. Test both failure before commit and ambiguous failure after commit. Recovery can reconcile a stale running row without discarding a completed result. Integrate with Claude's F09 changes rather than introducing a second competing lifecycle layer.

## Stop point and next continuation

This follow-up stops after the four offline reproductions and their code review. The first audit remains intact. Its 15 counterexamples plus these four give **19 executed probe scenarios**, not 19 independent production incidents.

No PostgreSQL server or Docker executable was available in the checked environment, so real database fault/concurrency testing remains unperformed. The memory-store injection strengthens the control-flow finding but does not substitute for transaction-level validation. Provider prices were evaluated from the locked library, not checked against live billing. No end-to-end quality claims were added.

The highest-value next action is for Claude to incorporate C01 and the applicable persistence correction, finish the first audit's fixes, and share a branch or commit. Review that diff next. Repeat only affected probes and relevant tests, followed by the repository's required validation. Avoid another broad audit of unchanged code.

## Reproduction script

Save the following beside the repository as `audit_continuation_probes.py` and run `.venv/bin/python ../audit_continuation_probes.py` from the repository. It uses scripted responses and the local pricing library; it performs no network or database requests. The script prints observed behavior on the audited commit; convert the expectations into regression assertions when fixing the code.

```python
"""Offline follow-up audit: no provider or database requests."""
import asyncio
import json
import warnings
from decimal import Decimal

import logfire
from genai_prices import calc_price
from pydantic_ai import FunctionToolset
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.usage import RequestUsage

from research_loop.agents import scout_agent
from research_loop.config import Settings
from research_loop.evals import reader_text
from research_loop.evidence import EvidenceLedger
from research_loop.quality import DIMENSIONS, QualityJudgment, _validate, find_quality_packet
from research_loop.render import render_markdown
from research_loop.schemas import FinalReport, ReportClaim, ResearchQuestion
from research_loop.scout import _Attempt, _Run
from research_loop.store import MemoryStore
from research_loop.study_budget import StudyBudget, upper_input_tokens
from research_loop.tools import TimedToolset

logfire.configure(send_to_logfire=False, console=False)
warnings.filterwarnings('ignore')


def emit(name, **data):
    print(json.dumps({'probe': name, **data}, sort_keys=True))


async def pricing():
    model = 'google:gemini-2.5-pro'
    messages = [ModelResponse(parts=[TextPart('prior result')],
                              usage=RequestUsage(input_tokens=300000, output_tokens=1))]
    parameters = ModelRequestParameters()
    settings = {'max_tokens': 24000}
    bound = upper_input_tokens(messages, parameters, settings)
    budget = StudyBudget(Decimal('1.05'))
    charge = await budget.reserve(model, messages, settings, parameters)
    usage = RequestUsage(input_tokens=300000, output_tokens=24000)
    actual = calc_price(usage, 'gemini-2.5-pro', provider_id='google').total_price
    budget.settle(model, charge, usage)
    emit('long_context_output_tier', input_bound=bound, simulated_input=usage.input_tokens,
         reserved=str(charge), actual=str(actual), cap=str(budget.cap_usd),
         settled_total=str(budget.reserved_usd))


def quality():
    packet = find_quality_packet('st04')
    judgment = QualityJudgment(
        overall_level=3, overall_reason='Strong answer.',
        dimensions=[{'dimension': name, 'level': 3,
                     'report_anchor': 'This text is not checked against a report.', 'reason': 'Test.'}
                    for name in DIMENSIONS],
        fact_checks=[{'target_id': target.id, 'finding': 'correct', 'source_ids': target.source_ids,
                      'report_anchor': '', 'reason': 'Test.'} for target in packet.fact_targets],
        critical_claims=[{'claim': 'Additional decisive claim.', 'finding': 'supported',
                         'source_ids': [], 'reason': 'No source IDs supplied.'}])
    accepted = _validate(judgment, packet)
    emit('unanchored_quality_judgment', overall=accepted.overall_level,
         critical_source_ids=accepted.critical_claims[0].source_ids,
         fact_anchor=accepted.fact_checks[0].report_anchor)


def grading():
    hidden = 'UNIQUE_ASSERTION_ONLY_IN_INVENTORY'
    summary = 'UNIQUE_ASSERTION_ONLY_IN_SUMMARY'
    report = FinalReport(title='Test', executive_summary=summary, answer='An ordinary answer.',
                         claims=[ReportClaim(statement=hidden)])
    ledger = EvidenceLedger()
    grade_text = reader_text(report, ledger)
    rendered = render_markdown({'run_id': 'test', 'question': 'Test?', 'status': 'complete',
                               'report': report.model_dump(), 'ledger': {}, 'checks': {}})
    emit('grading_surface_mismatch', inventory_in_grade=hidden in grade_text,
         inventory_in_rendered=hidden in rendered, summary_in_grade=summary in grade_text,
         summary_in_rendered=summary in rendered)


async def persistence():
    class FailSuccessfulWrite(MemoryStore):
        async def finish_call(self, call_id, **kwargs):
            if kwargs['status'] == 'succeeded':
                raise RuntimeError('Injected persistence failure after model success')
            await super().finish_call(call_id, **kwargs)

    settings = Settings(_env_file=None, logfire=False, offline_world=0,
        models={role: 'fake:fuzz@high' for role in ('planner','scout','synthesizer','fallback','judge')})
    store = FailSuccessfulWrite()
    run = _Run('Test?', settings, store, (), (), None, None)
    question = ResearchQuestion(id='q1', question='Test?')

    def respond(messages, info):
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {
            'question_id':'q1', 'question':'Test?', 'conclusion':'Finished', 'confidence':1,
            'claims':[{'id':'c1', 'statement':'Completed finding', 'confidence':1}]})])

    with scout_agent.override(model=FunctionModel(respond)):
        result = await run._scout(_Attempt(question), asyncio.Semaphore(1), Decimal('1'),
            TimedToolset(FunctionToolset()), asyncio.get_running_loop().time()+300)
    emit('successful_call_store_failure', returned_claims=len(result.claims), cut_off=result.cut_off,
         call_status=next(iter(store.calls.values()))['status'])


async def main():
    await pricing()
    quality()
    grading()
    await persistence()


asyncio.run(main())
```
