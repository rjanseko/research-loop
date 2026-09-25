"""Do several planner calls, or a landscape survey before planning, give steadier plans than one call?

The planner has split the same question differently from run to run: `task2+`, seven countries, was
planned by country, by topic, in pairs, and in a mix. This plans each study case REPEATS times in each
of three arms and measures, without a model, how alike a case's plans are and how much of what the
objective names they cover:

- `single`: today's planner call, once;
- `best-of-3`: three planner calls at once, and a selector call (FIXED selector model) that picks one
  against fixed criteria: one subject per question, the objective covered, no overlap;
- `landscape`: a scout (LANDSCAPE model, LANDSCAPE_LIMITS) surveys the objective first, and the planner
  plans with its summary and findings (`landscape` in the planner prompt).

Every unit is a Postgres job marked with the trial and arm, as in prompt_trial.py, under `--max-usd`.
The decision rules, set before the trial ran, are in docs/settings-study.md.

    .venv/bin/python scripts/plan_trial.py --max-usd 2
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import importlib.util
import json
import re
import sys
from contextlib import AsyncExitStack
from dataclasses import replace
from datetime import UTC, datetime
from itertools import combinations
from pathlib import Path
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field
from pydantic_ai import Agent, ModelRetry, RunContext

from research_loop.async_orchestrator import AsyncResearchLoop, landscape_question
from research_loop.policy import ModelPolicy
from research_loop.schemas import ResearchConstraints, ResearchPlan, ResearchRole

_SCRIPTS = Path(__file__).parent
_STUDY = _SCRIPTS.parent / "examples" / "settings_study.py"
ARMS = ("single", "best-of-3", "landscape")
REPEATS = 2
# The deep questions, whose plans have varied; the short ones plan to one to three questions every time.
DEFAULT_CASES = ("task2+", "task8", "task17+", "task26")
SELECTOR = "openai:gpt-6-luna"
LANDSCAPE = "zai:glm-5.3-flash"
LANDSCAPE_LIMITS = {"max_requests": 8, "max_tool_calls": 16}
# Plans of a case that match this well, question by question, count as the same split (plan_agreement).
MATCH_THRESHOLD = 0.35
# The entities each case's objective names, with the forms plans use for them, written down before the
# follow-up trial ran. Two questions naming the same entities split a case the same way, whatever their
# wording, which word overlap missed in the first trial (docs/settings-study.md).
ENTITIES: dict[str, dict[str, tuple[str, ...]]] = {
    "task2+": {
        "indonesia": ("indonesia",), "malaysia": ("malaysia",), "pakistan": ("pakistan",),
        "philippines": ("philippine", "filipino"), "sri lanka": ("sri lanka",), "thailand": ("thailand", "thai"),
        "vietnam": ("vietnam", "viet nam"),
    },
    "task17+": {
        "outsystems": ("outsystems",), "mendix": ("mendix",), "power apps": ("power apps", "powerapps"),
        "ui bakery": ("ui bakery",), "appcube": ("appcube",), "appmaster": ("appmaster",), "retool": ("retool",),
        "new oriental": ("new oriental",), "mercedes-benz": ("mercedes",), "mdec": ("mdec",),
        "masshousing": ("masshousing",),
    },
}

_STOPWORDS = frozenset(
    ["the", "and", "for", "with", "that", "this", "from", "what", "which", "are", "was", "were", "have", "has", "how", "does", "into", "over", "under", "their", "its", "about", "each", "other", "than", "then", "them", "they", "these", "those", "such", "when", "where", "while", "also", "into", "more", "most", "early", "late", "as", "of", "in", "on", "to", "by", "or", "an", "be", "is", "it", "at", "a"])


class Selection(BaseModel):
    choice: int = Field(description="The number of the chosen plan, from 1")
    reason: str = Field(description="One sentence on why")


SELECTOR_INSTRUCTIONS = (
    "Choose the research plan that will serve the objective best. Prefer, in order: each question about one "
    "subject (one country, product, or period when the objective names several), every entity or requirement "
    "the objective names covered by some question, no two questions overlapping, and fewer questions when "
    "the plans are otherwise equal. Answer with the plan's number."
)
selector_agent = Agent(output_type=Selection, instructions=SELECTOR_INSTRUCTIONS, deps_type=int)


@selector_agent.output_validator
def _choice_in_range(ctx: RunContext[int], output: Selection) -> Selection:
    if not 1 <= output.choice <= ctx.deps:
        raise ModelRetry(f"choice must be between 1 and {ctx.deps}")
    return output


def _load(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9+]+", text.lower()) if len(w) > 2 and w not in _STOPWORDS}


def plan_agreement(a: list[str], b: list[str]) -> float:
    """How alike two plans' splits are: questions matched one to one by shared words, over the larger plan.

    A question matches its most similar unmatched partner when their word overlap (Jaccard) reaches
    MATCH_THRESHOLD. Two plans that split a case the same way score 1; one by country and one by topic
    score near 0.
    """
    if not a or not b:
        return 0.0
    words_a, words_b = [_words(q) for q in a], [_words(q) for q in b]
    pairs = sorted(((len(x & y) / len(x | y) if x | y else 0.0, i, j)
                    for i, x in enumerate(words_a) for j, y in enumerate(words_b)), reverse=True)
    used_a: set[int] = set()
    used_b: set[int] = set()
    matched = 0
    for score, i, j in pairs:
        if score < MATCH_THRESHOLD:
            break
        if i not in used_a and j not in used_b:
            used_a.add(i)
            used_b.add(j)
            matched += 1
    return matched / max(len(a), len(b))


def entities_in(question: str, entities: dict[str, tuple[str, ...]]) -> frozenset[str]:
    """The case's entities a question names, by any of their forms, as whole words."""
    text = question.lower()
    return frozenset(name for name, forms in entities.items()
                     if any(re.search(rf"\b{re.escape(form)}", text) for form in forms))


