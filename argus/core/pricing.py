"""Shared token pricing helpers."""
from __future__ import annotations

import json
import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Literal

from .contract_resources import contract_schema_path
from .token_usage import TokenUsage

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


# Both runtimes use the same reference rates; packaged wheels carry this JSON.
_PRICE_CATALOG = json.loads(contract_schema_path("model_pricing.json").read_text(encoding="utf-8"))
MODEL_PRICES_USD_PER_MTOK: dict[str, ModelPrice] = {
    name: ModelPrice(**price) for name, price in _PRICE_CATALOG["models"].items()
}
_COPILOT_DEFAULT_PRICE: float = _PRICE_CATALOG["copilot_usd_per_premium_request"]

DEFAULT_PRICES_USD_PER_MTOK: dict[str, tuple[float, float]] = {
    name: (price.input_usd_per_mtok, price.output_usd_per_mtok)
    for name, price in MODEL_PRICES_USD_PER_MTOK.items()
}


def price_for(model: str, *, default: str = "gpt-6.1-sol") -> tuple[float, float]:
    """USD per million ``(input, output)`` tokens for ``model``."""
    if not model:
        return DEFAULT_PRICES_USD_PER_MTOK[default]
    if model in DEFAULT_PRICES_USD_PER_MTOK:
        return DEFAULT_PRICES_USD_PER_MTOK[model]
    if "mini" in model:
        return DEFAULT_PRICES_USD_PER_MTOK["gpt-5.5-mini"]
    return DEFAULT_PRICES_USD_PER_MTOK[default]


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
    cache_write_tokens: int | None = None,
) -> PricingQuote:
    """Strictly price one token-based request, preserving partial/unpriced states.

    Reasoning tokens are a part of ``output_tokens`` (codex's ``total_tokens``
    is input plus output), so they are never billed again.
    """
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
        + output_count * price.output_usd_per_mtok * output_multiplier
    ) / 1_000_000
    complete = input_tokens is not None and output_tokens is not None
    return PricingQuote(
        cost_usd=cost,
        status="priced" if complete else "partial",
        tier="long_context" if long_context else "default",
        reason="" if complete else "input or output token count is missing",
    )


def quote_requests(model: str, requests: Sequence[TokenUsage]) -> PricingQuote:
    """Price a call request by request: the long-context tier belongs to one
    request's prompt, never to a call's summed input (2026-10-27: 277
    requests of at most 240K tokens were billed as one 43.9M-token prompt,
    $12.68 for $6.28). A count a request did not report stays missing."""
    quotes = [
        quote_token_usage(
            model,
            **{
                name: getattr(usage, name) if getattr(usage, f"{name}_present") else None
                for name in ("input_tokens", "cached_input_tokens", "cache_write_tokens", "output_tokens")
            },
        )
        for usage in requests
    ]
    unsettled = [quote for quote in quotes if quote.status != "priced"]
    if len(quotes) == 1 or unsettled:
        return (unsettled or quotes)[0]
    return PricingQuote(
        cost_usd=sum(quote.cost_usd or 0.0 for quote in quotes),
        status="priced",
        tier="long_context" if any(quote.tier == "long_context" for quote in quotes) else "default",
    )


def copilot_usd_per_premium_request() -> float:
    raw = os.environ.get("ARGUS_SKILL_COPILOT_USD_PER_PREMIUM_REQUEST", "").strip()
    if not raw:
        return _COPILOT_DEFAULT_PRICE
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return _COPILOT_DEFAULT_PRICE
    return value if value >= 0.0 else _COPILOT_DEFAULT_PRICE


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
    price_lookup: PriceLookup = price_for,
) -> float:
    """Compute USD with cache-aware input pricing."""
    if price_lookup is price_for:
        strict = quote_token_usage(
            model,
            input_tokens=input_tokens,
            cached_input_tokens=cached_input_tokens,
            cache_write_tokens=0,
            output_tokens=output_tokens,
        )
        if strict.cost_usd is not None:
            return strict.cost_usd
    in_price, out_price = price_lookup(model)
    cached = max(0, min(int(cached_input_tokens or 0), max(0, int(input_tokens or 0))))
    fresh = max(0, int(input_tokens or 0) - cached)
    return (
        (fresh * in_price)
        + (cached * (in_price / 10.0))
        + max(0, int(output_tokens or 0)) * out_price
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
    "quote_requests",
    "quote_token_usage",
    "usd_for_tokens",
]
