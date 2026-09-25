"""Estimate what context-management changes to the scout and deep-dive tool loops would save.

No model call and no network. It replays one tool loop request by request on the p01 token
profile that `route_costs.py` prices, then changes how much history each request resends:

- mask: replace tool results older than the last K turns with a short stub (observation
  masking, Lindenbauer et al. 2025, arXiv:2508.21433);
- batched mask: the same, but move the masking cutoff only every B turns, so the cached
  prompt prefix survives between moves;
- fewer turns: the budget awareness of BATS (arXiv:2511.17006), which reported 40% fewer
  search calls at equal accuracy;
- parallel calls: two tool calls per turn, so half the turns resend the same results;
- dedup: a repeated fetch returns a stub instead of the document again (ArcticSwarm,
  arXiv:2609.01870, saw a third of fetches repeat another agent's URL).

Caching is simulated, not assumed: each request reads from cache the prefix it shares with the
loop's previous request (`--hit-rate` of it), and pays for the rest as fresh input. Masking
changes that prefix, which is why the batched variant exists. docs/model-routing.md has the
token profile's caveats; the savings here are a model to pick experiments with, not a result.

    .venv/bin/python scripts/loop_costs.py                     # value preset loop models
    .venv/bin/python scripts/loop_costs.py --lineup "F Z.ai-heavy" --hit-rate 0.9
"""
from __future__ import annotations

import argparse
import math
from dataclasses import dataclass
from datetime import UTC, datetime

from route_costs import LINEUPS, LOOP_REQUESTS, PROFILE, request_cost

# Tokens every loop request starts with: instructions, tool and output schemas, and the task
# prompt (about 2,000 characters). An estimate; the per-turn sizes below are solved from it.
BASE_PROMPT_TOKENS = 2_500
# Tokens a masked or deduplicated tool result still sends: the call, URL, and a one-line note.
STUB_TOKENS = 40
# Share of tool-result tokens that repeat a document another agent already fetched.
DEDUP_SHARE = 0.20


@dataclass(frozen=True)
class Loop:
    """One tool loop's shape: requests, and the tokens each turn appends to the history."""

    turns: int
    output_per_turn: float  # the assistant's tool calls and text, resent afterwards
    result_per_turn: float  # tool results


def calibrate(input_tokens: int, output_tokens: int, turns: int = LOOP_REQUESTS) -> Loop:
    """Per-turn sizes that reproduce the profile's summed input when every request resends all.

    Request i sends BASE + (i - 1) × (output + result), so the sum over n requests is
    n × BASE + (output + result) × n(n - 1) / 2.
    """
    output = output_tokens / turns
    growth = (input_tokens - turns * BASE_PROMPT_TOKENS) / (turns * (turns - 1) / 2)
    if growth <= output:
        raise ValueError("profile input too small for the base prompt; lower BASE_PROMPT_TOKENS")
    return Loop(turns, output, growth - output)


@dataclass(frozen=True)
class Strategy:
    name: str
    keep_last: int | None = None  # full tool results kept; None keeps all
    mask_every: int = 1  # turns between moves of the masking cutoff
    turn_factor: float = 1.0  # share of the baseline turns the loop needs
    calls_per_turn: int = 1
    dedup_share: float = 0.0


STRATEGIES = (
    Strategy("baseline: resend everything"),
    Strategy("mask, keep last 5", keep_last=5),
    Strategy("mask, keep last 3", keep_last=3),
    Strategy("mask, keep 3, move every 4", keep_last=3, mask_every=4),
    Strategy("fewer turns (budget, 0.6x)", turn_factor=0.6),
    Strategy("2 tool calls per turn", calls_per_turn=2),
    Strategy(f"dedup repeated fetches ({DEDUP_SHARE:.0%})", dedup_share=DEDUP_SHARE),
    Strategy("combined: batched mask + 2 calls + dedup", keep_last=3, mask_every=4, calls_per_turn=2,
             dedup_share=DEDUP_SHARE),
    Strategy("combined + fewer turns", keep_last=3, mask_every=4, calls_per_turn=2,
             dedup_share=DEDUP_SHARE, turn_factor=0.6),
)