def entity_agreement(a: list[str], b: list[str], entities: dict[str, tuple[str, ...]]) -> float:
    """How alike two plans' splits are by the entities each question names, over the larger plan.

    A question naming entities matches one in the other plan naming exactly the same ones. Questions
    naming none, such as a topic across every country, match by shared words as plan_agreement does.
    """
    if not a or not b:
        return 0.0
    left = [entities_in(q, entities) for q in a]
    right = [entities_in(q, entities) for q in b]
    matched = 0
    unused = list(range(len(b)))
    for i, names in enumerate(left):
        if names and (j := next((j for j in unused if right[j] == names), None)) is not None:
            unused.remove(j)
            matched += 1
    rest_a = [q for q, names in zip(a, left, strict=True) if not names]
    rest_b = [b[j] for j in unused if not right[j]]
    if rest_a and rest_b:
        matched += round(plan_agreement(rest_a, rest_b) * max(len(rest_a), len(rest_b)))
    return matched / max(len(a), len(b))


def entity_coverage(questions: list[str], entities: dict[str, tuple[str, ...]]) -> float:
    """Share of the case's entities that some question names."""
    named = set().union(*(entities_in(q, entities) for q in questions)) if questions else set()
    return len(named) / len(entities)


def named_entities(objective: str) -> set[str]:
    """Capitalized words the objective names mid-sentence: its countries, programmes, and products."""
    names = set()
    for sentence in re.split(r"(?<=[.?!:])\s+|\n+", objective):
        for word in re.findall(r"[A-Za-z][A-Za-z0-9+-]*", sentence)[1:]:
            if word[0].isupper() and len(word) > 2 and word.lower() not in _STOPWORDS:
                names.add(word.lower())
    return names


def coverage(questions: list[str], objective: str) -> float:
    """Share of the objective's named entities that some question names."""
    names = named_entities(objective)
    if not names:
        return 1.0
    text = " ".join(questions).lower()
    return sum(name in text for name in names) / len(names)


