"""Unit tests for the anti-COPY / NOVELTY gate (the '不能抄' layer).

A long verbatim run lifted from the reference is BLOCKING; a short shared
span (idiom/allusion) stays a non-blocking note; a clean continuation that shares
no long run passes; an original with no reference fires nothing; a declared
``novelty_budget`` tightens the gate. Real negatives so gutting the checker goes
red.
"""
from __future__ import annotations

from argus_skill.verticals.fiction_writing.novelty import (
    NOVELTY_FINDING_TYPE,
    check_novelty,
    is_original,
)
from argus_skill.verticals.fiction_writing.style import (
    novelty_budget,
    validate_voice_card,
)

# A source (canon) sentence a continuation must NOT transcribe.
_REF = "黛玉自那日弃舟登岸时，便有荣国府打发了轿子并拉行李的车辆久候了。"

# Lifts a >24-char span verbatim.
_COPY = "次日清晨，黛玉自那日弃舟登岸时，便有荣国府打发了轿子并拉行李的车辆久候了，心下暗想。"

# Same characters/props, wholly re-written sentences — the good case.
_CLEAN = "次日清晨，黛玉才梳洗罢，紫鹃便捧了燕窝进来，说是宝姑娘打发人送来的。"

# Shares a 13-char run (>= note 12, < block 24): a borderline echo, not a lift.
_REF2 = "宝玉听了这话，心中十分欢喜，便叫袭人来。"
_ECHO = "次日，宝玉听了这话，心中十分欢喜，只是不语。"


def test_verbatim_lift_is_blocking_copy():
    findings = check_novelty(_COPY, _REF, {}, "zh")
    blocking = [f for f in findings if f["blocking"]]
    assert blocking
    assert all(f["type"] == NOVELTY_FINDING_TYPE for f in blocking)
    assert not is_original(_COPY, _REF, {}, "zh")
    assert any(f["run_len"] and f["run_len"] >= 24 for f in blocking)


def test_clean_continuation_passes():
    assert check_novelty(_CLEAN, _REF, {}, "zh") == []
    assert is_original(_CLEAN, _REF, {}, "zh")


def test_short_echo_is_nonblocking_note():
    findings = check_novelty(_ECHO, _REF2, {}, "zh")
    runs = [f for f in findings if f.get("run_len")]
    assert runs and all(not f["blocking"] for f in runs)
    assert is_original(_ECHO, _REF2, {}, "zh")
    assert all(f["calibration"] == "model-seed (BCC-pending)" for f in findings)


def test_original_with_no_reference_fires_nothing():
    assert check_novelty(_COPY, "", {}, "zh") == []
    assert is_original(_COPY, "   ", {}, "zh")


def test_declared_max_verbatim_run_tightens_gate():
    card = {"meta": {"language": "zh"}, "novelty_budget": {"max_verbatim_run": 12}}
    # the 13-char echo now exceeds the tightened block threshold
    assert not is_original(_ECHO, _REF2, card, "zh")


def test_declared_overlap_ratio_blocks():
    card = {"meta": {"language": "zh"}, "novelty_budget": {"max_overlap_ratio": 0.3}}
    findings = check_novelty(_ECHO, _REF2, card, "zh")
    assert any(f["blocking"] and "overlap ratio" in f["detail"] for f in findings)


def test_en_verbatim_lift_is_blocking():
    ref = "it was the best of times, it was the worst of times, it was the age of wisdom."
    draft = "She wrote: it was the best of times, it was the worst of times, then stopped."
    assert not is_original(draft, ref, {}, "en")
    clean = "She wrote a line about hope and doubt, crossed it out, and started again."
    assert is_original(clean, ref, {}, "en")


def test_novelty_budget_accessor_is_strict():
    assert novelty_budget({}) == {}
    assert novelty_budget({"novelty_budget": {"max_verbatim_run": 20}}) == {
        "max_verbatim_run": 20}
    # booleans and non-positive values are rejected, never coerced
    assert novelty_budget({"novelty_budget": {"max_verbatim_run": True}}) == {}
    assert novelty_budget({"novelty_budget": {"max_overlap_ratio": 0.25}}) == {
        "max_overlap_ratio": 0.25}


def test_novelty_budget_validates_against_schema():
    validate_voice_card({
        "meta": {"language": "zh"},
        "novelty_budget": {"max_verbatim_run": 20, "max_overlap_ratio": 0.2},
    })
