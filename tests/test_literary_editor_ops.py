"""literary_editor EDIT-DISCIPLINE checks — the deterministic machine layer.

Each editing mode's discipline has a real negative: a critique that rewrites, a
proofread that becomes a rewrite, an expand that does not add, a dropped must-keep
segment. Break the rule and the check fires. Whether the edit is GOOD is not here.
"""
from __future__ import annotations

import pytest

from argus_skill.verticals.literary_editor.edit_ops import (
    EDIT_FINDING_TYPES,
    EDITOR_MODES,
    EditError,
    check_edit,
    is_disciplined,
)

_SRC = "这是一段需要校对的文字，里面藏着一个关键句，还有个错别字。"


def _types(*a, **k):
    return {f["type"] for f in check_edit(*a, **k)}


def test_proofread_small_fix_passes_rewrite_fails():
    fixed = "这是一段需要校对的文字，里面藏着一个关键句，还有一个错别字。"
    assert is_disciplined(_SRC, fixed, "proofread")
    # a wholesale rewrite is not a proofread
    rewrite = "完全不同的另一段话，讲的是别的事情，毫无关系。"
    assert "over_edit" in _types(_SRC, rewrite, "proofread")


def test_critique_must_not_rewrite():
    assert is_disciplined(_SRC, _SRC, "critique")           # unchanged: ok
    assert "mode_discipline" in _types(_SRC, _SRC + "改了", "critique")


def test_expand_must_add_material():
    longer = _SRC + "这里补充了一整段新的描写，让画面更完整。"
    assert is_disciplined(_SRC, longer, "expand")
    assert "no_expansion" in _types(_SRC, _SRC, "expand")    # not longer


def test_must_keep_segment_preserved():
    kept = "改写后的文字，但仍然保留了那个关键句在里面。"
    assert is_disciplined(_SRC, kept, "rewrite", must_keep=["关键句"])
    dropped = "改写后的文字，把该保留的东西弄丢了。"
    assert "must_not_break" in _types(_SRC, dropped, "rewrite", must_keep=["关键句"])


def test_empty_edit_flagged_except_critique():
    assert "empty" in _types(_SRC, "   ", "polish")
    # critique mirrors the source, so empty edited is not an 'empty' finding there
    assert is_disciplined(_SRC, _SRC, "critique")


def test_unknown_mode_raises():
    with pytest.raises(EditError, match="unknown editing mode"):
        check_edit(_SRC, _SRC, "translate")


def test_vocabulary_and_modes():
    assert EDIT_FINDING_TYPES == {
        "must_not_break", "mode_discipline", "over_edit", "no_expansion", "empty"}
    assert EDITOR_MODES == {"rewrite", "expand", "polish", "proofread", "critique"}
