"""Baseline parity against the pinned reference under ``third_party/``.

The project's implementation of the baseline path reproduces the reference
implementation's output on a fixed input within a stated tolerance. Until it
passes, the host shows the baseline component as partial or contradicted. The
reference is used as cloned at a pinned revision, never edited in place; the
host reads the revision and remote from the clone itself.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from conftest import ATOL, RTOL

REFERENCE_DIR = Path(__file__).resolve().parents[2] / "third_party"  # TODO: / "<name>"
PINNED_REVISION = "TODO: commit hash or tag the clone was checked out at"


@pytest.mark.component("baseline", kind="parity")  # TODO: the baseline row in METHOD.md
def test_reference_is_present_at_pinned_revision() -> None:
    """The clone exists and is at the pinned revision."""
    pytest.skip("TODO: point REFERENCE_DIR at the clone and check its revision")
    assert REFERENCE_DIR.is_dir(), f"missing reference clone at {REFERENCE_DIR}"


@pytest.mark.component("baseline", kind="parity")  # TODO: the baseline row in METHOD.md
def test_baseline_matches_reference_output(rng: np.random.Generator) -> None:
    """Our baseline path equals the reference on a fixed input."""
    pytest.skip("TODO: load the reference, run both on the same input, compare")
    x = rng.standard_normal((16, 16))
    expected = x  # TODO: reference forward on x (or a stored output from its example run)
    actual = x  # TODO: our baseline forward on x
    np.testing.assert_allclose(actual, expected, rtol=RTOL, atol=ATOL)
