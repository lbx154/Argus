from __future__ import annotations

import pytest

from argus_skill.core.pricing import usd_for_tokens


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
