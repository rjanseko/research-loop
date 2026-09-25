"""Price candidate model lineups on one research question's measured token profile.

No model call and no network: prices come from the installed `genai-prices` table, the same
data PydanticAI uses to cost a run. The token profile is the p01 calibration question (see
long_horizon/agentic_se/PROMPT_SIZES.md); docs/model-routing.md explains the lineups and
the caveats. Rerun after a price change or a new model:

    .venv/bin/python scripts/route_costs.py
    .venv/bin/python scripts/route_costs.py --date 2027-01-15   # prices on another date
"""
from __future__ import annotations

import argparse
from datetime import UTC, datetime

from genai_prices import Usage, calc_price

ROLES = ("planner", "scout", "gap", "deep_dive", "synthesizer", "verifier")

# task: (route, calls per question, input tokens per call, output tokens per call, tool loop).
# Tokens are on OpenAI's tokenizer and summed across a tool loop's requests. From the p01 pilot
# (job 253311b6) and its reruns (46044486, 31eba511): three scouts, one salvaged; two deep dives,
# both salvaged; finishing prompts at the projected-ledger sizes.
PROFILE = {
    "planner": ("planner", 1, 1_100, 1_200, False),
    "scout": ("scout", 3, 240_000, 11_000, True),
    "scout salvage": ("scout", 1, 37_000, 10_000, False),
    "gap": ("gap", 1, 23_000, 1_300, False),
    "deep dive": ("deep_dive", 2, 170_000, 3_000, True),
    "deep-dive salvage": ("deep_dive", 2, 14_000, 4_300, False),
    "synthesizer": ("synthesizer", 1, 26_000, 11_000, False),
    "verifier": ("verifier", 1, 33_000, 9_000, False),
}
# Requests per tool loop. Each request is priced alone, as providers bill it; pricing a loop's
# summed input as one request would wrongly cross the long-context price tier.
LOOP_REQUESTS = 16
# Input tokens per OpenAI token for the same JSON: 3.8 vs 2.5 characters per token (pilot).
TOKENIZER = {"anthropic": 3.8 / 2.5}
# Share of a tool loop's input billed as cache reads. "observed" is what the pilot's bills imply:
# GLM about 84%, the OpenAI deep dive about 5%, and Anthropic none, because no route sends
# cache_control. Providers the pilot did not use are guesses. "configured" assumes caching
# is set on every route.
OBSERVED_CACHE_SHARE = {"zai": 0.84, "deepseek": 0.84, "openai": 0.05, "anthropic": 0.0}
UNKNOWN_CACHE_SHARE = 0.5
CONFIGURED_CACHE_SHARE = 0.80

LINEUPS = {
    "A current defaults": {
        "planner": "anthropic:claude-opus-5", "scout": "zai:glm-5.3", "gap": "openai:gpt-5.6-sol",
        "deep_dive": "openai:gpt-5.6-sol", "synthesizer": "anthropic:claude-opus-5",
        "verifier": "openai:gpt-5.6-sol",
    },
    "B successors": {
        "planner": "anthropic:claude-opus-5-5", "scout": "zai:glm-5.3", "gap": "openai:gpt-6-sol",
        "deep_dive": "openai:gpt-6-sol", "synthesizer": "anthropic:claude-opus-5-5",
        "verifier": "openai:gpt-6-sol",
    },
    "C B, Sonnet 5 writes": {
        "planner": "anthropic:claude-opus-5-5", "scout": "zai:glm-5.3", "gap": "openai:gpt-6-sol",
        "deep_dive": "openai:gpt-6-sol", "synthesizer": "anthropic:claude-sonnet-5",
        "verifier": "openai:gpt-6-sol",
    },
    "D budget": {
        "planner": "anthropic:claude-sonnet-5", "scout": "deepseek:deepseek-v4-flash",
        "gap": "openai:gpt-6-luna", "deep_dive": "openai:gpt-6-sol",
        "synthesizer": "anthropic:claude-sonnet-5", "verifier": "openai:gpt-6-sol",
    },
    "E floor": {
        "planner": "openai:gpt-6-luna", "scout": "openai:gpt-6-luna", "gap": "openai:gpt-6-luna",
        "deep_dive": "openai:gpt-6-luna", "synthesizer": "anthropic:claude-sonnet-5",
        "verifier": "openai:gpt-6-luna",
    },
    "F all Opus 5.5": dict.fromkeys(ROLES, "anthropic:claude-opus-5-5"),
}


def request_cost(model: str, input_tokens: float, output_tokens: float, cache_share: float, when: datetime) -> float:
    provider, name = model.split(":", 1)
    input_tokens = round(input_tokens * TOKENIZER.get(provider, 1.0))
    cached = round(input_tokens * cache_share)
    usage = Usage(input_tokens=input_tokens, output_tokens=round(output_tokens), cache_read_tokens=cached)
    if provider == "anthropic" and cached:
        # Anthropic bills writing the uncached part to the cache above the plain input price.
        usage.cache_write_tokens = input_tokens - cached
    return float(calc_price(usage, name, provider_id=provider, genai_request_timestamp=when).total_price)


def lineup_cost(lineup: dict[str, str], caching: str, when: datetime) -> dict[str, float]:
    costs = dict.fromkeys(ROLES, 0.0)
    for route, calls, input_tokens, output_tokens, loop in PROFILE.values():
        model = lineup[route]
        share = 0.0
        if loop and caching == "configured":
            share = CONFIGURED_CACHE_SHARE
        elif loop:
            share = OBSERVED_CACHE_SHARE.get(model.partition(":")[0], UNKNOWN_CACHE_SHARE)
        requests = LOOP_REQUESTS if loop else 1
        per_request = request_cost(model, input_tokens / requests, output_tokens / requests, share, when)
        costs[route] += calls * requests * per_request
    return costs


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--date", help="price date, YYYY-MM-DD (default: today)")
    args = parser.parse_args()
    when = datetime.fromisoformat(args.date).replace(tzinfo=UTC) if args.date else datetime.now(UTC)
    print(f"USD per question on the p01 token profile, list prices on {when:%Y-%m-%d}")
    for caching in ("observed", "configured"):
        print(f"\nTool-loop prompt caching: {caching}")
        print(f"{'lineup':22}" + "".join(f"{role:>12}" for role in ROLES) + f"{'total':>9}")
        for name, lineup in LINEUPS.items():
            costs = lineup_cost(lineup, caching, when)
            print(f"{name:22}" + "".join(f"{costs[role]:12.3f}" for role in ROLES) + f"{sum(costs.values()):9.2f}")


if __name__ == "__main__":
    main()
