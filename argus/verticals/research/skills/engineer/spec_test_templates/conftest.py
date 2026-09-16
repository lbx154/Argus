"""Shared helpers for the executable spec under ``tests/spec``.

Copy this directory to ``tests/spec/`` in the project, replace every TODO,
and delete the ``pytest.skip`` lines as each test becomes real. Plain pytest
and numpy only: the oracle must run without the method's framework.

Every test carries ``@pytest.mark.component("<component>", kind=...)`` naming
the METHOD.md component it exercises. The hook below records the markers in
``<rootdir>/.argus/spec_components.json``; the host joins that file with its
own run of this suite to derive each component's status (proven, contradicted,
partial, untested, unchecked) for the Reviewer and the Atlas web UI. Nothing
here blocks anything and nothing about status is written by hand.
"""
from __future__ import annotations

import contextlib
import json
import time
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path

import numpy as np
import pytest

COMPONENT_MARKER = "component"
SPEC_COMPONENTS_FILE = Path(".argus") / "spec_components.json"
KINDS = ("knockout", "differential", "invariant", "claim", "parity")
_KIND_BY_FILE = {
    "test_knockouts.py": "knockout",
    "test_differential.py": "differential",
    "test_claim_shape.py": "claim",
    "test_parity_reference.py": "parity",
}


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "component(name, kind=None): the METHOD.md component this test exercises; kind is "
        "one of knockout, differential, invariant, claim, parity (inferred from the file "
        "name when omitted)",
    )


def _marker_kind(marker: pytest.Mark, filename: str) -> str:
    kind = marker.kwargs.get("kind") or (marker.args[1] if len(marker.args) > 1 else None)
    if kind in KINDS:
        return str(kind)
    return _KIND_BY_FILE.get(filename, "invariant")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Record ``{nodeid: {component, kind}}`` for the host to join with its run.

    Fail-soft: a write error must never fail collection.
    """
    records: dict[str, dict[str, str]] = {}
    for item in items:
        marker = item.get_closest_marker(COMPONENT_MARKER)
        if marker is None:
            continue
        name = marker.args[0] if marker.args else marker.kwargs.get("name")
        if not name:
            continue
        records[item.nodeid] = {
            "component": str(name),
            "kind": _marker_kind(marker, Path(str(item.path)).name),
        }
    try:
        target = Path(str(config.rootpath)) / SPEC_COMPONENTS_FILE
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps({"generated_at": time.time(), "items": records}, indent=2, sort_keys=True),
            encoding="utf-8",
        )
    except Exception:  # noqa: BLE001 - recording markers is a convenience, not a gate
        pass

# TODO: import the implementation and the oracle here, for example
#   from mymethod import model
#   from tests.spec.oracle import equation_3_attention, equation_5_loss

RTOL = 1e-6
ATOL = 1e-8


@pytest.fixture
def rng() -> np.random.Generator:
    """One seeded generator per test so failures reproduce."""
    return np.random.default_rng(20260916)


@contextlib.contextmanager
def knockout(obj: object, attribute: str, replacement: object) -> Iterator[None]:
    """Disable one component for the duration of a ``with`` block.

    ``replacement`` is whatever makes the component do nothing: ``False`` for a
    flag, ``0.0`` for a weight, an identity function for a transform. The
    original value is restored afterwards.
    """
    missing = object()
    original = getattr(obj, attribute, missing)
    setattr(obj, attribute, replacement)
    try:
        yield
    finally:
        if original is missing:
            delattr(obj, attribute)
        else:
            setattr(obj, attribute, original)


def assert_changes(
    baseline: np.ndarray,
    knocked_out: np.ndarray,
    *,
    min_relative_change: float = 1e-3,
) -> None:
    """The knockout must move the output by more than numerical noise.

    A component whose removal leaves the output unchanged is not doing what
    METHOD.md says it does; the case handed to the knockout test must be
    built so the component matters.
    """
    baseline = np.asarray(baseline, dtype=np.float64)
    knocked_out = np.asarray(knocked_out, dtype=np.float64)
    scale = max(float(np.max(np.abs(baseline))), 1e-12)
    change = float(np.max(np.abs(baseline - knocked_out))) / scale
    assert change > min_relative_change, (
        f"knockout changed the output by {change:.3g} relative, below "
        f"{min_relative_change:.3g}: the component is not exercised by this case"
    )


def scaling_slope(
    measure: Callable[[int], float],
    sizes: Sequence[int],
    *,
    repeats: int = 3,
) -> float:
    """Fit the log-log slope of ``measure(size)`` across ``sizes``.

    ``measure`` returns a cost (seconds, bytes, operation count) for one
    size; the minimum over ``repeats`` is used per size so that background
    noise inflates nothing. A claim of O(n) is a slope near 1, O(n log n)
    slightly above 1, O(n^2) near 2. Use at least three sizes spanning at
    least one decade.
    """
    if len(sizes) < 3:
        raise ValueError("scaling_slope needs at least three sizes")
    costs = [
        min(float(measure(int(size))) for _ in range(repeats)) for size in sizes
    ]
    xs = np.log(np.asarray(sizes, dtype=np.float64))
    ys = np.log(np.maximum(np.asarray(costs, dtype=np.float64), 1e-12))
    slope, _intercept = np.polyfit(xs, ys, 1)
    return float(slope)


def wall_time(fn: Callable[[], object]) -> float:
    """Seconds taken by one call of ``fn``; pair with ``scaling_slope``."""
    start = time.perf_counter()
    fn()
    return time.perf_counter() - start
