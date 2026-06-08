"""Regression: model-supplied boolean-ish JSON values must be coerced safely.

``bool("false")`` is ``True`` in Python, so a model that emits the string
``"false"`` for a flag like ``resolved`` would otherwise be read as true.
"""
from __future__ import annotations

from argus_skill.tools.subagent import _coerce_bool


def test_coerce_bool_string_false_is_false():
    assert _coerce_bool("false") is False
    assert _coerce_bool("False") is False
    assert _coerce_bool("0") is False
    assert _coerce_bool("no") is False
    assert _coerce_bool("") is False


def test_coerce_bool_string_true_is_true():
    assert _coerce_bool("true") is True
    assert _coerce_bool("True") is True
    assert _coerce_bool("1") is True
    assert _coerce_bool("yes") is True


def test_coerce_bool_native_types():
    assert _coerce_bool(True) is True
    assert _coerce_bool(False) is False
    assert _coerce_bool(1) is True
    assert _coerce_bool(0) is False


def test_coerce_bool_unknown_uses_default():
    assert _coerce_bool(None) is False
    assert _coerce_bool("maybe") is False
    assert _coerce_bool("maybe", default=True) is True
    assert _coerce_bool({"x": 1}) is False
