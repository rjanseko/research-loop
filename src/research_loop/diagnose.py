"""Where a run's rubric points were lost, and whether a score can show a change: a post-run step.

A rubric grade (evals.py) says how many points a report met, not why it missed the others. The same judge
also grades two earlier views of a stored run:
- `claims`: the research's claim statements, each with the titles and addresses of its sources;
- `research`: everything the synthesizer was shown (EvidenceLedger.prompt_view), including quotes,
  excerpts, conclusions, open items, and what the scouts could not establish.
A point the report missed takes the earliest stage that met it (`STAGES`). A fixed-plan rescout writes no
report, so only its claims and research are graded, and its points start at "claimed": this compares scout
models on one plan without a synthesizer's variation. In the audit of drb2-task8 on
28 September 2026, most missed points had reached the synthesizer only as quote text or open items, which
no claim stated, and about six were never found at all.

The free checks read only stored grades:
- how often two judges disagree about one report, and on which points;
- URL points a report missed only by giving an equivalent address, such as oqmd.org for www.oqmd.org;
- points no run of a case has met in any view, which may be out of reach until a person confirms them;
- the smallest difference between two arms that their replicates can detect.

Like the support audit (audit.py), this is an evaluation step outside the run: it reads what runs stored
and writes only its own grades.
"""
from __future__ import annotations

import json
import math
import re
import statistics
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

from .evals import GradeRecord, StudyCase, grade_row
from .evidence import EvidenceLedger

# 1: the claims and research views, and stages in the order reported, claimed, seen, not found.
DIAGNOSE_VERSION = 1

Point = tuple[str, int]
VIEWS = ("claims", "research")
STAGES = ("reported", "claimed", "seen", "not_found")
STAGE_NAMES = {"reported": "Reported", "claimed": "Claimed, not reported", "seen": "Seen, not claimed",
               "not_found": "Not found"}
# A normal approximation for two arms at 80% power and a two-sided 5% test: 1.96 + 0.84.
_DETECT_FACTOR = 2.8
_URL = re.compile(r"https?://[^\s)\]>|`'\"]+")


def claims_text(ledger: EvidenceLedger) -> str:
    """Every claim statement, by research question, with the titles and addresses of its sources."""
    sources = {row["id"]: row for row in ledger.source_table()}
    by_claim = ledger.claim_source_ids()
    lines: list[str] = []
    for result in ledger.all():
        if not result.claims:
            continue
        lines.append(f"## {result.question_id}: {result.question}")
        for claim in result.claims:
            cited = [sources[source_id] for source_id in sorted(by_claim.get(claim.id, ())) if source_id in sources]
            refs = "; ".join(" ".join(part for part in (row.get("title") or "", row.get("url") or "") if part)
                             for row in cited)
            lines.append(f"- {claim.statement}" + (f" (sources: {refs})" if refs else ""))
    return "\n".join(lines)


def research_text(ledger: EvidenceLedger) -> str:
    """Everything the synthesizer was shown of the research."""
    return json.dumps(ledger.prompt_view(), ensure_ascii=False, indent=1)


VIEW_TEXT = {"claims": claims_text, "research": research_text}


def stage_grade_row(grade: GradeRecord, view: str) -> dict[str, Any]:
    """The `stage_grades` row for a judge call on one view of a run."""
    return grade_row(grade) | {"view": view, "diagnose_version": DIAGNOSE_VERSION}


def verdicts(points: Iterable[dict[str, Any]]) -> dict[Point, bool]:
    return {(p["category"], int(p["point"])): bool(p["met"]) for p in points}


@dataclass
class RunDiagnosis:
    """One run's verdicts under one judge: on its report, and on its claims and research when graded. A
    rescout has no report (`report` None)."""

    run_id: UUID
    case: StudyCase
    report_text: str
    report: dict[Point, bool] | None
    claims: dict[Point, bool] | None = None
    research: dict[Point, bool] | None = None
    arm: str | None = None

    def stages(self) -> dict[Point, str] | None:
        """Each point's stage: reported, or the earliest earlier view that met it, or not found."""
        if self.claims is None or self.research is None:
            return None
        report = self.report if self.report is not None else dict.fromkeys(self.claims, False)
        return {point: "reported" if met else "claimed" if self.claims.get(point)
                else "seen" if self.research.get(point) else "not_found"
                for point, met in report.items()}

    def score(self) -> int | None:
        """Points met by what the arms are compared on: the report, or a rescout's claims."""
        met = self.report if self.report is not None else self.claims
        return None if met is None else sum(met.values())

    def beyond_research(self) -> list[Point]:
        """Points the report met that neither its claims nor its research did: judge noise, or the synthesizer
        writing from its own knowledge."""
        if self.claims is None or self.research is None or self.report is None:
            return []
        return [point for point, met in self.report.items()
                if met and not self.claims.get(point) and not self.research.get(point)]

    def equivalent_urls(self) -> list[Point]:
        return [] if self.report is None else equivalent_urls(self.case, self.report_text, self.report)


