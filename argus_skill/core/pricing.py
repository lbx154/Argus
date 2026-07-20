"""Shared token pricing helpers."""
from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

PriceLookup = Callable[[str], tuple[float, float]]
PricingStatus = Literal["priced", "partial", "unpriced", "not_billed"]


@dataclass(frozen=True)
class ModelPrice:
    input_usd_per_mtok: float
    cached_input_usd_per_mtok: float
    output_usd_per_mtok: float
    long_context_threshold: int | None = None
    long_input_multiplier: float = 1.0
    long_cached_input_multiplier: float = 1.0
    long_output_multiplier: float = 1.0
    cache_write_multiplier: float = 1.25


@dataclass(frozen=True)
class PricingQuote:
    cost_usd: float | None
    status: PricingStatus
    tier: str
    reason: str = ""


MODEL_PRICES_USD_PER_MTOK: dict[str, ModelPrice] = {
    # Official GPT-5.6 Sol API pricing.  Requests whose input exceeds 272K
    # tokens price the full request at 2x input (including cached input) and
    # 1.5x output.
    "gpt-5.6-sol": ModelPrice(
        input_usd_per_mtok=5.0,
        cached_input_usd_per_mtok=0.5,
        output_usd_per_mtok=30.0,
        long_context_threshold=272_000,
        long_input_multiplier=2.0,
        long_cached_input_multiplier=2.0,
        long_output_multiplier=1.5,
    ),
    "gpt-5.5": ModelPrice(1.25, 0.125, 10.0),
    "gpt-5.5-mini": ModelPrice(0.25, 0.025, 2.0),
    "gpt-5.4": ModelPrice(1.25, 0.125, 10.0),
    "gpt-5.4-mini": ModelPrice(0.25, 0.025, 2.0),
    "gpt-5.2": ModelPrice(1.25, 0.125, 10.0),
    "gpt-5.2-codex": ModelPrice(1.25, 0.125, 10.0),
}

DEFAULT_PRICES_USD_PER_MTOK: dict[str, tuple[float, float]] = {
    name: (price.input_usd_per_mtok, price.output_usd_per_mtok)
    for name, price in MODEL_PRICES_USD_PER_MTOK.items()
}


def price_for(model: str, *, default: str = "gpt-5.5") -> tuple[float, float]:
    """USD per million ``(input, output)`` tokens for ``model``."""
    if not model:
        return DEFAULT_PRICES_USD_PER_MTOK[default]
    if model in DEFAULT_PRICES_USD_PER_MTOK:
        return DEFAULT_PRICES_USD_PER_MTOK[model]
    if "mini" in model:
        return DEFAULT_PRICES_USD_PER_MTOK["gpt-5.5-mini"]
    return DEFAULT_PRICES_USD_PER_MTOK["gpt-5.5"]


def model_price_for(model: str) -> ModelPrice | None:
    """Strict model lookup used by the persistent usage ledger.

    Unlike :func:`price_for`, this never assigns an arbitrary default to an
    unknown model.  Date-stamped variants of a known model inherit that model's
    published price.
    """
    normalized = str(model or "").strip().lower()
    if not normalized:
        return None
    provider, separator, provider_model = normalized.partition("/")
    if separator and provider == "openai" and provider_model:
        normalized = provider_model
    exact = MODEL_PRICES_USD_PER_MTOK.get(normalized)
    if exact is not None:
        return exact
    for name, price in MODEL_PRICES_USD_PER_MTOK.items():
        if normalized.startswith(name + "-"):
            return price
    return None


