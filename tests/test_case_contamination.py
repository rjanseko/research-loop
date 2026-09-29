"""No text a run's models can see may carry a frozen case's wording.

The instructions, budget notes, output schemas, tool descriptions, and validator messages are compared with
every frozen case's question and rubric. The contamination audit of 28 September 2026 found a drb2-task8
answer, 'Inorganic Crystal Structure Database (ICSD)', as the example in the scouts' output schema since
scout-v6, and the case's own categories and fields as the planner's and scouts' examples since scout-v13.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from research_loop import agents, prompts, tools

CASES = Path(prompts.__file__).with_name("study_cases.jsonl")
_STOP = {"the", "a", "an", "of", "and", "or", "to", "in", "for", "on", "with", "by", "as", "is", "are", "be",
         "its", "it", "this", "that", "each", "at", "from", "such", "their", "what", "which", "how", "into",
         "than", "not", "was", "were", "has", "have", "had", "do", "does", "can", "may", "more", "most", "all",
         "any", "one", "two"}
# Phrases shared with a case that are ordinary research wording, each with why it may stay.
ALLOWED = {
    "the answer must": "generic instruction wording (drb2-task82's question also uses it)",
    "a comprehensive report": "generic (drb2-task21's question asks for one)",
    "can no longer": "generic English",
    "preprints and published": "keeping preprints apart from published work is general source hygiene (since v1)",
    "about its own": "part of the vendor-claims rule below",
    "as vendor claims": "labeling vendors' claims about their own products is general source hygiene, written with the "
                        "in-house st07 case in v1; see docs/audit-2026-09-28-case-contamination.md",
}


def _visible_text() -> str:
    texts = [*prompts.INSTRUCTIONS.values(), *map(str, prompts.BUDGET_NOTES.values())]
    texts += [json.dumps(output.model_json_schema()) for output in prompts.OUTPUTS.values()]
    texts += re.findall(r'"""(.*?)"""', Path(tools.__file__).read_text(), re.DOTALL)
    texts += re.findall(r'"([^"]{20,})"', Path(agents.__file__).read_text())
    return " ".join(texts).lower()


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9][a-z0-9'\-]*", text.lower())


def _trigrams(words: list[str]) -> set[str]:
    return {" ".join(words[i:i + 3]) for i in range(len(words) - 2)
            if sum(word not in _STOP for word in words[i:i + 3]) >= 2}


def _case_text(case: dict) -> str:
    rubrics = case.get("rubrics") or {}
    items = [item for value in rubrics.values() for item in (value if isinstance(value, list) else [value])] \
        if isinstance(rubrics, dict) else list(rubrics)
    return " ".join([case["objective"], *map(str, items)])


def test_no_model_visible_text_shares_a_frozen_cases_wording() -> None:
    visible = _trigrams(_words(_visible_text()))
    found = {}
    for line in CASES.read_text(encoding="utf-8").splitlines():
        if line.strip():
            case = json.loads(line)
            if shared := sorted(visible & _trigrams(_words(_case_text(case))) - ALLOWED.keys()):
                found[case["id"]] = shared
    assert not found, f"model-visible text carries case wording: {found}"


# Long or hyphenated words shared with some case that are ordinary wording.
GENERIC_TERMS = {"authoritative", "comprehensive", "contradiction", "definitions", "description", "established",
                 "follow-up", "independent", "requirement", "requirements"}


def test_no_model_visible_text_uses_a_frozen_cases_distinctive_terms() -> None:
    # A single term escapes the phrase check: the planner's example "Exploration-based: disadvantages" was one.
    visible = set(_words(_visible_text()))
    found = {}
    for line in CASES.read_text(encoding="utf-8").splitlines():
        if line.strip():
            case = json.loads(line)
            terms = {word for word in _words(_case_text(case)) if "-" in word or len(word) >= 11}
            if shared := sorted(terms & visible - GENERIC_TERMS):
                found[case["id"]] = shared
    assert not found, f"model-visible text uses case terms: {found}"