def _landscape_policy(policy: ModelPolicy) -> ModelPolicy:
    """The study policy with the landscape survey's route as the cheap scout, which a low-difficulty question gets."""
    policy = copy.copy(policy)
    policy.cheap_scout = replace(policy.routes[ResearchRole.SCOUT], model=LANDSCAPE, **LANDSCAPE_LIMITS)
    return policy


async def plan_arm(arm: str, loop: AsyncResearchLoop, selector_route: Any, objective: str,
                   constraints: ResearchConstraints, job_id: UUID) -> dict[str, Any]:
    """One unit of an arm: its plan, with the landscape findings or the selector's choice when it has them."""
    if arm == "single":
        plan = await loop._plan(job_id, objective, constraints, None)
        return {"plan": plan}
    if arm == "landscape":
        survey = await loop._run_scout(job_id, landscape_question(objective), asyncio.Semaphore(1), constraints, None)
        plan = await loop._plan(job_id, objective, constraints, None, landscape=survey)
        return {"plan": plan, "landscape_findings": len(survey.claims)}
    plans: list[ResearchPlan] = list(await asyncio.gather(
        *(loop._plan(job_id, objective, constraints, None) for _ in range(3))))
    listing = [{"plan": number, "questions": [q.question for q in plan.questions]}
               for number, plan in enumerate(plans, 1)]
    selection: Selection = await loop._run_agent(
        job_id=job_id, agent=selector_agent, role=ResearchRole.PLANNER, route=selector_route, deps=len(plans),
        prompt=json.dumps({"objective": objective, "plans": listing}, ensure_ascii=False))
    chosen = plans[selection.choice - 1]
    await loop.repository.save_plan(job_id, chosen.model_dump(mode="json"))
    return {"plan": chosen, "choice": selection.choice, "candidates": [len(p.questions) for p in plans]}


async def run_trial(settings: Any, max_usd: float, case_ids: tuple[str, ...], arms: tuple[str, ...] = ARMS,
                    repeats: int = REPEATS) -> dict[str, Any]:
    prompt_trial = _load("prompt_trial", _SCRIPTS / "prompt_trial.py")
    study = _load("settings_study", _STUDY)
    from research_loop.benchmarks import load_suite

    manifest, specs = load_suite(study.SUITE)
    cases = [spec for spec in specs if spec.case_id in case_ids]
    policy, config = study.build(True, 5.0, 1.0, settings, cache_mode="reuse", budget_notes=(ResearchRole.SCOUT,))
    selector_route = replace(policy.routes[ResearchRole.PLANNER], model=SELECTOR, refusal_fallback=None)
    budget = prompt_trial.TrialBudget(max_usd, concurrency=3)
    started = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    async with AsyncExitStack() as stack:
        repo = await prompt_trial.open_trial_repository(stack, settings.database_dsn)
        loop = AsyncResearchLoop(_landscape_policy(policy), config, repo, settings=settings)

        async def unit(case: Any, arm: str, repeat: int) -> dict[str, Any]:
            objective = case.render_objective()
            constraints = ResearchConstraints(blocked_urls=case.blocked_urls, benchmark_id=case.benchmark_id,
                                              benchmark_case_id=case.case_id, benchmark_suite=manifest.name)
            done = await prompt_trial._unit(
                loop, budget, constraints, lambda job_id: plan_arm(arm, loop, selector_route, objective, constraints, job_id),
                objective=objective, trial={"name": "plan", "started": started, "arm": arm, "repeat": repeat,
                                            "case_id": case.case_id})
            questions = [q.question for q in done.result["plan"].questions] if done.result else []
            covered = (entity_coverage(questions, ENTITIES[case.case_id]) if case.case_id in ENTITIES
                       else coverage(questions, objective))
            return {"case_id": case.case_id, "arm": arm, "repeat": repeat, "job_id": done.job_id, "error": done.error,
                    "cost_usd": done.cost, "questions": questions, "coverage": covered,
                    **{k: v for k, v in (done.result or {}).items() if k != "plan"}}

        rows = list(await asyncio.gather(*(unit(case, arm, repeat) for case in cases for arm in arms
                                           for repeat in range(1, repeats + 1))))
    return {"started": started, "max_usd": max_usd, "spent_usd": budget.spent, "rows": rows,
            "summary": summarize(rows, arms)}


