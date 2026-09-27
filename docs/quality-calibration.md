# Human reference marks for the first quality packets

These marks calibrate a model evaluator; they are not automatic grades or a selection decision. The source packet in `quality_packets.jsonl` was checked against the official [ILSVRC task list](https://www.image-net.org/challenges/LSVRC/2016/index.php) and [results](https://image-net.org/challenges/LSVRC/2016/results), [OpenAI's Verified introduction](https://openai.com/index/introducing-swe-bench-verified/) and [later reappraisal](https://openai.com/index/why-we-no-longer-evaluate-swe-bench-verified/), and [METR's maintainer review](https://metr.org/notes/2026-03-10-many-swe-bench-passing-prs-would-not-be-merged-into-main/). Packet passages are paraphrases, not report excerpts. They were reviewed on 25 September 2026; changing one creates a new packet version.

| Saved run | Overall reference (0–3) | Decisive fact marks | Human review note |
|---|---:|---|---|
| `95f9ed4d-f2ec-4269-b8a5-a874890e4018` (st04 Flash high) | 2 | Premise correct; video task correct | Gives the direct answer and official task list. It spends substantial space on alternate video subtasks. Its NUIST Task 3a detail was unverified by its own retrieved quote, although the independent official results packet confirms the value. |
| `c6d1848a-cc4b-4734-ac37-510d2bf6fb6c` (st04 Luna high) | 3 | Premise correct; video task correct | Gives the direct answer quickly, identifies the relevant task, and cites the official results. Its optional NUIST Task 3a detail matches the independent results page. |
| `f1558521-b72f-445d-9cea-be9025b11c7a` (st07 Flash high) | 2 | Origin correct; test limitations substantially correct; contamination/deprecation correct as an OpenAI claim; maintainer gap omitted | Direct, broad synthesis with several caveats. The run could not read OpenAI's 2026 page and relied on snippets and secondary reports for a decisive point; the packet independently confirms OpenAI's published position. One report statement has no supporting ledger evidence. The METR maintainer-review result is absent. Quantitative preprint findings need separate source audits before trusting them. |
| `e344f2fd-7938-4609-b3a7-119363400628` (st07 Luna high) | 1 | Origin correct; test limitations partly covered; contamination/deprecation omitted; maintainer gap omitted | The report is clear about its limits and does not invent a contamination conclusion. Its q2 scout failed on a provider 429, so it cannot answer a central part of the question. Its current quality mark describes this partial report, not Luna's inherent ability on st07. |

The evaluator should keep overall quality and factual results separate. For st07 it should flag the missing contamination evidence and maintainer gap; for st04 it should not penalize the true NUIST detail merely because the run's own quote check failed. A decisive disagreement with these marks gets human review before model selection.

## First paid calibration (25 September 2026)

The user approved two saved-report assessments with separate pre-dispatch ceilings. Both calls used
`openai:gpt-6-sol`, evaluator v1, and the reviewed packet v1. The judgments and transcripts are in
Postgres `quality_assessments`; no new Scout research was run.

| Report | Assessment ID | Human / judge overall | Factual result | Actual / reserved / ceiling |
|---|---|---:|---|---:|
| st04 Flash high | `31742d33-2546-48d6-a564-de87e6163d2d` | 2 / 3 | Both targets correct; the two official-result claims checked were supported | $0.025422 / $0.383184 / $0.75 |
| st07 Flash high | `8d3582ef-7a9d-48f1-ab23-647b1bce0f64` | 2 / 2 | Three targets correct; METR maintainer-review finding omitted | $0.070892 / $1.458256 / $3.00 |

The st04 one-level disagreement is about reader utility, not the factual answer. The judge considered
the report's extra task tables and cautious, unverified NUIST detail harmless; the human reference
marked that detour as a material reservation for a simple false-premise question. The saved Luna st04
report answers the same question more directly and cheaply. Do not tune the judge to this one example
or treat v1 overall scores as a selection rule yet. Check the other saved st04 and st07 reports with
separately approved calls or a blind human pair review, then freeze any evaluator revision.

The st07 judgment matches the human overall mark and identifies the missing maintainer evidence. It
also marks several numerical claims `unresolved` because packet v1 contains only three source summaries;
that is a packet-coverage limit, not proof those claims are false. Both assessments used one request.
Their combined actual charge was $0.096314, below the combined $3.75 ceiling.

## Frozen external hard cases

Two English, CC BY 4.0 cases from [DeepResearch Bench II](https://github.com/imlrz/DeepResearch-Bench-II)
are now frozen as `drb2-task8` and `drb2-task68-plus` in `study_cases.jsonl`, while st04 and st07 remain diagnostics:

| Official ID | Domain | Expert rubric points | What it tests |
|---|---|---:|---|
| `task8` (idx 16) | Materials inverse design | 52 | Synthesis of three method families, their limits, and source-backed database comparison. |
| `task68+` (idx 46) | Cloud auto-scaling | 54 | Reactive versus proactive methods and evidence for five practical challenges. |

Selection was based on the official English tasks' topic and scope, before any Scout output on these
cases was read. The dataset snapshot was `imlrz/DeepResearch-Bench-II` commit
`b38f360603db9531b102aef8c166cedb8509b6f6` (download SHA-256
`263aaabb8c279fb16cbe7c9499afe82d657a8ab3ccfb07ace084387e367d921a`). Each case now preserves the exact `content.task`, expert rubric, blocked source URLs, license, official ID and index, and dataset revision. The case content digests are `840c63bd8195a546bbd3ee4bee15ba24aae4fee7e34f06b7461651d641ad4367` and `2ef645b6ab3c877e82eaca77463f873fceaebe3d4f274f53dc4552f5a3208500`. The Scout case command sends only the task to research agents; the rubric and blocked expert report remain out of research context. Grading requires the saved run to match the frozen case and has a separate enforced pre-dispatch ceiling.

These are stress cases: a bounded Scout run may return partial coverage of a rubric drawn from a long
expert report. Report task coverage, unresolved sections, cost, and deadline behavior separately from
overall reader quality. The existing seven-country pension case (`task2+`, 72 points) is an extreme
stress diagnostic if the first two cases show the workflow can finish useful research.

## First hard-case baseline and budget correction

The user approved one `drb2-task8` baseline Scout run with a $3.00 pre-dispatch ceiling and the
ordinary $0.75 soft budget. Run `53478812-e5d0-4b89-82cc-031184cfe24f` took 103.7 seconds and
charged $0.043367815. It reserved $2.4916688 under `byte-reserve-v1`. Two of four scouts returned
claims, with 11 distinct sources, 10 read as full text or abstracts, and 26 of 28 quotes verified.
The exploration and optimization scouts were refused before later requests because the four-times-byte
reservation estimated more than one million input tokens, despite their recorded model usage being
about 90,000 and 111,000 input tokens. Synthesis was not dispatched: its $1.1928 reservation would
have exceeded the $3.00 ceiling. This run has no final report or rubric grade, so it is a budget-guard
diagnostic, not a quality score for Scout or Luna. The `unpriced call` review flag was also misleading:
the refused synthesis sent no request.

The guard now uses `byte-reserve-v2`: two times serialized request bytes plus 16,000 fixed input
tokens, with the same output caps and atomic pre-dispatch ceiling. The doubled byte count remains a
conservative allowance for message framing and serialization. It stops treating zero-request budget
refusals as unpriced calls. An offline regression checks a 300,000-byte history that v1 would reject
as above the million-token range. The next paid step is one separately approved baseline retry of the
same frozen case under the same $3.00 ceiling. Inspect its coverage and final report before paying to
grade it or comparing gap follow-up. No follow-up or grading call has yet been made on these cases.

## Baseline retry and settled reservations

The user approved one retry of the same frozen case under `byte-reserve-v2`. Run
`d106c122-a47b-422a-9ab1-48def7b6e700` took 181 seconds and charged $0.05925755. All four scouts
returned claims, with 62 of 71 quotes verified and 66 items read as full text. Synthesis was again not
dispatched: the scouts had charged about $0.05 but still held $1.8206 of reservations, and the
synthesizer's $1.1972 reservation would have crossed the $3.00 ceiling. The run is partial with no
report, so it is a second guard diagnostic rather than a quality result. Its `unpriced call` flag was
also still wrong: PydanticAI counts a request before the guard refuses it, so v2's zero-request check
never applied to a real refusal.

The guard now uses `byte-reserve-v3`. It still reserves the same conservative maximum before dispatch,
then replaces the reservation with the priced charge when the response returns. A failed request, or
one whose usage cannot be priced, keeps its full reservation. A refusal that was a call's only request
is no longer reported as an unpriced call. Offline tests cover settling, a kept reservation after
failure, streamed responses, and sequential requests that v2 would have refused. The next paid step
is again one separately approved retry of `drb2-task8` under the $3.00 ceiling.

The approved `byte-reserve-v3` retry, run `e16d7c7b-3ea9-491e-ad69-c01ae3e71ae0`, took 3.1 minutes and
charged about $0.06. All four scouts returned claims from 19 sources, 18 read as full text or
abstracts, and the guard dispatched synthesis with room to spare. Anthropic rejected that request with
HTTP 400 because the configured API key is not scoped to a workspace. The run is partial with no
report. It confirms the settled reservations, but it is a credential failure, not a quality result.

After the key was replaced, replicate 4, run `b61f1b55-ef3e-43c8-9e34-510df86b9061`, completed in 2.6
minutes for $0.22, of which Opus synthesis was $0.17. All four questions returned evidence, and 38 of
42 quotes were verified. The version-2 judge gave it 20 of 52 points (0.385) for $0.043, recorded as
grade `214fb3be-136f-4cd7-89d7-f902ee470660`. Presentation met 3 of 3, analysis 5 of 12, and
information recall 12 of 37.

Most of the loss is in the database list, which met 4 of its 21 points. The report lists five
computed-data platforms (Materials Project, AFLOW, OQMD, JARVIS, NOMAD). The rubric expects the
experimental and specialist databases ICSD, the Cambridge Structural Database, the ASM Alloy Center, and
DDSE, each with a description and URL. The method section names most expected algorithms but does not
explain how reinforcement learning, GANs, genetic algorithms, Bayesian optimization, or topology
optimization work in inverse design. Its advantages and disadvantages are source-specific rather than
the general trade-offs the rubric names. At least one verdict looks too strict: the report lists NOMAD
with its official URL, but the judge marked "Novel materials discovery" as absent because the report
does not spell out the name. That is at most three points and does not change the picture.

This is the first Scout score on a DRB-II case, so there is no earlier score to compare it with. The
missing databases are the kind of material gap that `--follow-up` is meant to find, which makes a
follow-up run on this case a direct test of that mode.

Two follow-up runs were then graded with the same judge. The first, `f157f38f-58b2-4ba2-9e08-fb42e4aac001`,
cost $0.28. Its database scout was lost to an OpenAI token-rate 429, and the gap analysis spent the deep
dive on that question. The deep dive found ICSD, COD, and Materials Cloud. It scored 24 of 52 (0.462),
grade `e4c4247c-be2c-4ced-9bfa-6c09e67d74bf`. After scout pacing (commit bc749a7), the second attempt,
`fbe4997b-e6aa-447b-b697-b6ff0e7971b0`, failed on an unwrapped TLS error from one scout after spending
$0.047; commit 819d03c makes such an error end only its own call. The third, `302403c4-4cf5-4d35-94e6-c263c70c0663`,
cost $0.27 and had no rate-limit errors. All four scouts returned claims, and the gap analysis
chose to confirm NOMAD. It scored 21 of 52 (0.404), grade `6451fddf-6390-4c5d-91ac-5daf17b0d080`.

| Run | Points | ICSD points | Other differences from replicate 4 |
|---|---|---|---|
| Replicate 4, no follow-up | 20 | 0 of 3 | |
| Follow-up 1 | 24 | 3 of 3 | +3 method points; −2 database URLs, −1 presentation |
| Follow-up 3 | 21 | 0 of 3 | +2 NOMAD points; −1 analysis point |

The follow-up gained most when its deep dive recovered a named database that the rubric wanted. Otherwise,
the gain was about the size of the run-to-run variation, which moves method and URL points either way. With
one baseline run and two follow-up runs, this cannot separate the mode's effect from that variation. On
this case the gap analyzer treats the database question as complete once it holds a few computed-data
platforms. It does not look for the experimental and specialist databases the expert report lists. Those
account for 12 of the 21 database points, and no run found them.

The first `drb2-task68-plus` baseline, run `88b6017b-f889-485c-ad21-8347d91c73e6`, completed in 4.8
minutes for $0.23. All four questions returned evidence, 37 of 42 quotes were verified, and pacing
kept it free of rate-limit errors. The same judge gave it 14 of 54 points (0.259), recorded as grade
`8ba2bd7c-bdf2-4ac9-8eed-75db6301e532`. Presentation met 5 of 7, analysis 2 of 9, and information
recall 7 of 38.

The report follows the requested structure, with the reactive and proactive sections and each named
challenge as a heading, but its taxonomy stops short. It covers threshold rules and general machine
learning. It mentions queuing theory and reinforcement learning only in passing, and it omits fuzzy
logic and time-series methods. Twenty-three of the 38 recall points ask for specific named papers,
which suggests the expert report follows one survey closely. Open-web research is unlikely to land on
those exact papers, so this case caps Scout's score lower than drb2-task8 does. The shared pattern is
the one drb2-task8 showed: the research finds some members of the expected set and stops short of the
full set, and the gap analysis cannot see what is missing without a checklist.

## Quote attribution (evidence version 6)

An outside review of the data export found that the quote check did not establish attribution. Evidence versions 5 and earlier marked a quote `verified` when its words appeared anywhere in what a scout's tools returned, and checked separately whether the cited source had been returned, so a quote from one page could vouch for another. `scripts/rescore_quotes.py` re-checks every stored scout and deep-dive call from its recorded messages, with no model call. Over the 117 successful calls stored on 26 September 2026 (1,234 quotes, 1,153 stored as verified), matching each quote only within its cited source's text turned 71 verified quotes (6.2%) into mismatches, the same count the review reported.

Most of those were the same work under another address. Evidence version 6 counts a Wayback Machine copy as the page it archived, an ar5iv rendering as its arXiv paper, a publisher page whose address contains a DOI (Springer, APS, ACM, Wiley) or a Nature article page as that DOI, and a fetched document's first window as the DOI printed in its opening 3,000 characters. With those identities, 35 of the 1,153 (3.0%) remain `misattributed`. Fourteen are PDFs on a publisher's file server that never print their DOI where the check can see it, and are probably the cited paper. The rest are different documents: a preprint quoted as its published article, a paper quoted as a different paper from the same journal, documentation quoted as the paper it describes, and ScienceDirect pages cited by DOI, whose addresses carry an internal ID. A misattributed quote no longer makes a statement `read`, and the run lists it for review. Stored runs keep their version-5 marks; only new runs use the new check.

## Harness

The paid runs of 26 and 27 September 2026 kept finding bugs instead of measuring research, so a harness now looks for that class of bug for free before a study pays (README, "Finding bugs before paying"). While it was being built it found:

- Open-item IDs changed meaning when a deep dive added results under an earlier question, found by `research fuzz` on three seeds and shrunk by a property test to one open item. IDs now come from the item's name (72996e7).
- A database behind on migrations ended every command in a traceback (a dry study).
- The study budget guard priced models itself and refused every call of the harness's models (a dry study).
- The study runner treated an unread grade line as a failed grade, so a parser bug could hide.
- A Wayback copy of a URL ending in `/.` keyed differently from the original, since trailing punctuation was trimmed twice (a property test).

To check that the harness finds real bugs, each earlier bug was reintroduced on a scratch copy and the harness run against it. It caught the grade-line parser (property test and dry study), colliding open-item IDs (fuzz), unstable open-item IDs (fuzz and property test), lone-surrogate PDF text (fuzz), an unexpected scout error failing the run (fuzz), a quote verified against another source's text (fuzz and property test), and the blanket `ValueError` catch together with the parser bug (dry study). Two gaps closed on the way: the surrogate bug was missed until the fuzz model encoded each request as a provider client does and every world held a surrogate PDF, and the unstable-ID bug was missed by one seed batch until fuzzed deep dives named new items under earlier questions. On the final code, 500 fuzz runs and a three-seed dry study of `studies/deep-vs-standard-task8.toml` keep every invariant.

The first cheap check (`research study run studies/deep-vs-standard-task8.toml --cheap`, study `deep-vs-standard-task8-cheap`, 27 September 2026) ran one standard and one deep run on drb2-task8 with every role on `openai:gpt-6-luna@low`: runs `77f51d9e` ($0.03, 2.0 minutes) and `0d6feefb` ($0.05, 5.2 minutes), $0.08 in all with no invariant violations and no tracebacks. Their grades come from the cheap judge and are not comparable with real ones. They did show a design problem: scouts put caveats and whole sentences in `open_items` ("No uncovered categories required by the stated early-2024 scope; ..."), each became a coverage item, and the reports neither addressed nor listed 10 to 13 of them, so every answer read as weak.

## Statements that rest on a summary (evidence version 7)

A review of the scout-v6 smoke runs on 27 September 2026 found that answer support measured whether a statement's source was read, not whether code had checked any of its words. A statement counted as `read` when a scout had fetched its source, even if the evidence was only the scout's own summary, with no quote. Nothing checks that such a summary matches its source. Evidence version 7 calls such statements `paraphrase`. That makes the answer `weak`, lists the statements for review, and adds a count of short quotes, meaning verified quotes under a quarter of their claim's words. The study summary gains a statement column: quoted, summary only, and thin. Quote checks themselves are unchanged. The fetch record of an unreached page now keeps the detail of a bare `ValueError`, so the size cap, an empty extraction, and an unread type are told apart.

The 16 stored reports were re-scored from their ledgers with no model call. Of 208 report statements, 178 rest on a verified quote, 18 only on a summary, 11 on thin evidence, and 1 on none. The deep smoke run `56c4b4dd` holds 11 of the 18 summary-only statements: 11 of its 30 statements, and it has 8 short quotes. Its two method-survey scouts quoted almost nothing (1 of 18 and 0 of 16 evidence items quoted), while its other scouts and deep dives quoted nearly everything. The scout prompt asks for a quote only "when a claim rests on specific wording", so conceptual summaries of reviews go unquoted. No stored run that was `supported` becomes `weak` under version 7, because every scout-v6 run was already weak on coverage. Among the older runs, `f1558521` (st07, Flash high) has 3 summary-only statements of 12.

The fetch failures recorded as `ValueError` in stored scout messages were mostly MDPI pages (118 empty extractions) and PDFs over the 5 MB cap (21 on arXiv and 8 on Nature). MDPI's CDN now refuses the fetcher and a browser user agent alike with a 403, so those pages need another copy of the paper. The size cap could be raised for PDFs.

## Cheap checks of scout-v9 (27 September 2026)

Two study specs check scout-v9: `studies/v9-short-check.toml` (st04 and st05, two runs each) and `studies/v9-st07-check.toml` (one diagnostic run of st07). Both ran `--dry` with no invariant violations, over three seeds for the first, and then `--cheap`, with every role on `openai:gpt-6-luna@low`. The cheap judge's grades are not comparable with real ones.

| Study | Run | Status, answer | Quotes verified / misattributed / not found | Statements quoted / summary only / thin | Cost with grade |
|---|---|---|---|---|---|
| v9-short-check-cheap (st04) | `3f5ed912` | complete, weak | 1 / 0 / 1 | 1 / 0 / 1 | $0.0062 |
| v9-st07-check-cheap (st07) | `f7c7e94e` | complete, supported | 14 / 0 / 0 | 6 / 0 / 0 | $0.0115 |

Together they cost $0.018 and showed no invariant violations. OpenAI's two SWE-bench Verified pages refused the fetcher with 403. 
The paid runs followed on the same day, with the default models and grading by the version-2 judge. They cost $0.55 in total, grades included, with no tracebacks or invariant violations.

| Case | Run | Status, answer | Rubric | Quotes verified / misattributed / not found (short) | Statements quoted / summary only / thin | Cost | Time | scout-v1 Luna for comparison |
|---|---|---|---|---|---|---|---|---|
| st04 r1 | `f94412fc` | complete, supported | 4/4 | 2 / 0 / 0 | 2 / 0 / 0 | $0.045 | 65 s | 4/4, $0.029, 56 s |
| st04 r2 | `804ab265` | complete, weak | 4/4 | 3 / 0 / 0 | 2 / 0 / 0 | $0.050 | 70 s | |
| st05 r1 | `72131d69` | complete, supported | 11/11 | 11 / 0 / 0 | 9 / 0 / 0 | $0.109 | 254 s | 11/11, $0.078, 80 s |
| st05 r2 | `61cc0645` | complete, supported | 10/11 | 14 / 0 / 1 | 9 / 0 / 0 | $0.121 | 191 s | |
| st07 r1 | `7a7fc5b5` | partial, weak | 2/9 | 31 / 0 / 3 (1) | 12 / 0 / 0 | $0.218 | 349 s | 2/9 partial, $0.154, 130 s |

The short cases did not regress on their rubrics. The one lost point is st05's Chinchilla item. The rubric wants the report to say that the paper gives no context length; the report said this could not be established, because the scout's quote for the absence was not found in the source. That is a cautious answer, not a wrong one. st04 replicate 2 is `weak` only because the planner listed three video subtasks as coverage items that research did not establish.

No report statement in the five runs rests on a summary alone: all 34 carry a verified quote, against 11 of 30 summary-only statements in the v6 deep run. That is what the scout-v9 prompt asked for. Whether each quote supports its claim is still unchecked.

The short cases now cost 50 to 70 percent more and take longer than under scout-v1: st05 took 191 to 254 seconds against 80. That follows from the scout-v3 budgets rather than from quoting, but it is a real cost to quick questions.

st07 lost the question that decides it. The q2 scout, which researched contamination and flawed tests, read 8 pages in 6 requests, then its next model request failed with `SSLError: SSLV3_ALERT_BAD_RECORD_MAC`, a transient TLS fault. A network error ends a call without a retry, so the question returned nothing, and the report again omits the contamination evidence and the maintainer review, as scout-v1 Luna's did after a 429. Seven DuckDuckGo searches also timed out. The grade therefore measures that loss, not scout-v9.

## Support audit of every stored report (audit version 1, 27 September 2026)

`research audit` (commit a7fdda7) asked `zai:glm-5.3` at high effort, a vendor no run uses, whether the verified quotes behind each report statement say what the statement says. All 23 stored reports were audited in one batch under a $2.00 cap, and the batch cost $0.26. The verdicts are in the `support_audits` table.

| Runs | Statements | Supported | Partial | Unsupported | No verified quote |
|---|---|---|---|---|---|
| scout-v1, production models (12 reports) | 132 | 66 | 53 | 4 | 9 |
| scout-v6, production models (2) | 60 | 29 | 18 | 0 | 13 |
| scout-v9, production models (5) | 34 | 29 | 5 | 0 | 0 |
| cheap checks, Luna low (4) | 24 | 10 | 12 | 1 | 1 |

A hand check of all 5 `unsupported` verdicts and a random 12 of the 76 `partial` verdicts in production runs found the following:

- **Unsupported verdicts.** All 5 are right. Four are statements that go beyond their quotes; the fifth is a report describing its own scope.
- **Partial verdicts, real overreach (6 of 12).** The statement adds something the quotes do not state: that the Chinchilla paper gives no context length, that OpenHands with Claude 3.7 Sonnet was the system behind a figure, "since 2022", two challenges missing from the quote, that the dataset is static, and that saturation is not established.
- **Partial verdicts, audit artefacts (4 of 12).** They come from sending only each source's title: author names, dates, and URLs that came from a source's record look unsupported.
- **Partial verdicts, other (2 of 12).** One statement had already said its figure rested on an unverified quote, and one is minor framing.

Overreach enters at both steps. Usually the scout's claim already says more than its quote, drawing on the rest of a page it read or on its own inference; claims that something is absent from a source, such as Chinchilla's context length, are a recurring case that a quote can never establish. Once, the synthesizer added a specific system and model name that neither the claim nor the quote contains.

Taking the sample's rate, roughly one in five quoted statements in production runs says more than its quotes. The scout-v9 reports did best (29 of 34 supported), but they are the three easiest cases, so this is not yet a comparison of versions. Audit version 2 should send each source's URL, date, and authors, so that record details stop counting against a statement.

## A second rubric judge (27 September 2026)

Every report with a `gpt-6-sol` high-effort grade (18 reports) was graded again by `zai:glm-5.3` at high effort, with the same version-2 judge prompt (`RESEARCH_MODELS__JUDGE=zai:glm-5.3@high research grade`, $0.30 cap per grade). The 18 grades cost $0.27, and all of them are in `grades` under their judge model.

| Case | Reports | Sol points | GLM points | Points where the judges disagree |
|---|---|---|---|---|
| st04 | 5 | 19/20 | 19/20 | 0 |
| st05 | 4 | 43/44 | 43/44 | 0 |
| st07 | 3 | 10/27 | 13/27 | 3 |
| drb2-task8 | 5 | 118/260 | 131/260 | 15 |
| drb2-task68-plus | 1 | 14/54 | 22/54 | 10 |

The judges agree on 375 of 405 points (92.6%). Of the 30 disagreements, GLM credits 27 points that Sol does not, and Sol credits 3 that GLM does not. On the short cases they agree exactly. On the DRB-II cases GLM scores each report 1 to 8 points higher. Both judges rank the drb2-task8 reports almost identically: the deep run `56c4b4dd` is first under both, and only two reports two points apart swap places.

A hand check of the three st07 disagreements on `7a7fc5b5` favours GLM on two. The rubric point "human annotators screened instances for underspecified issues and unfair or overly specific tests" is stated in the report in other words ("a clear problem statement, a correct test patch, and solvability"). The point "documents contamination or memorization concerns" is met by a quoted SWE-rebench finding that scores "might be inflated due to contamination issues". This matches the earlier NOMAD verdict: Sol does not always credit equivalent wording.

The choice of judge therefore moves a DRB-II score by up to 8 points, as much as the run-to-run variation the studies try to see past, while leaving short cases and the order of reports mostly unchanged. A decision that rests on a few rubric points should use both judges and have their disagreements checked by hand.

## Exa search: dry and cheap checks (27 September 2026)

`RESEARCH_SEARCH_ENGINE=exa` (commit 05d5c69) was checked with `studies/exa-search-check.toml`, which has a DuckDuckGo arm and an Exa arm on st04. The dry check passed, as did a three-seed dry study of both short cases with the Exa arm. The cheap check (every role on `gpt-6-luna@low`) cost $0.02, with no invariant violations:

| Arm | Run | Status, answer | Searches | Search cost | Run cost | Time | Cheap judge |
|---|---|---|---|---|---|---|---|
| DuckDuckGo | `37a0a63d` | complete, weak | 6 (3 cached as results) | free | $0.006 | 45 s | 3/4 |
| Exa | `3aa8dde9` | complete, supported | 2 | $0.014 | $0.017 | 20 s | 4/4 |

Exa charged the listed $0.007 a search, and the run recorded exactly that as `search_usd`. Its first search returned ten results, the first of them the official ILSVRC 2016 results page with a highlight from its results table. On this cheap run the searches cost more than the models; with the production models they would be a smaller share. One run per arm on the easiest case says nothing yet about quality. A paid comparison needs more cases, replicates, and a decision rule set before it runs.

## Exa against DuckDuckGo: dry and cheap checks of the comparison specs (27 September 2026)

The comparison is split into three specs (`studies/exa-vs-duckduckgo-{short,st07,task8}.toml`), so each can have a ceiling fitted to its runs, and their shared header fixes the decision rule. All three passed `--dry --seeds 2`. The cheap checks, with every role on `gpt-6-luna@low`, cost $0.39 in all, with no invariant violations. A cheap run's hard cap is now the $0.25 cheap ceiling (commit after 7dd3ad1), because paid searches are not cheap; the task8 Exa run reached it and had one question refused, as designed.

| Case | Arm | Run | Status, answer | Search cost | Run cost | Time |
|---|---|---|---|---|---|---|
| st04 | DuckDuckGo | `e47e39a3` | complete, supported | | $0.002 | 17 s |
| st04 | Exa | `ee0c71d8` | complete, supported | $0.007 | $0.010 | 16 s |
| st07 | DuckDuckGo | `43a40c62` | complete, supported | | $0.014 | 56 s |
| st07 | Exa | `6a16f441` | complete, supported | $0.077 | $0.115 | 277 s |
| drb2-task8 (deep) | DuckDuckGo | `bbe0b73a` | complete, weak | | $0.045 | 303 s |
| drb2-task8 (deep) | Exa | `6b504ddd` | partial, weak (cheap cap) | $0.154 | $0.198 | 335 s |

Exa's own requests are quick: 8 to 15 seconds of tool time per scout, as with DuckDuckGo. Its highlights are not snippets, though. The median result carried 3,900 to 6,400 characters against DuckDuckGo's 210 to 230, and one search returned 49,000 to 61,000 characters against about 2,500. Every later request of a scout resends them, so the Exa scouts on st07 sent 119,000 to 336,000 input tokens each against 44,000 to 63,000. That made them wait under Luna's token rate limit and cost about 2.7 times as much in model calls. Exa's text also arrives labeled `snippet`, so evidence resting on it counts as shallow unless the scout fetches the page. A paid comparison with uncapped highlights would mostly measure this context growth.

## Fetch bake-off, free candidates (27 September 2026)

`scripts/fetch_bakeoff.py` retried the 160 distinct URLs our fetcher failed on in production runs, with no model calls. The free candidates together read 83 of the 160. Jina Reader, used without a key, read 52, including MDPI 20 of 26 and OpenAI 5 of 5, but hit a challenge page on all 26 RSC pages. The open-access route, through OpenAlex and Europe PMC, read 38 and recovered the most cited quotes (39 of 79). Our own fetcher now reads 19, all 11 arXiv PDFs among them, since the 25 MB cap. RSC, ScienceDirect, OQMD, De Gruyter, and Materials Project stay mostly unread. The paid candidates are still to run: Exa `/contents` (about $0.16), and Tavily and Firecrawl, which need keys.
