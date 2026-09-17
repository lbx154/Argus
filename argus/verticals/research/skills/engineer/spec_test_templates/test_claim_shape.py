"""Claim-shaped tests for complexity, memory and throughput claims.

For every such claim in METHOD.md, measure at three or more scales spanning
a decade and assert the log-log slope matches the claimed order. For an
accuracy claim, the test is the positive control: a case with a known
recoverable signal through the same executed path.
"""
from __future__ import annotations

import numpy as np
import pytest
from conftest import scaling_slope, wall_time

# TODO: import the implementation entry point.

SIZES = [256, 1024, 4096, 16384]  # TODO: sizes the claim covers; at least one decade
SLOPE_MARGIN = 0.35


@pytest.mark.component("component one", kind="claim")  # TODO: the component the claim is about
def test_time_scales_as_claimed() -> None:
    """METHOD.md claims O(n) time (TODO: state the claimed order)."""
    pytest.skip("TODO: replace the measure with the real entry point")

    def measure(size: int) -> float:
        x = np.zeros((size, 16))
        return wall_time(lambda: x)  # TODO: implementation(x)

    slope = scaling_slope(measure, SIZES)
    assert abs(slope - 1.0) < SLOPE_MARGIN, f"measured slope {slope:.2f}"


@pytest.mark.component("component one", kind="claim")  # TODO: the component the claim is about
def test_memory_scales_as_claimed() -> None:
    """METHOD.md claims O(n) peak memory (TODO: state the claimed order)."""
    pytest.skip("TODO: measure peak bytes per size, e.g. tracemalloc or the accelerator's peak counter")


@pytest.mark.component("component one", kind="claim")  # TODO: the component the claim is about
def test_positive_control_recovers_known_signal(rng: np.random.Generator) -> None:
    """A planted signal the method must recover is recovered through the real path."""
    pytest.skip("TODO: plant a known signal and run the public entry point on it")
