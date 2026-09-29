# Candidate questions and rubrics for end-to-end report evaluation

**Status:** draft development set, 29 September 2026. No case below has been frozen, run, graded, or added to `study_cases.jsonl`. The existing frozen cases and held-out DeepResearch Bench II cases are unchanged. Review the reference packets and source accessibility before freezing a v1 set. The common evidence cutoff is **31 December 2024**: reports should not use later findings to answer these historical questions.

## Why these five

The set gives Scout five distinct jobs that a reader might actually ask it to do. The two short cases test whether a simple answer stays simple and accurate; the three report cases test a comparison, an organizational decision, and a contested causal synthesis. Their rubrics are intentionally shorter than expert-report checklists. Each point expresses one observable claim or reasoning step. A report can earn a coverage point while still fail the independent source-support check.

| ID | Reader task | Suggested depth | Main failure to catch |
|---|---|---|---|
| `ex-dart-period` | Update a dated numerical result | Shallow or medium | Repeating an early estimate as final, or confusing the two orbits |
| `ex-voyager-boundary` | Correct a false premise | Shallow | Equating the heliopause with the outer edge of the Solar System |
| `ex-grid-storage` | Compare options at two durations | Medium or deep | Mixing incompatible cost bases or recommending a universal winner |
| `ex-hybrid-policy` | Make a bounded policy recommendation | Medium or deep | Turning one trial into a universal productivity claim |
| `ex-heat-2023` | Explain a contested multi-cause event | Deep | Assigning precise or exclusive causes without comparable evidence |

The question in each section is the complete proposed **model-visible objective**. The reader, reference facts, rubrics, and defect checks remain evaluation-side data. Do not append them to the run prompt.

## 1. DART: a result that changed with later measurement

**Question:** “NASA first reported that DART shortened Dimorphos's orbit by about 32 minutes. Using evidence available through 2024, what did later observations show, and why did the reported change grow?”

**Reader and output:** A science editor wants a short correction of a dated result. `short_answer` is appropriate; a paragraph plus citations should suffice. Evidence cutoff: 31 December 2024.

**Reference packet:** NASA's [October 2022 announcement](https://www.nasa.gov/news-release/nasa-confirms-dart-mission-impact-changed-asteroids-motion-in-space/) gave an early change of about 32 minutes. The [March 2024 JPL summary of the orbital study](https://www.jpl.nasa.gov/news/nasa-study-asteroids-orbit-shape-changed-after-dart-impact/) identifies Dimorphos's orbit around Didymos, an immediate post-impact change of 32 min 42 sec, then an additional shortening over weeks as rocky material escaped. It reports a settled change of **33 min 15 sec**, to an **11 h 22 min 03 sec** period. The early and revised numbers describe observations and analysis at different times, not a failed deflection.

**Binary coverage rubric (`facts`, five points):**

1. Identifies the measured quantity as Dimorphos's orbital period **around Didymos**.
2. Identifies approximately 32 minutes as NASA's initial reported shortening, rather than the final value.
3. Gives the later settled shortening as approximately 33 min 15 sec (allow rounding to about 33 minutes).
4. Separates the immediate modeled change of 32 min 42 sec from the further shortening over subsequent weeks.
5. Explains that continuing loss of rocky material was part of the reported reason for the further orbital evolution.

**Critical checks:** Do not credit a claim that DART changed Dimorphos's period *around the Sun* or that 33 min 15 sec was the period itself. Check the numerical claim at the cited passage, not just at a NASA home page. A concise answer may omit the final absolute period without losing a point.

## 2. Voyager 1: a false premise about boundaries

**Question:** “As of the end of 2024, did Voyager 1 leave the Solar System when it entered interstellar space in 2012? Explain the boundary it crossed and the boundary it has not.”

**Reader and output:** A museum editor needs a precise correction. `short_answer`; one or two paragraphs. Evidence cutoff: 31 December 2024.

