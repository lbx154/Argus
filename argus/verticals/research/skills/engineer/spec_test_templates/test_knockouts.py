"""One knockout per component in METHOD.md.

Disable the component and, on a case built so that the component matters,
assert the output changes by more than noise. A knockout that does not move
the output means the component is not doing what the card says.

Tag each test with the component's name exactly as METHOD.md spells it; the
host derives the component's status from the marker and the run.
"""
from __future__ import annotations

import numpy as np
import pytest
from conftest import assert_changes, knockout

# TODO: import the implementation and name each component's switch.


def _case_where_component_matters(rng: np.random.Generator) -> np.ndarray:
    """Build an input on which the component under test changes the answer.

    Random inputs often do not: a gating term that is nearly always open, a
    correction that vanishes at zero mean. Construct the case deliberately.
    """
    return rng.standard_normal((32, 16))  # TODO: shape the case for the component


@pytest.mark.component("component one", kind="knockout")  # TODO: name as in METHOD.md
def test_knockout_component_one(rng: np.random.Generator) -> None:
    """METHOD.md row 'component one': disabling it changes the output."""
    pytest.skip("TODO: wire the real implementation and its component switch")
    model = None  # TODO: implementation instance
    x = _case_where_component_matters(rng)
    baseline = model(x)
    with knockout(model, "use_component_one", False):
        knocked_out = model(x)
    assert_changes(baseline, knocked_out)


@pytest.mark.component("component two", kind="knockout")  # TODO: name as in METHOD.md
def test_knockout_component_two(rng: np.random.Generator) -> None:
    """METHOD.md row 'component two': disabling it changes the output."""
    pytest.skip("TODO: one knockout per component row in METHOD.md")