def requests(loop: Loop, strategy: Strategy) -> list[tuple[float, float, float]]:
    """(input, output, shared prefix with the previous request) for each request of the loop.

    The same tool results are gathered in fewer turns when calls are parallel; fewer turns
    means fewer results. Output per result stays the same either way.
    """
    results = max(1, round(loop.turns * strategy.turn_factor))
    turns = math.ceil(results / strategy.calls_per_turn)
    full = loop.result_per_turn * (1 - strategy.dedup_share) + STUB_TOKENS * strategy.dedup_share
    block_output = loop.output_per_turn * strategy.calls_per_turn
    block_full = full * strategy.calls_per_turn
    block_stub = STUB_TOKENS * strategy.calls_per_turn
    out: list[tuple[float, float, float]] = []
    previous: list[float] | None = None
    for i in range(turns):
        # Before request i, blocks 0..i-1 exist; block j is full unless it is behind the cutoff.
        cutoff = 0
        if strategy.keep_last is not None:
            moved_at = (i // strategy.mask_every) * strategy.mask_every
            cutoff = max(0, moved_at - strategy.keep_last)
        blocks = [block_output + (block_stub if j < cutoff else block_full) for j in range(i)]
        prompt = BASE_PROMPT_TOKENS + sum(blocks)
        shared = 0.0
        if previous is not None:
            shared = BASE_PROMPT_TOKENS
            for now, before in zip(blocks, previous, strict=False):
                if now != before:
                    # The assistant's part of a newly masked block is still identical.
                    shared += block_output
                    break
                shared += now
        out.append((prompt, block_output, shared))
        previous = blocks
    return out


def loop_cost(model: str, loop: Loop, strategy: Strategy, hit_rate: float, when: datetime) -> tuple[float, float]:
    """USD and summed input tokens for one run of the loop."""
    cost = tokens = 0.0
    for prompt, output, shared in requests(loop, strategy):
        share = hit_rate * shared / prompt if hit_rate else 0.0
        cost += request_cost(model, prompt, output, share, when)
        tokens += prompt
    return cost, tokens


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--lineup", default="B value preset", choices=list(LINEUPS),
                        help="models for the scout and deep-dive loops")
    parser.add_argument("--hit-rate", type=float, default=1.0,
                        help="share of the shared prefix the provider bills as cached; 0 turns caching off")
    parser.add_argument("--date", help="price date, YYYY-MM-DD (default: today)")
    args = parser.parse_args()
    if not 0 <= args.hit_rate <= 1:
        parser.error("--hit-rate must be between 0 and 1")
    when = datetime.fromisoformat(args.date).replace(tzinfo=UTC) if args.date else datetime.now(UTC)
    lineup = LINEUPS[args.lineup]

    # Single calls do not change, so price them once to show the loops' share of a question.
    fixed = sum(
        calls * request_cost(lineup[route], input_tokens, output_tokens, 0.0, when)
        for route, calls, input_tokens, output_tokens, is_loop in PROFILE.values() if not is_loop
    )
    loops = [
        (name, route, calls, calibrate(input_tokens, output_tokens))
        for name, (route, calls, input_tokens, output_tokens, is_loop) in PROFILE.items() if is_loop
    ]

    print(f"USD per question on the p01 loop profile, {args.lineup}, list prices on {when:%Y-%m-%d}")
    print(f"caching: {args.hit_rate:.0%} of each request's prefix shared with the previous one")
    for name, route, calls, loop in loops:
        print(f"  {name}: {calls} x {lineup[route]}, {loop.turns} turns, "
              f"{loop.result_per_turn:,.0f} result + {loop.output_per_turn:,.0f} output tokens a turn")
    print(f"  single calls (planner, gap, salvage, synthesis, verifier): {fixed:.2f}")

    header = "".join(f"{name:>13}" for name, *_ in loops)
    print(f"\n{'strategy':44}{header}{'loop input':>12}{'question':>10}{'saving':>8}")
    baseline_total = None
    for strategy in STRATEGIES:
        costs, tokens = [], 0.0
        for _, route, calls, loop in loops:
            cost, loop_tokens = loop_cost(lineup[route], loop, strategy, args.hit_rate, when)
            costs.append(calls * cost)
            tokens += calls * loop_tokens
        total = fixed + sum(costs)
        baseline_total = baseline_total or total
        saving = 1 - total / baseline_total
        print(f"{strategy.name:44}" + "".join(f"{cost:13.3f}" for cost in costs)
              + f"{tokens / 1000:11,.0f}k{total:10.2f}{saving:8.0%}")


if __name__ == "__main__":
    main()