**Reference packet:** NASA JPL's [explanation of the crossing](https://www.jpl.nasa.gov/news/how-do-we-know-when-voyager-reaches-interstellar-space/) retrospectively dates entry into interstellar plasma to **25 August 2012** and names the **heliopause** as the boundary of the heliosphere. It also explains that, under the common gravitational/Oort Cloud definition of the Solar System, Voyager had **not** left the Solar System. The same source gives rough times of about 300 years to the inner Oort Cloud and perhaps 30,000 years beyond it; these are context, not required precision. NASA's [Oort Cloud overview](https://science.nasa.gov/solar-system/oort-cloud/facts/) independently explains its Solar System relationship.

**Binary coverage rubric (`facts`, four points):**

1. Answers “No” to the question as framed, while acknowledging that “Solar System” can have more than one informal meaning.
2. States that Voyager 1 entered interstellar space in 2012 by crossing the heliopause, the heliosphere's outer boundary.
3. Explains that crossing the heliopause does not mean passing the outer Oort Cloud or the Sun's gravitational realm.
4. Says Voyager 1 had not yet traversed the Oort Cloud as of the cutoff.

**Critical checks:** Treat “left the Solar System in 2012” as a decisive error unless the answer immediately defines the term as the heliosphere and explains the broader boundary. Do not require the 300/30,000-year figures: they are projections and add little to the correction.

## 3. Grid storage: a decision with incompatible cost bases

**Question:** “How should a US utility choose between lithium iron phosphate and vanadium redox-flow batteries for a 100 MW project needing either four or ten hours of storage? Compare cost and performance using evidence available through 2024, and explain which assumptions could change the choice.”

**Reader and output:** A utility planning team needs a decision memo, not a procurement quote. `report`. Evaluate the four-hour and ten-hour cases separately. Evidence cutoff: 31 December 2024.

**Reference packet:** The US DOE's [2022 Grid Energy Storage Technology Cost and Performance Assessment](https://www.energy.gov/sites/default/files/2022-09/2022%20Grid%20Energy%20Storage%20Technology%20Cost%20and%20Performance%20Assessment.pdf), executive summary pp. 7–8, models **fully installed 100 MW** systems using **projected 2021 costs**. For **10 hours**, it gives LFP **$356/kWh** and vanadium redox flow **$385/kWh**. For **4 hours**, it explicitly gives LFP **$385/kWh**; do not infer a flow-battery value from the 10-hour table. DOE's technology chapters discuss efficiency, lifetime, and duration-specific costs. The [2024 NREL utility-scale battery ATB](https://atb.nrel.gov/electricity/2024/2023/utility-scale_battery_storage) can help assess lithium-ion assumptions, but is a different model and must not be read as a same-basis vanadium bid. PNNL's [vanadium-flow project page](https://www.pnnl.gov/projects/esgc-cost-performance/vanadium-redox-flow-battery) explains the separately scalable power and energy components. Site prices and technology performance remain uncertain; the cited modeled values are benchmarks, not a present-day offer.

**Binary coverage rubric (`decision`, twelve points):**

1. Converts 100 MW × 4 h to **400 MWh** of nominal energy capacity.
2. Converts 100 MW × 10 h to **1,000 MWh** of nominal energy capacity.
3. States the DOE 10-hour LFP cost as about **$356/kWh**, identifying it as modeled installed cost for that system and base year.
4. States the DOE 10-hour vanadium-flow cost as about **$385/kWh**, on the same DOE basis.
5. States the DOE four-hour LFP value as about **$385/kWh**, without presenting it as a flow-battery figure.
6. Explains the physical design distinction: flow-battery energy capacity and power equipment can be scaled more independently than a conventional LFP battery system.
7. Compares round-trip efficiency or delivered-energy implications for LFP and vanadium flow, anchored to a source.
8. Compares degradation, replacement, or expected cycle-life implications for the two technologies, anchored to a source.
9. Gives a conditional four-hour choice and names the assumption most likely to reverse it.
10. Gives a conditional ten-hour choice and names the assumption most likely to reverse it.
11. Calls for both options to be compared on one base year and one project-level cost basis.
12. Includes duty cycle, replacement/augmentation, financing, or site-specific costs among the inputs needed before procurement.

**Critical checks:** Flag a made-up four-hour vanadium number, a comparison of $/kW with $/kWh without conversion, or 2021 DOE estimates described as 2024 bids. Do not grant “lowest cost” solely from installed $/kWh: lifetime value depends on throughput, efficiency, financing, and replacement. Any reasonable recommendation can pass if it preserves those conditions; the benchmark is not a prescribed winner.

## 4. Hybrid work: external validity and a measurable pilot

**Question:** “As of 31 December 2024, what is the best evidence for and against a midsize software company offering two work-from-home days per week? Assess retention, measured output, and mentoring, and recommend what the company should track during a six-month pilot.”

**Reader and output:** A management team deciding whether to trial a policy. `report`; lead with a recommendation, then evidence and pilot measures. Evidence cutoff: 31 December 2024.

**Reference packet:** The [2024 Bloom, Han and Liang randomized trial](https://www.nature.com/articles/s41586-024-07500-2) assigned **1,612** employees at Trip.com to a two-day hybrid option or five office days. Quit rates were **4.8% versus 7.2%** during the experiment, a one-third relative reduction; measured reviews, promotions, and engineer lines of code showed no material difference in the reported analyses. This is evidence about this intervention and population, not proof of a productivity increase everywhere. The [Pallais and coauthors' proximity study](https://pallais.scholars.harvard.edu/publications/power-proximity-coworkers-training-tomorrow-or-productivity-today) reports more online feedback for engineers with co-located teammates and a training/short-term-output trade-off, especially relevant to junior workers. Trip.com excluded interns and probationary “rookies” partly over mentoring concerns. The [New York Fed call-center study](https://www.newyorkfed.org/research/staff_reports/sr1061) concerns fully remote workers and a different kind of work; it is a caution about heterogeneity, not an estimate of a two-day software hybrid policy.

**Binary coverage rubric (`decision`, eleven points):**

1. Gives a direct, conditional recommendation on whether and how to pilot two home days.
2. Identifies the Trip.com result as a randomized test of two home days versus full office attendance, rather than of fully remote work.
3. Reports the retention finding accurately: 4.8% versus 7.2% quits, or an approximately one-third **relative** reduction.
4. Describes Trip.com's measured performance findings as no material detected reduction, without claiming a proven productivity gain.
5. Notes a material limit on transfer to this company: geography, company, eligible roles, or exclusion of interns/probationary workers.
6. Presents credible evidence or a reasoned mechanism for a mentoring/onboarding risk, especially for junior staff, with the supporting population identified.
7. Keeps evidence on fully remote work or call-center work distinct from the two-day software policy.
8. Specifies a predeclared retention measure for the pilot.
9. Specifies an output measure and a quality guardrail, with a comparison group or baseline.
10. Specifies a mentoring or onboarding measure for junior staff.
11. Sets a review point or decision rule for revising the policy.

**Critical checks:** Flag “hybrid raised productivity by 33%” (the 33% is the relative quit-rate reduction), a blanket “no mentoring cost,” or a fully remote study presented as the trial of this policy. Judge the recommendation for calibration and usefulness, not for matching a favored workplace policy.

## 5. Exceptional 2023 warmth: multi-cause, uncertain attribution

**Question:** “What made 2023 exceptionally warm worldwide? Using research available through 2024, distinguish long-term greenhouse warming, El Niño and ocean variability, aerosol changes, and the Hunga Tonga eruption; identify which contributions remain uncertain.”

**Reader and output:** A general science reader who wants a causal explanation without false numerical precision. `report`. Evidence cutoff: 31 December 2024.

**Reference packet:** NASA's [2024 review of five factors](https://science.nasa.gov/earth/earth-observatory/five-factors-to-explain-the-record-heat-in-2023-152313/) identifies long-term human-caused greenhouse warming as the background, El Niño and ocean variability as contributors, potential regional and smaller global effects from reduced shipping aerosols, and no substantial global contribution from Hunga Tonga in its assessment. NOAA's [State of the Climate in 2023 record](https://repository.library.noaa.gov/view/noaa/67834) supplies observational context and the La Niña to El Niño transition. A [2024 analysis by Samset and colleagues](https://www.nature.com/articles/s43247-024-01637-8) found the exceptional warmth consistent with previously observed sea-surface-temperature influences, regional forcing, and steady anthropogenic warming. It does not establish a universal numerical partition. The packet should preserve disagreement where attribution methods and time windows differ.

**Binary coverage rubric (`synthesis`, eight points):**

1. States that 2023 set a global temperature record in the observational series discussed, with the dataset or baseline identified when giving a numerical anomaly.
2. Identifies accumulated anthropogenic greenhouse warming as the long-term background for the high global level.
3. Explains that the developing El Niño and broader ocean variability contributed to 2023's short-term warmth, rather than causing the underlying trend.
4. Distinguishes the timing of the 2023 El Niño from the whole-year global anomaly; does not assign every warm month to it.
5. Describes reduced aerosols, including shipping-related changes, as a possible contribution whose global size was still uncertain in the cited literature.
6. Reports that NASA's reviewed assessment did **not** find Hunga Tonga a substantial contributor to the global 2023 record, while explaining why its water-vapor injection attracted study.
7. States an explicit unresolved attribution question or uncertainty about the record's excess warmth, tied to a source or methodological limit.
8. Avoids an exact additive percentage breakdown unless the cited estimates share the same baseline, variable, time window, and attribution method.

**Critical checks:** Flag “El Niño alone caused the record,” Hunga Tonga as the dominant cause without strong comparative evidence, or a sum of estimates from incompatible studies presented as a measured partition. The rubric deliberately rewards an honest residual rather than forcing a complete causal decomposition.

## How to evaluate these cases

**Coverage:** The current `StudyCase.rubrics` and `research grade` judge can represent the numbered binary points. Preserve category and point order when transcribing them. A met point means the report states it consistently; it does **not** certify that the cited passage supports it. The historical judge reads `answer`, key statements, caveats and source names, but omits the executive summary and does not see source passages. Do not use its percentage alone as the acceptance score for these examples.

**Truth and citation support:** For each case, independently verify the direct answer plus every decision-changing numerical or causal claim against the exact cited passage. Record `supported`, `contradicted`, or `unresolved`, including the passage, correction, and whether the error changes the reader's decision. The listed reference sources are starting points, not mandatory citations: an equivalent reliable original source can support a claim. Verify the report's actual source, not only the packet. A critical contradiction or fabricated citation blocks a quality win even when coverage is high.

**Reader quality:** Score direct answer, coverage/synthesis, evidence use, calibration, and reader utility separately on the [0–3 anchors in the evaluation guide](evaluation.md#three-views-of-a-run). For the short cases, also note whether the answer reached the point without needless research or prose. For a comparison, use blind paired preference on the same case with `A`, `B`, `tie`, or `neither`, plus a passage-based reason. Keep cost, elapsed time, completion, and failed/partial outcomes visible apart from quality.

**Do not pool unlike cases:** Show results for each ID and then within short-answer, decision, and synthesis groups. Five easy coverage scores cannot outweigh a critical error in a decision memo. A single run per arm is only a screen; any default-changing study needs the decision rule and sufficient repeats fixed before paid execution.

## Freeze checklist before using them in a study

1. Have a human check every packet statement against the linked original passage and record a dated source snapshot, title, publication date, URL/DOI, and passage location. In particular, confirm that each source can be fetched by Scout or record an accessible equivalent; a browser-visible page alone is not a guarantee.
2. Resolve whether each question's length and depth fit the configured run cap. Keep the natural objective unchanged, and put reader context and the 2024 cutoff in case metadata or an explicitly supported study context field. If the runner cannot enforce the cutoff without adding text to the objective, revise and version the objective **before** freezing.
3. Convert each candidate into a new `StudyCase` ID with `rubric_version` 1; check for duplicate or compound points and mark the source packet version separately. Do not edit any previously graded case. Keep packet answers invisible to the research run.
4. Pilot the grading on a human-written strong answer, a plausible but wrong answer, and a report with the decisive claim only in its summary. Adjudicate judge disagreements and decide whether a reader-visible judge version is needed before interpreting scores.
5. Run the exact study spec with `--dry` and `--cheap` and no invariant violations. Treat a paid first pass as a screen. Before any larger paid comparison, set the decision rule, estimate from comparable costs, cap spending, and follow the repository's paid-run approval and study-log rules.

This is a **candidate development set**, not a held-out confirmation set. Once Scout outputs have been used to tune it, those cases cannot later serve as independent confirmation. Keep the existing held-out cases sealed for that purpose, or freeze a separately selected new holdout before viewing model outputs.
