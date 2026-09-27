"""How much of a development case's expected set a run's research found, without a judge or a model call.

A member counts as found when a claim or a conclusion names it, and as named only when it appears nowhere
but in `unresolved`: the research saw it but did not establish it. Both are free, fast signals for
screening a change before paying for synthesis and grading; neither replaces the rubric grade.

Held-out cases have no expected sets here on purpose: tuning against them would spend their value.
"""
from __future__ import annotations

import re

# Members each development case's rubric expects, as patterns over lowercased text.
EXPECTED: dict[str, dict[str, str]] = {
    "drb2-task8": {
        "Materials Project": r"materials ?project", "OQMD": r"oqmd|open quantum materials",
        "NOMAD": r"nomad", "ICSD": r"icsd|inorganic crystal structure",
        "CSD": r"\bcsd\b|cambridge structural", "ASM Alloy Center": r"asm (alloy|international)|alloy center",
        "DDSE": r"ddse|solid[- ]state electrolyte",
    },
    "drb2-task68-plus": {
        "threshold rules": r"threshold", "queuing theory": r"queu", "reinforcement learning": r"reinforcement",
        "fuzzy logic": r"fuzzy", "machine learning": r"machine learning|neural", "time series": r"time[- ]series|arima",
    },
}


def coverage(record: dict) -> dict:
    """The run's case, and which expected members its research found or only named."""
    case_id = ((record.get("config") or {}).get("case") or {}).get("id")
    members = EXPECTED.get(case_id or "")
    if members is None:
        raise ValueError(f"no expected set for case {case_id!r}")
    results = [result for results in (record.get("ledger") or {}).values() for result in results]
    established = " ".join([result.get("conclusion", "") for result in results]
                           + [claim.get("statement", "") for result in results for claim in result.get("claims", [])])
    unresolved = " ".join(item for result in results for item in result.get("unresolved", []))
    found = [name for name, pattern in members.items() if re.search(pattern, established.lower())]
    named = [name for name, pattern in members.items()
             if name not in found and re.search(pattern, unresolved.lower())]
    return {"case": case_id, "run_id": record.get("run_id"), "found": found, "named_only": named,
            "missing": [name for name in members if name not in found and name not in named],
            "expected": len(members)}
