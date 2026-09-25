"""Price model candidates for each research role on one question's measured token profile.

No model call and no network: prices come from the installed `genai-prices` table, the same
data PydanticAI uses to put a cost on each run, including its copy of OpenRouter's catalog.
The token profile is the p01 calibration question (long_horizon/agentic_se/PROMPT_SIZES.md).
docs/model-routing.md explains the candidates, the lineups, and the caveats.

    .venv/bin/python scripts/route_costs.py                    # lineups and per-role candidates
    .venv/bin/python scripts/route_costs.py --date 2026-11-22  # prices on another date
"""
from __future__ import annotations

import argparse
from datetime import UTC, datetime

from genai_prices import Usage, calc_price

from research_loop.settings import PROVIDER_KEY_ENV

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
# Other vendors are treated like OpenAI.
TOKENIZER = {"anthropic": 3.8 / 2.5}
# Share of a tool loop's input billed as cache reads, as the pilot's provider-reported usage
# shows it: GLM about 84%, the OpenAI deep dive about 5%, Anthropic none, because no route sends
# cache_control. Other vendors are a guess. "configured" assumes caching is set on every route.
OBSERVED_CACHE_SHARE = {"zai": 0.84, "openai": 0.05, "anthropic": 0.0}
UNKNOWN_CACHE_SHARE = 0.5
CONFIGURED_CACHE_SHARE = 0.80
# Models `genai-prices` cannot price: (input, cached input, output) USD per million tokens.
# PydanticAI reports no cost for them either, which turns off the job's dollar cap.
MANUAL_PRICES = {
    "openrouter:tencent/hy4-preview": (0.834, 0.042, 2.501),  # OpenRouter listing, 2026-09
}
# The repo's model ids name the provider; `genai-prices` names some differently.
PRICE_PROVIDER = {"xai": "x-ai"}

OPUS_55 = "anthropic:claude-opus-5-5"
GPT6_SOL = "openai:gpt-6-sol"
GLM = "zai:glm-5.3"
GLM_FLASH = "zai:glm-5.3-flash"
RECOMMENDED = {
    "planner": OPUS_55, "scout": GLM, "gap": GPT6_SOL,
    "deep_dive": GPT6_SOL, "synthesizer": OPUS_55, "verifier": GPT6_SOL,
}
LINEUPS = {
    "A current defaults": {
        "planner": "anthropic:claude-opus-5", "scout": GLM, "gap": "openai:gpt-5.6-sol",
        "deep_dive": "openai:gpt-5.6-sol", "synthesizer": "anthropic:claude-opus-5",
        "verifier": "openai:gpt-5.6-sol",
    },
    "B recommended": RECOMMENDED,
    "C B, GLM Flash scouts": {**RECOMMENDED, "scout": GLM_FLASH},
    "D B, GLM synthesizer": {**RECOMMENDED, "synthesizer": GLM},
    "E B, GLM deep dives": {**RECOMMENDED, "deep_dive": GLM},
    "F Z.ai-heavy": {
        "planner": OPUS_55, "scout": GLM_FLASH, "gap": GLM,
        "deep_dive": GLM, "synthesizer": GLM, "verifier": GPT6_SOL,
    },
    "G B, OpenRouter open": {
        **RECOMMENDED, "scout": "openrouter:deepseek/deepseek-v4-flash",
        "deep_dive": "openrouter:tencent/hy4-preview",
    },
    "H floor": {
        "planner": "openai:gpt-6-luna", "scout": "openai:gpt-6-luna", "gap": "openai:gpt-6-luna",
        "deep_dive": "openai:gpt-6-luna", "synthesizer": "anthropic:claude-sonnet-5",
        "verifier": "openai:gpt-6-luna",
    },
    "I Opus 5.5 everywhere": dict.fromkeys(ROLES, OPUS_55),
}
# Models tried in each role, one at a time, with every other role as recommended.
CANDIDATES = {
    "planner": [OPUS_55, "anthropic:claude-sonnet-5", GPT6_SOL, GLM],
    "scout": [
        GLM, GLM_FLASH, "openai:gpt-6-luna", "google:gemini-3.8-flash",
        "openrouter:deepseek/deepseek-v4-flash", "openrouter:xiaomi/mimo-v2.5",
        "openrouter:tencent/hy4-preview", GPT6_SOL,
    ],
    "gap": [GPT6_SOL, GLM, "openai:gpt-6-luna", OPUS_55],
    "deep_dive": [
        GPT6_SOL, "openai:gpt-5.6-sol", GLM, "openrouter:tencent/hy4-preview",
        "openrouter:xiaomi/mimo-v2.5-pro", "openrouter:moonshotai/kimi-k3", OPUS_55,
    ],
    "synthesizer": [OPUS_55, "anthropic:claude-opus-5", "anthropic:claude-sonnet-5", GLM, GPT6_SOL],
    "verifier": [GPT6_SOL, "openai:gpt-5.6-sol", GLM, "openai:gpt-6-luna", OPUS_55],
}


