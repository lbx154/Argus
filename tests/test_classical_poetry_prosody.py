"""Machine prosody checks for classical_poetry — the crown deterministic layer.

Reproducible, char-by-char against the 平水韵 table: a compliant 近体诗 passes; an
out-of-rhyme foot, a 失替 at a 分明位, and a 三平尾 are each detected as blocking
findings. If the engine were gutted to always return compliant, the negatives here
go red. Multi-tone chars are not false-flagged; a non-近体 shape is rhyme-only.
"""
from __future__ import annotations

import pytest

from argus_skill.verticals.classical_poetry.prosody import (
    PROSODY_FINDING_TYPES,
    ProsodyError,
    analyze,
)

_DENG = "白日依山尽，黄河入海流。欲穷千里目，更上一层楼。"  # 王之涣·登鹳雀楼


def _blocking_types(text):
    return {f["type"] for f in analyze(text)["findings"] if f["severity"] == "blocking"}


def test_compliant_poem_passes():
    r = analyze(_DENG)
    assert r["is_jinti"] and r["compliant"]
    assert r["rhyme_group"] == "十一尤"
    assert not [f for f in r["findings"] if f["severity"] == "blocking"]


def test_out_of_rhyme_detected():
    # 末句换到不同韵部 -> 出韵
    bad = "白日依山尽，黄河入海流。欲穷千里目，更上一层村。"
    r = analyze(bad)
    assert not r["compliant"]
    assert "rhyme" in _blocking_types(bad)


def test_meter_failure_detected():
    # 打乱平仄，分明位失替
    bad = "月落乌啼霜，孤舟夜泊船。江枫渔火对，愁眠一夜天。"
    r = analyze(bad)
    assert not r["compliant"]
    assert "meter" in _blocking_types(bad)


def test_three_ping_tail_is_a_hard_fault():
    # 首句「依山高」三平尾
    bad = "白日依山高，黄河入海天。欲穷千里目，更上一层云。"
    types = _blocking_types(bad)
    assert "hard_fault" in types
    assert any("三平尾" in f["detail"] for f in analyze(bad)["findings"])


def test_non_jinti_shape_is_rhyme_only():
    six = "一二三四五，六七八九十。春夏与秋冬，东西复南北。天地何辽阔，古今几沉浮。"
    r = analyze(six)
    assert r["is_jinti"] is False
    # a 仄声 rhyme foot is still caught, but meter谱 is not applied
    assert all(f["type"] == "rhyme" for f in r["findings"] if f["severity"] == "blocking")


def test_empty_input_raises():
    with pytest.raises(ProsodyError):
        analyze("no chinese here 123 ...")


def test_finding_type_vocabulary():
    assert PROSODY_FINDING_TYPES == {"rhyme", "meter", "hard_fault", "parallelism"}
