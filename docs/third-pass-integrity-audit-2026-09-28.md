# Third-pass integrity audit: cache, spend, and retrieval contracts

**Date:** 28 September 2026

**Code reviewed:** `c3bea6a` (the current audit branch adds documentation only)

**Related reports:** [Independent architecture audit](architectural-audit-2026-09-28-independent.md) and [workflow audit addendum](workflow-audit-addendum-2026-09-28.md)

This pass follows five contracts across module boundaries that the earlier two reports did not fully test: a blocked document through a cached page window; an uncertain provider charge through the parent study ceiling; a fallback copy through source attribution; a scholarly search limit through tool output; and a development-case name through the study summary. These findings add to, rather than supersede, A01–A10 and W01–W08. In particular, T02 is a narrower accounting failure than A09's warning that reservations are estimates, and T03 is a concrete fallback route within A07's broader source-identity problem.

## Method and limits

I inspected the current implementations and related offline tests, then used deterministic, local probes with cache entries or mock HTTP responses. The probes establish what the code accepts under specified inputs; they do not measure how often providers return mismatched data or how often real studies encounter the edge cases. I made no model, paid-service, or database calls. No production code, cases, packets, study records, or the supplied `audit.md` were changed.

## Findings, ranked by potential impact

### T01 — P1: a cached later window can bypass a new document block

`WebAcquisition.fetch` checks a URL block before its cache. For a cache hit at `start > 0`, however, it tests the current document policy against a separately cached `start=0` window. If that opener is absent, `or {}` turns the policy input into empty text and the later window is returned (`src/research_loop/web.py:280-295`). A later window is a valid independent cache entry: `_window` writes only the requested window (`web.py:336-350`). The cache's `reuse` and `replay` modes read old entries; study mode defaults to reuse (`src/research_loop/acquisition.py:85-113`, `src/research_loop/config.py:442-452`).

**Offline counterexample:** A `start=1000` window for an opaque CDN URL existed in the cache, while its `start=0` window did not. With a newly configured blocked title, replay returned the later text with no `BlockedSource` error. A fresh fetch would check the full document before caching, and the existing cache test covers both windows being present (`tests/test_source_policy.py:175-201`); neither protects this missing-opener case. The bypass requires the blocked identity to be discoverable from opening content rather than the URL. That condition is realistic for the CDN copies already described in the source-policy test, but this probe does not show a saved run that used one.

**Repair and check:** Store a code-owned document-policy verdict or enough opening identity with *every* window. Until that exists, treat a later-window cache hit without a compatible opening as a miss; replay should return `CacheMiss`, and live/reuse should re-fetch and check the full document. Add an offline test that first caches only a later window under an unblocked policy, then introduces a blocked title or DOI and asks for that window. Version the fetch/cache semantics and invalidate incompatible entries when implementing the repair.

### T02 — P1: the parent study ceiling ignores a child's retained uncertain reservation

`StudyBudgetModel` reserves before a provider request. It releases a reservation on a known 429, but leaves it in place on a request failure with unknown usage (`src/research_loop/study_budget.py:148-185`). `_Run._recorded` saves that reservation in `checks.study_budget_reserved_usd` even when the known `cost_usd` is zero (`src/research_loop/scout.py:752-766`). A scout failure can be converted into a cut-off result, allowing a partial run and `run.json` (`scout.py:613-636`, `cli.py:62-75`).

The parent study counts a full child cap only when `run.json` is absent or its `cost_usd` is `null`; it otherwise charges the reported cost plus separately unreadable grading steps (`src/research_loop/study.py:313-323, 536-542, 604`). It never reads the child's persisted reservation. In an offline injected three-replicate study with a $0.50 ceiling and a $0.25 child cap, each partial child record reported `cost_usd=0.0` and `study_budget_reserved_usd=0.25`. The parent passed a $0.25 cap to **all three** children, exposing $0.75 of uncertain reservations against a $0.50 ceiling while reporting zero study charge. This is a deterministic ceiling-accounting error. It does not prove that either mock request was billed, or that an actual provider can exceed a child's own reservation cap.

**Repair and check:** For a saved child, charge the parent at least `max(reported cost, retained reservation)` while any reservation is unsettled, without adding the two (known charges are already included in the reservation total). Keep known cost and uncertain reserved exposure as separate study columns. Treat a malformed or missing accounting field conservatively, especially on partial/failed records. Add a regression test with a saved partial run whose `cost_usd` is zero and reservation positive; the second child must receive only the ceiling room left after the first reservation. This changes budget behavior, so bump `BUDGET_POLICY_VERSION` and document the new ceiling rule.

