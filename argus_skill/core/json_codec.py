"""Decode JSON for HTTP-facing event records and their persisted read models."""

from __future__ import annotations

import json
import math
from typing import Any


def is_finite_number(value: object) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _reject_constant(value: str) -> Any:
    raise ValueError(f"non-finite JSON number: {value}")


def _finite_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"non-finite JSON number: {value}")
    return number


def loads_finite_json(value: str | bytes | bytearray) -> Any:
    """Reject NaN/Infinity and overflowing exponents before they reach a view.

    Python's default decoder accepts these values, which can break strict HTTP
    serialization or become null timestamps in response adapters. Callers handle
    ValueError just like a bad JSON line; the original audit file is not modified.
    """
    return json.loads(value, parse_constant=_reject_constant, parse_float=_finite_float)
