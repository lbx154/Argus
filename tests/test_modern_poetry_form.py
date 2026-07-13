"""modern_poetry FORM checks — the honest, thin deterministic layer.

Only DECLARED hard constraints are machine-checked (language, line count, banned
cliché list, non-empty). Each has a real negative; there is deliberately NO
aesthetic gate. If the checker were gutted the negatives here go red.
"""
from __future__ import annotations

import pytest

from argus_skill.verticals.modern_poetry.form import (
    FORM_FINDING_TYPES,
    check_form,
    is_compliant,
)

_POEM = "夜把城市折起来\n只留一盏灯\n和灯下没说完的话"


def test_no_spec_only_requires_non_empty():
    assert is_compliant(_POEM, {})
    assert is_compliant(_POEM, None)


def test_empty_poem_flagged():
    fs = check_form("   \n  \n", {})
    assert any(f["type"] == "empty_line" for f in fs)


def test_line_count_violation_detected():
    fs = check_form(_POEM, {"line_count": 5})
    assert any(f["type"] == "line_count" for f in fs)
    assert not is_compliant(_POEM, {"line_count": 5})
    assert is_compliant(_POEM, {"line_count": 3})


def test_min_max_lines():
    assert any(f["type"] == "line_count" for f in check_form(_POEM, {"min_lines": 5}))
    assert any(f["type"] == "line_count" for f in check_form(_POEM, {"max_lines": 2}))


def test_banned_cliche_detected():
    fs = check_form(_POEM, {"banned_words": ["灯下"]})
    assert any(f["type"] == "banned_word" for f in fs)
    assert not is_compliant(_POEM, {"banned_words": ["灯下"]})


def test_language_mismatch_detected():
    fs = check_form("just english words here\nno chinese at all", {"language": "zh"})
    assert any(f["type"] == "language" for f in fs)
    fs2 = check_form(_POEM, {"language": "en"})
    assert any(f["type"] == "language" for f in fs2)


def test_finding_type_vocabulary():
    assert FORM_FINDING_TYPES == {"language", "line_count", "banned_word", "empty_line"}