### T03 — P2: a fallback can attribute another work's text to the requested URL

The Europe PMC route searches for a DOI but uses the first hit's `pmcid` without comparing the returned hit's DOI to the requested DOI (`src/research_loop/reading.py:134-151`). CORE does perform that comparison (`reading.py:174-195`). The Exa reader similarly selects the first `/contents` result's text without checking its returned URL (`reading.py:221-235`). `WebAcquisition._window` then attaches the *requested* URL to the fallback text, retaining only a `via` label to explain the retrieval route (`src/research_loop/web.py:336-350`).

**Offline counterexample:** A mocked Europe PMC search for `10.1234/expected` returned a hit with `doi=10.9999/wrong` and a usable full-text XML response. `OpenAccessReader.read` returned that wrong article's text as `oa:europepmc`. This tests a mismatched provider response, not a claim that Europe PMC normally makes this mistake. It exposes a trust boundary: a lookup result or crawl result can be inconsistent, and the current code lacks the identity check needed before attribution. If a fallback copy is legitimately hosted at a different URL, string URL equality alone is too strict; DOI/work identity or a validated canonical relation is needed.

**Repair and check:** Require a matching DOI (or a checked work relationship) before accepting an open-access hit, validate the identity reported by paid readers when provided, and retain both requested and retrieved locations in the document/evidence record. Reject a deliberately mismatched mocked hit; also test a legitimate repository copy of the same work. If source identity or the model-visible fetch result changes, bump the affected fetch/evidence version.

### T04 — P2: `scholar_search(limit)` returns more than its stated total

The tool tells scouts it returns up to `limit` works, capped at 25 (`src/research_loop/tools.py:84-91`). `ScholarClient.search` requests and appends up to `limit` OpenAlex works, then appends up to `min(5, limit)` arXiv works (`src/research_loop/scholar.py:187-215`). An offline mocked `limit=1` search returned two works with `truncated=False`. At `limit=25`, the response can contain 30 works. The `truncated` flag reflects the OpenAlex count only, not omitted arXiv results.

The extra records can increase context and model cost and make arm comparisons harder to interpret, although retrieving both published and preprint records can be useful. Decide whether `limit` means a total or a per-provider allowance. If total, cap the merged result and report truncation for the combined set; if per-provider, make the tool contract and returned counts explicit. Test `limit=1`, mixed-provider results, and arXiv-only overflow. Keep preprint/published records distinct when their content or version differs.

### T05 — P3: the development coverage screen calls negated mentions “found”

`coverage()` concatenates conclusions and claim statements and searches them for expected-term regexes; it does not inspect polarity, support, or whether the term names the intended entity (`src/research_loop/coverage.py:28-43`). The study summary labels the resulting count “Coverage found (named)” (`src/research_loop/study.py:680-696`). An offline task8 record with the sole conclusion “ICSD was not found; the team was nomadic,” no claims, and an unresolved ICSD item produced `found=['NOMAD','ICSD']` and `named_only=[]`. The unbounded `nomad` pattern matches “nomadic”; “was not found” still counts as ICSD found.

This is a cheap development diagnostic, not the rubric judge, and the module states that it does not replace grading. Its current label nevertheless invites an incorrect interpretation in a screen. Rename the metric to **term mentions** if it remains purely lexical, use word boundaries for entity names, and do not use it as evidence of research coverage. If an established-coverage metric is desired, derive it from supported, evidence-linked per-item claims and test explicit negation and unresolved-only cases. Avoid tuning this screen on held-out cases.

## Decision order

1. Repair T01 and T02 before more paid studies. T01 can contaminate an arm's source exclusions; T02 weakens the parent study ceiling precisely when cost is uncertain. W01's protected-argument override is another prerequisite for a trustworthy ceiling.
2. Repair T03 before treating fallback-recovered quotations as strong source evidence, alongside A03/A07's quote and identity checks.
3. Clarify T04's retrieval contract and T05's label before interpreting search-depth or coverage screens. These can be corrected without a new run role or service.

This audit does not change defaults or justify a paid rerun. After fixes, the narrow offline counterexamples above should be regression tests, followed by the repository's normal offline suite and a versioned, predeclared study when a default-changing question is ready.