def quote_token_usage(
    model: str,
    *,
    input_tokens: int | None,
    cached_input_tokens: int | None,
    output_tokens: int | None,
    reasoning_output_tokens: int | None = None,
    cache_write_tokens: int | None = None,
) -> PricingQuote:
    """Strictly price one token-based call, preserving partial/unpriced states."""
    price = model_price_for(model)
    if price is None:
        return PricingQuote(
            cost_usd=None,
            status="unpriced",
            tier="unknown",
            reason=f"no configured price for model {model or '(missing)'}",
        )
    if input_tokens is None and output_tokens is None:
        return PricingQuote(
            cost_usd=None,
            status="partial",
            tier="unknown",
            reason="token usage is missing",
        )

    # GPT-5.6 Sol's output multiplier depends on total prompt length.  With no
    # input count, even an observed output count cannot be priced exactly.
    if input_tokens is None and price.long_context_threshold is not None:
        return PricingQuote(
            cost_usd=None,
            status="partial",
            tier="unknown",
            reason="input tokens missing; long-context tier cannot be selected",
        )

    input_count = max(0, int(input_tokens or 0))
    cached_count = max(
        0,
        min(int(cached_input_tokens or 0), input_count),
    )
    cache_write_count = max(
        0,
        min(int(cache_write_tokens or 0), input_count - cached_count),
    )
    output_count = max(0, int(output_tokens or 0))
    reasoning_count = max(0, int(reasoning_output_tokens or 0))
    long_context = bool(
        price.long_context_threshold is not None
        and input_tokens is not None
        and input_count > price.long_context_threshold
    )
    input_multiplier = price.long_input_multiplier if long_context else 1.0
    cached_multiplier = (
        price.long_cached_input_multiplier if long_context else 1.0
    )
    output_multiplier = price.long_output_multiplier if long_context else 1.0
    fresh_count = input_count - cached_count - cache_write_count
    cost = (
        fresh_count * price.input_usd_per_mtok * input_multiplier
        + cached_count * price.cached_input_usd_per_mtok * cached_multiplier
        + cache_write_count
        * price.input_usd_per_mtok
        * input_multiplier
        * price.cache_write_multiplier
        + (output_count + reasoning_count)
        * price.output_usd_per_mtok
        * output_multiplier
    ) / 1_000_000
    complete = input_tokens is not None and output_tokens is not None
    return PricingQuote(
        cost_usd=cost,
        status="priced" if complete else "partial",
        tier="long_context" if long_context else "default",
        reason="" if complete else "input or output token count is missing",
    )


def copilot_usd_per_premium_request() -> float:
    raw = os.environ.get("ARGUS_SKILL_COPILOT_USD_PER_PREMIUM_REQUEST", "").strip()
    if not raw:
        return 0.04
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 0.04
    return value if value >= 0.0 else 0.04


def quote_copilot_usage(premium_requests: float | None) -> PricingQuote:
    if premium_requests is None:
        return PricingQuote(
            cost_usd=None,
            status="partial",
            tier="premium_request",
            reason="Copilot premium-request usage is missing",
        )
    count = max(0.0, float(premium_requests))
    return PricingQuote(
        cost_usd=count * copilot_usd_per_premium_request(),
        status="priced",
        tier="premium_request",
    )


def usd_for_tokens(
    model: str,
    input_tokens: int,
    cached_input_tokens: int,
    output_tokens: int,
    *,
    reasoning_output_tokens: int = 0,
    price_lookup: PriceLookup = price_for,
) -> float:
    """Compute USD with cache-aware input pricing and output-priced reasoning tokens."""
    if price_lookup is price_for:
        strict = quote_token_usage(
            model,
            input_tokens=input_tokens,
            cached_input_tokens=cached_input_tokens,
            cache_write_tokens=0,
            output_tokens=output_tokens,
            reasoning_output_tokens=reasoning_output_tokens,
        )
        if strict.cost_usd is not None:
            return strict.cost_usd
    in_price, out_price = price_lookup(model)
    cached = max(0, min(int(cached_input_tokens or 0), max(0, int(input_tokens or 0))))
    fresh = max(0, int(input_tokens or 0) - cached)
    return (
        (fresh * in_price)
        + (cached * (in_price / 10.0))
        + (
            max(0, int(output_tokens or 0))
            + max(0, int(reasoning_output_tokens or 0))
        )
        * out_price
    ) / 1_000_000


__all__ = [
    "DEFAULT_PRICES_USD_PER_MTOK",
    "MODEL_PRICES_USD_PER_MTOK",
    "ModelPrice",
    "PricingQuote",
    "PricingStatus",
    "copilot_usd_per_premium_request",
    "model_price_for",
    "price_for",
    "quote_copilot_usage",
    "quote_token_usage",
    "usd_for_tokens",
]
