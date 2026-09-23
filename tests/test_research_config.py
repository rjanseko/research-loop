from __future__ import annotations

import pytest

from research_loop.async_orchestrator import ResearchConfig


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("max_parallel_scouts", 0),  # a zero-slot semaphore would wait forever
        ("max_parallel_deep_dives", 0),
        ("max_deep_dives_per_round", -1),
        ("max_verification_rounds", -1),
        ("min_scout_confidence", 1.5),
    ],
)
def test_research_config_rejects_values_that_cannot_run(field: str, value: float) -> None:
    with pytest.raises(ValueError, match=field):
        ResearchConfig(**{field: value})


def test_research_config_allows_disabling_deep_dives_and_verification_rounds() -> None:
    config = ResearchConfig(max_deep_dives_per_round=0, max_verification_rounds=0)
    assert (config.max_deep_dives_per_round, config.max_verification_rounds) == (0, 0)
