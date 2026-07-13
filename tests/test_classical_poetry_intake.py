"""classical_poetry intake — consumes the shared Task Envelope (second consumer).

A valid Chinese poetry envelope yields a poem brief; a non-zh envelope and a
narrative form are both rejected loudly at intake.
"""
from __future__ import annotations

import pytest

from argus_skill.verticals.classical_poetry.intake import (
    PoetryIntakeError,
    brief_from_envelope,
)


def _env(**kw):
    base = {"task_id": "p1", "mode": "from_scratch", "language": "zh",
            "form": "五言绝句", "intent": "写一首登高望远的五绝"}
    base.update(kw)
    return base


def test_envelope_becomes_poetry_brief():
    brief = brief_from_envelope(_env(output_requirements={"rhyme_target": "十一尤", "yan": 5}))
    assert brief["language"] == "zh"
    assert brief["form"] == "五言绝句"
    assert brief["is_jinti"] is True
    assert brief["rhyme_target"] == "十一尤"


def test_guti_form_is_not_jinti():
    brief = brief_from_envelope(_env(form="古体诗"))
    assert brief["is_jinti"] is False


def test_non_chinese_language_rejected():
    with pytest.raises(PoetryIntakeError, match="Chinese"):
        brief_from_envelope(_env(language="en"))


def test_narrative_form_rejected():
    with pytest.raises(PoetryIntakeError, match="does not handle form"):
        brief_from_envelope(_env(form="short_story"))
