"""Local corrections to the genai-prices data PydanticAI prices every call with.

genai-prices has no price for some models and a wrong one for others. A model without a price
has no cost_usd, so no cost cap holds on it; a model priced too low lets a cap through at a
multiple of what it names. `prices.toml` lists the corrections, and `install_price_overrides`
applies them to the process-wide price data. `AsyncResearchLoop` and `research-diagnose` call
it, so every run and every price check sees the corrected prices.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import tomllib
from dataclasses import dataclass
from decimal import Decimal
from functools import cache
from importlib import resources

from genai_prices import UpdatePrices
from genai_prices.data_snapshot import DataSnapshot, get_snapshot, set_custom_snapshot
from genai_prices.types import ClauseEquals, ModelInfo, ModelPrice


@dataclass(frozen=True)
class PriceOverride:
    """One model's list price per million tokens, and where it came from."""

    provider: str
    model: str
    input: Decimal
    cached_input: Decimal
    output: Decimal
    source: str
    checked: dt.date

    def model_price(self) -> ModelPrice:
        return ModelPrice(input_mtok=self.input, cache_read_mtok=self.cached_input, output_mtok=self.output)


@cache
def price_overrides() -> tuple[PriceOverride, ...]:
    """The corrections in the packaged `prices.toml`."""
    data = tomllib.loads(resources.files(__package__).joinpath("prices.toml").read_text(encoding="utf-8"))
    return tuple(
        PriceOverride(
            provider=item["provider"],
            model=item["model"].lower(),
            input=Decimal(item["input"]),
            cached_input=Decimal(item["cached_input"]),
            output=Decimal(item["output"]),
            source=item["source"],
            checked=item["checked"],
        )
        for item in data["model"]
    )


def override_for(model: str) -> PriceOverride | None:
    """The correction for a `provider:model` ID, if there is one."""
    provider, _, name = model.partition(":")
    return next((o for o in price_overrides() if (o.provider, o.model) == (provider, name.lower())), None)


class _OverriddenSnapshot(DataSnapshot):
    """Price data with the overrides applied, so they are never applied twice."""


def with_overrides(snapshot: DataSnapshot) -> DataSnapshot:
    """A copy of `snapshot` with every override applied; `snapshot` itself is left unchanged.

    A model the data already has keeps its entry, with the override's prices; a model it lacks
    gets a new entry ahead of the provider's others, so no broader match can claim it first.
    """
    providers = list(snapshot.providers)
    for index, provider in enumerate(providers):
        overrides = [o for o in price_overrides() if o.provider == provider.id]
        if not overrides:
            continue
        models = list(provider.models)
        for override in overrides:
            at = next((i for i, m in enumerate(models) if m.is_match(override.model)), None)
            if at is None:
                models.insert(0, ModelInfo(id=override.model, match=ClauseEquals(equals=override.model),
                                           name=override.model, prices=override.model_price()))
            else:
                models[at] = dataclasses.replace(models[at], prices=override.model_price())
        providers[index] = dataclasses.replace(provider, models=models)
    return _OverriddenSnapshot(providers=providers, from_auto_update=snapshot.from_auto_update,
                               timestamp=snapshot.timestamp)


def install_price_overrides() -> None:
    """Apply the overrides to the process-wide price data; a second call changes nothing."""
    current = get_snapshot()
    if not isinstance(current, _OverriddenSnapshot):
        set_custom_snapshot(with_overrides(current))


class OverridingUpdatePrices(UpdatePrices):
    """`UpdatePrices` that keeps the overrides on each price list it downloads.

    A plain `UpdatePrices` replaces the process-wide data with the download, which would drop them.
    """

    def fetch(self) -> DataSnapshot | None:
        fetched = super().fetch()
        if fetched is None:
            # genai-prices would fall back to its bundled data; keep the overrides on that too.
            current = get_snapshot()
            return current if isinstance(current, _OverriddenSnapshot) else with_overrides(current)
        return with_overrides(fetched)
