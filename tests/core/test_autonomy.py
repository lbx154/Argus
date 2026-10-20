from __future__ import annotations

import pytest

from argus.core.autonomy import operator_available


@pytest.mark.parametrize("value", ["false", "0", "off", "no"])
def test_operator_availability_knob(value: str) -> None:
    assert operator_available(env={"ARGUS_SKILL_OPERATOR_AVAILABLE": value}) is False


def test_operator_is_available_by_default() -> None:
    assert operator_available(env={}) is True
    assert operator_available(env={"ARGUS_SKILL_OPERATOR_AVAILABLE": "true"}) is True
