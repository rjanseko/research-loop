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