def _host(url: str) -> str:
    return (urlsplit(url).hostname or "").removeprefix("www.")


def _same_site(a: str, b: str) -> bool:
    first, second = _host(a), _host(b)
    return bool(first and second) and (first == second or first.endswith("." + second) or second.endswith("." + first))


def equivalent_urls(case: StudyCase, report_text: str, report: dict[Point, bool]) -> list[Point]:
    """Missed points that ask for a URL the report gives on the same site, such as https://oqmd.org/ for
    https://www.oqmd.org/, or materialsproject.org for next-gen.materialsproject.org. Probably equivalent;
    a person confirms."""
    given = _URL.findall(report_text)
    found = []
    for (category, number), met in report.items():
        if met:
            continue
        wanted = _URL.findall(case.rubrics[category][number - 1])
        if wanted and any(_same_site(want, url) for want in wanted for url in given):
            found.append((category, number))
    return found


@dataclass
class JudgeAgreement:
    """Two judges' report grades of the same runs, compared point by point."""

    judges: tuple[str, str]
    compared: int = 0
    credited_by: Counter[str] = field(default_factory=Counter)
    points: Counter[Point] = field(default_factory=Counter)

    @property
    def differ(self) -> int:
        return sum(self.credited_by.values())


def judge_agreement(grades: Sequence[dict[str, Any]]) -> JudgeAgreement | None:
    """How the two judges with the most report grades of the same runs compare; None without two."""
    by_run: dict[Any, dict[str, dict[Point, bool]]] = defaultdict(dict)
    for row in grades:
        if row["status"] == "succeeded" and row.get("points"):
            by_run[row["run_id"]][_judge_name(row)] = verdicts(row["points"])
    pairs = Counter(tuple(sorted(judges)[:2]) for judges in by_run.values() if len(judges) >= 2)
    if not pairs:
        return None
    first, second = pairs.most_common(1)[0][0]
    result = JudgeAgreement((first, second))
    for judges in by_run.values():
        if first in judges and second in judges:
            for point, met in judges[first].items():
                result.compared += 1
                if met != judges[second].get(point, met):
                    result.credited_by[first if met else second] += 1
                    result.points[point] += 1
    return result


def _judge_name(row: dict[str, Any]) -> str:
    return f"{row['judge_model']}@{row['judge_thinking']}" if row.get("judge_thinking") else row["judge_model"]


def never_met(case: StudyCase, history: Iterable[Iterable[dict[str, Any]]]) -> list[Point]:
    """The rubric points no stored grade of any view has met: possibly out of reach, until a person checks."""
    met = {point for points in history for point, ok in verdicts(points).items() if ok}
    return [(category, number) for category, texts in case.rubrics.items()
            for number in range(1, len(texts) + 1) if (category, number) not in met]


def detectable_difference(first: Sequence[float], second: Sequence[float]) -> float | None:
    """The smallest difference in mean score two arms' replicates can detect at 80% power; None below two
    replicates an arm."""
    if len(first) < 2 or len(second) < 2:
        return None
    pooled = ((len(first) - 1) * statistics.variance(first) + (len(second) - 1) * statistics.variance(second)) \
        / (len(first) + len(second) - 2)
    return _DETECT_FACTOR * math.sqrt(pooled) * math.sqrt(1 / len(first) + 1 / len(second))


def _label(case: StudyCase, point: Point, width: int = 110) -> str:
    category, number = point
    text = case.rubrics[category][number - 1]
    return f"{category} {number}: {text if len(text) <= width else text[: width - 3].rstrip() + '...'}"


