"""Implementation versus oracle.

The oracle is the route's equations transcribed into slow float64 functions,
one per equation, with no code shared with the implementation. Tag each test
with the METHOD.md component whose equation it checks; a component is derived
as ``proven`` only once a knockout or differential test tagged with it passes.
"""
from __future__ import annotations

import numpy as np
import pytest
from conftest import ATOL, RTOL

# TODO: import the implementation entry point and the oracle functions.


@pytest.mark.component("component one", kind="differential")  # TODO: name as in METHOD.md
@pytest.mark.parametrize("size", [8, 64, 512])
def test_forward_matches_oracle(rng: np.random.Generator, size: int) -> None:
    """The implementation reproduces the oracle on random inputs at several sizes."""
    pytest.skip("TODO: replace with the implementation and oracle calls")
    x = rng.standard_normal((size, 16))
    expected = x  # TODO: oracle_equation_1(x)
    actual = x  # TODO: implementation(x)
    np.testing.assert_allclose(actual, expected, rtol=RTOL, atol=ATOL)


@pytest.mark.component("training objective", kind="differential")  # TODO: name as in METHOD.md
def test_loss_matches_oracle(rng: np.random.Generator) -> None:
    """The training objective equals the route's loss equation on a fixed batch."""
    pytest.skip("TODO: replace with the loss implementation and oracle_equation_N")


@pytest.mark.component("update rule", kind="differential")  # TODO: name as in METHOD.md
def test_update_step_matches_oracle(rng: np.random.Generator) -> None:
    """One update from a fixed state lands where the route's update rule says."""
    pytest.skip("TODO: replace with one step of the real optimiser path")
