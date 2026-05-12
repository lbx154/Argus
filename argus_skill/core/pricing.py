"""Shared token pricing helpers."""
from __future__ import annotations

from collections.abc import Callable

PriceLookup = Callable[[str], tuple[float, float]]

DEFAULT_PRICES_USD_PER_MTOK: dict[str, tuple[float, float]] = {
    "gpt-5.4": (1.25, 10.0),
    "gpt-5.4-mini": (0.25, 2.0),
    "gpt-5.2": (1.25, 10.0),
    "gpt-5.2-codex": (1.25, 10.0),
}


def price_for(model: str, *, default: str = "gpt-5.4-mini") -> tuple[float, float]:
    """USD per million ``(input, output)`` tokens for ``model``."""
    if not model:
        return DEFAULT_PRICES_USD_PER_MTOK[default]
    if model in DEFAULT_PRICES_USD_PER_MTOK:
        return DEFAULT_PRICES_USD_PER_MTOK[model]
    if "mini" in model:
        return DEFAULT_PRICES_USD_PER_MTOK["gpt-5.4-mini"]
    return DEFAULT_PRICES_USD_PER_MTOK["gpt-5.4"]


def usd_for_tokens(
    model: str,
    input_tokens: int,
    cached_input_tokens: int,
    output_tokens: int,
    *,
    price_lookup: PriceLookup = price_for,
) -> float:
    """Compute USD with cache-aware input pricing."""
    in_price, out_price = price_lookup(model)
    cached = max(0, min(int(cached_input_tokens or 0), max(0, int(input_tokens or 0))))
    fresh = max(0, int(input_tokens or 0) - cached)
    return (
        (fresh * in_price)
        + (cached * (in_price / 10.0))
        + (max(0, int(output_tokens or 0)) * out_price)
    ) / 1_000_000

