# Audit: what to revise in the current workflow (29 September 2026)

The user asked for suggested revisions to the current workflow after a summary of the study data ([takeaways](takeaways-2026-09-29.md)). This audit ranks them by what the data shows. It covers scout-v20 with synthesis v8 on branch `claude/citations-synthesis` (`626be7f`), and the uncommitted work on top of it (scout-v21, synthesis v9, evidence v8). It builds on the [architectural review](architectural-review-2026-09-29.md) and [research frameworks](research-frameworks-2026-09-29.md) notes of the same day, and cites their finding IDs. It made no model calls. The stored diagnoses of the DeepSeek study were read with `research diagnose --free`, which reuses stored grades and costs nothing.

## What the uncommitted work already covers

The working tree changes quote matching and source identity (evidence version 8):
- **Numbers keep their meaning in quote matching.** Signs, decimals, and percent signs survive normalization, so `1.2 percent` no longer verifies `12 percent`, nor `-5` verify `+5` (review finding E2).
- **Quote segments match in order.** A quote's pieces must appear in the source in the order they were quoted.
- **URL queries count.** A source's identity keeps its query string, so two records on the same path stay distinct.
- **Quotes point into immutable text.** Each verified quote records the ID of an immutable, hashed snapshot of the tool text it was found in, and its character spans in that text.
- **Access comes from the quote.** `evidence_is_read` takes a verified quote's access level from the text the quote was found in, not the most complete text returned for its source. So a snippet quote no longer counts as read because another part of the source was fetched in full (E2).
- **Support comes from the cited passages.** Report statements carry the passage IDs they cite. Statement support is computed from those passages, and an ambiguous or missing citation makes a statement unsupported (part of E1).

The revisions below take that work as done and do not repeat it.

## A correction: where points are lost

The free audit of six drb2-task8 reports on 28 September concluded that points are lost when scouts turn sources into claims. The rescout diagnoses since then put most of the loss one step earlier.

The diagnosis sorts each of a run's 52 rubric points by the earliest step that met it. Across the 23 rescouts of the v14 search study, the Serper rerun, and the DeepSeek study, 17 to 28 points a run were not found in any of the research, and 35 in one partial run. Only 0 to 4 a run were seen in the research but never claimed.

| Study | Rescouts | Not found | Seen, not claimed |
|---|---|---|---|
| Search engines, scout-v14 | 12 | 19 to 27 (35 in one partial run) | 1 to 4 |
| Serper rerun, scout-v15 | 6 | 17 to 28 | 1 to 4 |
| DeepSeek, stopped after 5 | 5 | 19 to 23 | 0 to 3 |

About 7 points are out of reach, because they probably appear only in the blocked expert report. That leaves roughly 10 to 20 reachable points a run that research never found. Examples are topology optimization's role, Monte Carlo Tree Search, and model-based design's advantages, which are the category-level material that review articles cover. The claims problem the report audit found is real, but it accounts for a few points a run.

Two limits apply. The diagnoses were graded by `zai:glm-5.3@high` alone. And how much of the research the judge is shown bounds what it can call "seen".

## Revisions, in order

### 1. Finish the provenance work and close its gaps

Every quality figure rests on the `supported` label, and the support audit found that about one in five quoted statements says more than its quotes. Beyond what the uncommitted work does, these remain:
- **A report missing its title or summary is still `complete`.** The run should be marked partial instead (S1).
- **Uncited factual sentences do not lower support.** The st07 resynthesis (`6e811f5f`) had 5 uncited facts, which do not count against the report's answer support. They should.
- **Mismatched citations are noted and then dropped.** They should go into `RunChecks.citation_problems`, so a run records them.
- **Contradicting evidence cannot be cited.** It reaches the synthesizer only in the JSON brief, so a report can cite one side of a dispute and not the other. It should be sent as citable passages, with a stance label that code sets (E3).

All of this is offline and test-driven. It changes evidence and synthesis behavior, so `EVIDENCE_VERSION` and `SYNTHESIS_VERSION` change with it.

