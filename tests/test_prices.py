from __future__ import annotations

from decimal import Decimal

import pytest
from genai_prices import Usage, calc_price, data_snapshot
from pydantic_ai._genai_prices import calculate_price_for_usage
from pydantic_ai.usage import RequestUsage

from research_loop.prices import (
    OverridingUpdatePrices,
    install_price_overrides,
    override_for,
    price_overrides,
    with_overrides,
)

ZAI_URL = "https://api.z.ai/api/paas/v4/"
MILLION = 1_000_000


@pytest.fixture
def bundled_prices():
    """Start from genai-prices' bundled data, and put back whatever was installed afterwards."""
    previous = data_snapshot._custom_snapshot
    data_snapshot.set_custom_snapshot(None)
    yield data_snapshot._bundled_snapshot()
    data_snapshot.set_custom_snapshot(previous)


def _price(model: str, **usage: int) -> Decimal:
    return calc_price(Usage(**usage), model, provider_id="zai").total_price


def test_overrides_price_calls_the_way_pydantic_ai_prices_them(bundled_prices) -> None:
    install_price_overrides()
    usage = RequestUsage(input_tokens=MILLION, cache_read_tokens=200_000, output_tokens=100_000)
    # By API URL first, as PydanticAI tries it, then by provider name.
    for url in (ZAI_URL, None):
        price = calculate_price_for_usage(usage, model_name="glm-5.3-flashx", provider_api_url=url, provider_name="zai")
        assert price.total_price == Decimal("0.8") * Decimal("0.37") + Decimal("0.2") * Decimal("0.075") + Decimal("0.125")
    assert _price("glm-5.3-flash", input_tokens=MILLION) == Decimal("0.15")
    assert _price("GLM-5.3-Flash", output_tokens=MILLION) == Decimal("0.50")


def test_other_models_keep_their_genai_prices(bundled_prices) -> None:
    before = _price("glm-5.3", input_tokens=MILLION, output_tokens=MILLION)
    install_price_overrides()
    assert _price("glm-5.3", input_tokens=MILLION, output_tokens=MILLION) == before
    with pytest.raises(LookupError):
        _price("glm-5.3-flashy", input_tokens=MILLION)


def test_installing_twice_changes_nothing_and_leaves_the_bundled_data_alone(bundled_prices) -> None:
    install_price_overrides()
    first = data_snapshot.get_snapshot()
    install_price_overrides()
    assert data_snapshot.get_snapshot() is first
    assert not any(m.id == "glm-5.3-flashx" for p in bundled_prices.providers for m in p.models)


def test_a_price_update_keeps_the_overrides(bundled_prices, monkeypatch) -> None:
    downloads = iter([bundled_prices, None])
    monkeypatch.setattr("genai_prices.UpdatePrices.fetch", lambda self: next(downloads))
    for _ in range(2):  # a downloaded price list, then a failed download
        data_snapshot.set_custom_snapshot(OverridingUpdatePrices().fetch())
        assert _price("glm-5.3-flashx", input_tokens=MILLION) == Decimal("0.37")


def test_each_override_is_still_needed(bundled_prices) -> None:
    """Fails once genai-prices has a model's price right, so its override can be removed from prices.toml."""
    for override in price_overrides():
        provider = data_snapshot.find_provider_by_id(bundled_prices.providers, override.provider)
        model = provider.find_model(override.model)
        if model is not None:
            assert model.prices != override.model_price(), f"{override.model}: genai-prices now agrees; drop it"
        # The override applies to the data it corrects.
        patched = with_overrides(bundled_prices)
        found = data_snapshot.find_provider_by_id(patched.providers, override.provider).find_model(override.model)
        assert found.prices == override.model_price()


def test_override_for_matches_policy_model_ids() -> None:
    assert override_for("zai:glm-5.3-flashx").output == Decimal("1.25")
    assert override_for("zai:glm-5.3") is None
    assert override_for("openai:glm-5.3-flash") is None