def render(diagnoses: Sequence[RunDiagnosis], judge: str, grades: Sequence[dict[str, Any]],
           history: dict[str, list[list[dict[str, Any]]]]) -> str:
    """The diagnosis as Markdown: where points were lost, run by run and arm by arm, and whether the score
    can show a change. `grades` are the runs' stored report grades by every judge; `history` holds every
    stored grade of each case, report and stage alike."""
    out: list[str] = []
    for case_id in dict.fromkeys(d.case.id for d in diagnoses):
        runs = [d for d in diagnoses if d.case.id == case_id]
        case = runs[0].case
        total = sum(len(items) for items in case.rubrics.values())
        out += [f"## {case_id}: where the points were lost ({judge}, diagnose v{DIAGNOSE_VERSION})", "",
                "| Run | Arm | " + " | ".join(STAGE_NAMES[s] for s in STAGES) + " | Reported beyond research "
                "| Missed, equivalent URL |",
                "|---|---|" + "---|" * (len(STAGES) + 2)]
        counts: dict[Any, Counter[str]] = {}
        for d in runs:
            stages = d.stages()
            if stages is None:
                reported = sum(d.report.values()) if d.report is not None else "no report"
                out.append(f"| {str(d.run_id)[:8]} | {d.arm or ''} | {reported} | not graded | not graded | "
                           f"not graded | | {'' if d.report is None else len(d.equivalent_urls())} |")
                continue
            counts[d.run_id] = Counter(stages.values())
            out.append(f"| {str(d.run_id)[:8]} | {d.arm or ''} | "
                       + " | ".join("no report" if s == "reported" and d.report is None else str(counts[d.run_id][s])
                                    for s in STAGES)
                       + (" | | |" if d.report is None else f" | {len(d.beyond_research())} | {len(d.equivalent_urls())} |"))
        for arm in dict.fromkeys(d.arm for d in runs if d.run_id in counts):
            members = [counts[d.run_id] for d in runs if d.arm == arm and d.run_id in counts]
            reportless = all(d.report is None for d in runs if d.arm == arm)
            if len(members) > 1:
                out.append(f"| mean of {len(members)} | {arm or ''} | "
                           + " | ".join("no report" if s == "reported" and reportless
                                        else f"{statistics.mean(c[s] for c in members):.1f}" for s in STAGES) + " | | |")
        out += ["", f"Each run's {total} points: a point the report missed is put at the earliest stage that met it."]
        lost: dict[str, Counter[Point]] = {stage: Counter() for stage in STAGES[1:]}
        for d in runs:
            for point, stage in (d.stages() or {}).items():
                if stage != "reported":
                    lost[stage][point] += 1
        graded = sum(1 for d in runs if d.stages() is not None)
        for stage in STAGES[1:]:
            if lost[stage]:
                out += ["", f"**{STAGE_NAMES[stage]}**, most often (of {graded} runs):"]
                out += [f"- {n}×  {_label(case, point)}" for point, n in lost[stage].most_common(8)]
        out += ["", f"### {case_id}: can the score show a change?", ""]
        agreement = judge_agreement([g for g in grades if any(g["run_id"] == d.run_id for d in runs)])
        if agreement is None:
            out.append("- **Judges:** only one judge has graded these reports.")
        else:
            first, second = agreement.judges
            out.append(f"- **Judges:** {first} and {second} differ on {agreement.differ} of {agreement.compared} "
                       f"verdicts ({100 * agreement.differ / max(agreement.compared, 1):.0f}%); {first} alone credits "
                       f"{agreement.credited_by[first]}, {second} alone {agreement.credited_by[second]}.")
            out += [f"  - {n}×  {_label(case, point)}" for point, n in agreement.points.most_common(5)]
        urls = sum(len(d.equivalent_urls()) for d in runs)
        out.append(f"- **Equivalent URLs:** {urls} missed points across {len(runs)} runs ask for an address the report "
                   "gives on the same site. Check by hand; they are judge literalness, not missing research.")
        unreached = never_met(case, history.get(case_id, []))
        out.append(f"- **Never met:** {len(unreached)} of {total} points in any view of the "
                   f"{len(history.get(case_id, []))} stored grades of {case_id}. Possibly out of reach; confirm by hand"
                   + (":" if unreached else "."))
        out += [f"  - {_label(case, point)}" for point in unreached]
        # The rescouts the stage table counts: both views graded.
        rescouts = [d for d in runs if d.report is None and d.stages() is not None]
        if rescouts:
            claimed = {arm: [d.score() or 0 for d in rescouts if d.arm == arm] for arm in dict.fromkeys(d.arm for d in rescouts)}
            out.append(f"- **Points claimed ({judge}):** " + "; ".join(
                f"{arm or 'no arm'} {statistics.mean(scores):.1f} over {len(scores)} rescouts ({', '.join(map(str, scores))})"
                for arm, scores in claimed.items()) + ".")
            first, *others = claimed
            for arm in others:
                smallest = detectable_difference(claimed[first], claimed[arm])
                if smallest is not None:
                    out.append(f"  - {arm} against {first}: arms that differ by less than about {smallest:.1f} "
                               "points cannot be told apart from run-to-run variation.")
        arms = list(dict.fromkeys(d.arm for d in runs if d.report is not None))
        if len(arms) == 2:
            for name in sorted({_judge_name(g) for g in grades}):
                scores = {arm: [sum(verdicts(g["points"]).values()) for g in grades if g["status"] == "succeeded"
                                and _judge_name(g) == name and any(d.run_id == g["run_id"] and d.arm == arm for d in runs)]
                          for arm in arms}
                smallest = detectable_difference(scores[arms[0]], scores[arms[1]])
                if smallest is not None:
                    out.append(f"- **Detectable difference ({name}):** with {len(scores[arms[0]])} and "
                               f"{len(scores[arms[1]])} runs, arms that differ by less than about {smallest:.1f} "
                               f"points cannot be told apart from run-to-run variation.")
        out.append("")
    return "\n".join(out)
