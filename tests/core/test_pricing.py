from __future__ import annotations

import pytest

from argus.core.pricing import (
    model_price_for,
    quote_token_usage,
)


def test_gpt_6_1_sol_official_price_golden() -> None:
    """Standard $2.00 / $0.10 / $10.00 per MTok; above 272K input $4.00 / $0.20 / $15.00."""
    price = model_price_for("gpt-6.1-sol")
    assert price is not None
    assert price.input_usd_per_mtok == 2.0
    assert price.cached_input_usd_per_mtok == 0.1
    assert price.output_usd_per_mtok == 10.0
    quote = quote_token_usage(
        "gpt-6.1-sol",
        input_tokens=100_000,
        cached_input_tokens=20_000,
        output_tokens=10_000,
    )
    assert quote.status == "priced" and quote.tier == "default"
    assert quote.cost_usd == pytest.approx(0.262)
    long_quote = quote_token_usage(
        "gpt-6.1-sol",
        input_tokens=300_000,
        cached_input_tokens=100_000,
        output_tokens=10_000,
    )
    assert long_quote.tier == "long_context"
    # 200K fresh at $4, 100K cached at $0.20, 10K output at $15.
    assert long_quote.cost_usd == pytest.approx(0.8 + 0.02 + 0.15)
    assert model_price_for("gpt-6.1-sol-2026-10-01") == price