### 2. Improve how sources are found and read, not only how claims are made

Given the correction above, this is where most reachable points are.
- **Make scholarly search useful.** Scouts were shown 545 scholarly works in the stored drb2-task8 runs and fetched 1. Most were off-topic, highly cited works such as AlphaFold, fairness surveys, and EEG reviews. Scholarly search should rank by relevance rather than by citations, and offer a filter for review articles. Results that share no terms with the query should be dropped. This is a tool change in `tools.py`, not a new role.
- **Require a review for each category question, in code.** The scout prompt already asks for surveys first, and the claim fix asks for category-level claims from reviews, but neither is checked. A category question should count as covered only once a review has been read in full or as an abstract.
- **Adopt Serper after its confirmation.** It cut empty and failed searches from about 40% to 4 to 7% in two studies. By a reading of its rule, the Serper rerun qualifies for the four-case confirmation. It has not been written up.

The measure for these changes is the diagnosis's count of points never found, which varies less than the rubric score.

### 3. Make coverage mean the requirement was met

`coverage_states` marks an item covered when any claim names it (E4). So a question can read as covered while two of its three categories have no disadvantage stated. Instead:
- items should be one per category and requested field, such as "exploration-based: disadvantages";
- an item should count as covered only when a claim addressing it rests on a verified quote from a source read as an abstract or in full.

The gap analyzer then pursues real gaps instead of labels. This is a check that only judges and adds no role, as AGENTS.md prefers.

### 4. Keep finished work when a run stops

**Save each scout's result as it finishes.** Scout results enter the ledger only after the whole wave returns, so a cancelled or stopped run loses scouts that had already finished and been paid for (R1). Each checked `ResearchResult` should be saved as it finishes, keyed by run, question, and attempt, and merged into the ledger idempotently.

**Check the deadline setting.** It looks mismatched and needs a test before any change:
- The scout's budget withdraws its tools when less than `return_within` is left, and `scout.py` sets that to `limits.request_timeout_seconds`, 120 seconds by default.
- A scout request may run up to `scout_request_timeout_seconds`, 600 seconds.
- Stored Luna@xhigh result requests carried 200,000 to 365,000 input tokens and took longest.

So a slow result request that starts with a little over 120 seconds left may be cut off at the deadline, and a cut-off scout keeps no claims.

### 5. Hold synthesis where it is until it is graded

The citations path works, and every citation of its Opus 5.5 resynthesis matched its passage. But nothing on it has been graded, and the data says synthesis loses little: reports cite 90 to 100% of their ledgers' claims. Beyond sending contradicting passages (revision 1), passage selection in the manner of PaperQA2 should wait until exact passages can be audited, as the research-frameworks note also concludes.

## What not to change yet

- **The scout model.** Luna@xhigh is the cheapest and fastest scout. On the one plan with all three arms, DeepSeek Flash@xhigh claimed 3 points more at 2.2 times the cost and 25% more time. V4 Pro@xhigh claimed 2 fewer at 5 times the cost. Both are within noise.
- **Deep as the default depth.** Deep against standard was undecided: 5.0 and 4.7 points more under the two judges, at 1.75 times the cost.
- **New run roles or a supervisor.** Nothing in the data points to a missing role. It points to finding sources and to how support is checked.

## Measurement these revisions need

These are not workflow changes, but without them the revisions above cannot be judged.
- **An evaluator that grades the rendered report.** It should see the text a reader sees, including the executive summary, which `evals.reader_text` leaves out (V1).
- **More than one case.** Studies should run on the four newer development cases as well as drb2-task8, which is contaminated since scout-v6. The held-out cases should be kept for confirmation.
- **Sizing before paying.** Each study's detectable difference should be computed first. At three runs per arm it has been 7.5 to 14 points of 52.

## Cost and order

Revisions 1, 3, and 4, and the scholarly-search part of revision 2, can be built and tested offline. So they fit the current pause on paid calls. Revision 1 finishes work already in the tree and comes first. Measuring any of them needs a paid study, with a spec, a decision rule, and the user's approval once the contamination audit passes.
