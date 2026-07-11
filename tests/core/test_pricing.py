from __future__ import annotations

import pytest

from argus_skill.core.pricing import (
    model_price_for,
    quote_token_usage,
    usd_for_tokens,
)


def test_usd_for_tokens_reasoning_output_tokens_use_output_rate() -> None:
    base = usd_for_tokens("gpt-5.5", 1000, 100, 200)
    with_reasoning = usd_for_tokens(
        "gpt-5.5",
        1000,
        100,
        200,
        reasoning_output_tokens=50,
    )
    assert with_reasoning == pytest.approx(base + ((50 * 10.0) / 1_000_000))


def test_usd_for_tokens_reasoning_output_tokens_default_is_backward_compatible() -> None:
    explicit_zero = usd_for_tokens(
        "gpt-5.5-mini",
        500,
        50,
        75,
        reasoning_output_tokens=0,
    )
    omitted = usd_for_tokens("gpt-5.5-mini", 500, 50, 75)
    assert explicit_zero == pytest.approx(omitted)


def test_gpt_5_6_sol_official_default_price_golden() -> None:
    price = model_price_for("gpt-5.6-sol")
    assert price is not None
    assert price.input_usd_per_mtok == 5.0
    assert price.cached_input_usd_per_mtok == 0.5
    assert price.output_usd_per_mtok == 30.0
    quote = quote_token_usage(
        "gpt-5.6-sol",
        input_tokens=100_000,
        cached_input_tokens=20_000,
        output_tokens=10_000,
    )
    assert quote.status == "priced"
    assert quote.tier == "default"
    assert quote.cost_usd == pytest.approx(0.71)


def test_gpt_5_6_sol_official_long_context_price_golden() -> None:
    quote = quote_token_usage(
        "gpt-5.6-sol",
        input_tokens=300_000,
        cached_input_tokens=100_000,
        output_tokens=10_000,
    )
    assert quote.status == "priced"
    assert quote.tier == "long_context"
    assert quote.cost_usd == pytest.approx(2.55)
    assert usd_for_tokens(
        "gpt-5.6-sol",
        300_000,
        100_000,
        10_000,
    ) == pytest.approx(2.55)


def test_gpt_5_6_sol_long_context_threshold_is_strictly_over_272k() -> None:
    at_threshold = quote_token_usage(
        "gpt-5.6-sol",
        input_tokens=272_000,
        cached_input_tokens=0,
        output_tokens=0,
    )
    over_threshold = quote_token_usage(
        "gpt-5.6-sol",
        input_tokens=272_001,
        cached_input_tokens=0,
        output_tokens=0,
    )
    assert at_threshold.tier == "default"
    assert over_threshold.tier == "long_context"