def provider(model: str) -> str:
    return model.partition(":")[0]


def request_cost(model: str, input_tokens: float, output_tokens: float, cache_share: float, when: datetime) -> float:
    input_tokens = round(input_tokens * TOKENIZER.get(provider(model), 1.0))
    cached = round(input_tokens * cache_share)
    if model in MANUAL_PRICES:
        fresh_price, cached_price, output_price = MANUAL_PRICES[model]
        return ((input_tokens - cached) * fresh_price + cached * cached_price + output_tokens * output_price) / 1e6
    usage = Usage(input_tokens=input_tokens, output_tokens=round(output_tokens), cache_read_tokens=cached)
    if provider(model) == "anthropic" and cached:
        # Anthropic bills writing the uncached part to the cache above the plain input price.
        usage.cache_write_tokens = input_tokens - cached
    prov, name = model.split(":", 1)
    price = calc_price(usage, name, provider_id=PRICE_PROVIDER.get(prov, prov), genai_request_timestamp=when)
    return float(price.total_price)


def role_cost(route: str, model: str, caching: str, when: datetime) -> float:
    total = 0.0
    for task_route, calls, input_tokens, output_tokens, loop in PROFILE.values():
        if task_route != route:
            continue
        share = 0.0
        if loop and caching == "configured":
            share = CONFIGURED_CACHE_SHARE
        elif loop:
            share = OBSERVED_CACHE_SHARE.get(provider(model), UNKNOWN_CACHE_SHARE)
        requests = LOOP_REQUESTS if loop else 1
        total += calls * requests * request_cost(model, input_tokens / requests, output_tokens / requests, share, when)
    return total


def unsupported(models: list[str]) -> str:
    """Providers the research loop does not accept yet, which would need code to use."""
    missing = sorted({provider(m) for m in models} - set(PROVIDER_KEY_ENV))
    return f"  needs provider: {', '.join(missing)}" if missing else ""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--date", help="price date, YYYY-MM-DD (default: today)")
    args = parser.parse_args()
    when = datetime.fromisoformat(args.date).replace(tzinfo=UTC) if args.date else datetime.now(UTC)
    print(f"USD per question on the p01 token profile, list prices on {when:%Y-%m-%d}")
    for caching in ("observed", "configured"):
        print(f"\nLineups, tool-loop prompt caching {caching}")
        print(f"{'lineup':24}" + "".join(f"{role:>12}" for role in ROLES) + f"{'total':>8}")
        for name, lineup in LINEUPS.items():
            costs = [role_cost(role, lineup[role], caching, when) for role in ROLES]
            print(f"{name:24}" + "".join(f"{cost:12.3f}" for cost in costs) + f"{sum(costs):8.2f}"
                  + unsupported(list(lineup.values())))
    print("\nOne role at a time: that role's cost per question (observed / configured caching)")
    for role, models in CANDIDATES.items():
        print(f"\n{role}")
        for model in models:
            observed = role_cost(role, model, "observed", when)
            configured = role_cost(role, model, "configured", when)
            print(f"  {model:42}{observed:8.3f}{configured:8.3f}{unsupported([model])}")


if __name__ == "__main__":
    main()