def summarize(rows: list[dict[str, Any]], arms: tuple[str, ...] = ARMS) -> dict[str, dict[str, Any]]:
    """Per arm: mean agreement between a case's plans, mean coverage, mean question count, and cost.

    Agreement is by named entities (entity_agreement) for a case in ENTITIES, else by words; `same_split`
    is the share of a case's plan pairs that agree fully.
    """
    summary = {}
    for arm in arms:
        mine = [row for row in rows if row["arm"] == arm and not row["error"]]
        by_case: dict[str, list[list[str]]] = {}
        for row in mine:
            by_case.setdefault(row["case_id"], []).append(row["questions"])
        agreements = [entity_agreement(a, b, ENTITIES[case]) if case in ENTITIES else plan_agreement(a, b)
                      for case, plans in by_case.items() for a, b in combinations(plans, 2)]
        summary[arm] = {
            "agreement": sum(agreements) / len(agreements) if agreements else None,
            "same_split": sum(value == 1.0 for value in agreements) / len(agreements) if agreements else None,
            "coverage": sum(row["coverage"] for row in mine) / len(mine) if mine else None,
            "questions": sum(len(row["questions"]) for row in mine) / len(mine) if mine else None,
            "cost_usd": sum(row["cost_usd"] for row in rows if row["arm"] == arm),
            "failed": sum(1 for row in rows if row["arm"] == arm and row["error"]),
        }
    return summary


def render(record: dict[str, Any]) -> str:
    lines = ["arm         agreement same_split  coverage  questions  cost_usd  failed"]
    for arm, s in record["summary"].items():
        fmt = lambda value, spec: format(value, spec) if value is not None else "-"
        lines.append(f"{arm:<11} {fmt(s['agreement'], '9.2f')} {fmt(s.get('same_split'), '10.2f')} {fmt(s['coverage'], '9.2f')} "
                     f"{fmt(s['questions'], '10.1f')} {s['cost_usd']:9.3f} {s['failed']:7}")
    lines.append(f"spent ${record['spent_usd']:.2f} of the ${record['max_usd']:.2f} cap")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    from research_loop.settings import ResearchSettings

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--max-usd", type=float, required=True, help="Hard cap on the trial's spend")
    parser.add_argument("--cases", nargs="+", default=list(DEFAULT_CASES), help="Study cases to plan")
    parser.add_argument("--arms", nargs="+", choices=ARMS, default=list(ARMS), help="Arms to run (default all)")
    parser.add_argument("--repeats", type=int, default=REPEATS, help=f"Plans per case and arm (default {REPEATS})")
    parser.add_argument("--output", type=Path, help="Trial record (default: benchmark_outputs/settings_study/plan-trial/)")
    args = parser.parse_args(argv)
    if args.max_usd <= 0:
        parser.error("--max-usd must be positive")
    settings = ResearchSettings.from_env()
    if not settings.database_dsn:
        parser.error("trials store their jobs in Postgres; set DATABASE_URL (see docs/setup.md#postgres)")
    settings = settings.model_copy(update={"benchmark_cache": settings.benchmark_output / "settings_study" / "cache"})
    if args.repeats < 2:
        parser.error("--repeats must be at least 2: agreement compares a case's plans with each other")
    record = asyncio.run(run_trial(settings, args.max_usd, tuple(args.cases), tuple(args.arms), args.repeats))
    output = args.output or settings.benchmark_output / "settings_study" / "plan-trial" / f"{record['started']}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(record, indent=2, default=str) + "\n", encoding="utf-8")
    print(render(record))
    print(f"record: {output}")


if __name__ == "__main__":
    main()
