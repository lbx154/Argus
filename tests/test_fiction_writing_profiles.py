"""Fiction genre profiles change the brief and remain intake-validatable.

A profile is not a new vertical and not a dead data file: its knobs are distinct
per profile, it is carried into the brief, and an unknown profile is rejected.
"""
from __future__ import annotations

import pytest

from argus_skill.verticals.fiction_writing.intake import brief_from_envelope
from argus_skill.verticals.fiction_writing.profiles import (
    DEFAULT_PROFILE,
    FICTION_PROFILES,
    FictionProfileError,
    resolve_profile,
)


def test_resolve_known_default_unknown():
    assert resolve_profile("web_fiction")["name"] == "web_fiction"
    assert resolve_profile(None)["name"] == DEFAULT_PROFILE
    assert resolve_profile("")["name"] == DEFAULT_PROFILE
    with pytest.raises(FictionProfileError, match="unknown fiction profile"):
        resolve_profile("cyberpunk_haiku")


def test_profiles_are_distinct_not_a_dead_file():
    web = resolve_profile("web_fiction")
    lit = resolve_profile("literary_fiction")
    assert web["pacing"] != lit["pacing"]
    assert web["exposition_tolerance"] == "high"
    assert lit["exposition_tolerance"] == "low"
    assert web["chapter_hooks"] == "required"
    assert lit["character_complexity"] == "high"
    assert web["reviewer_emphasis"] != lit["reviewer_emphasis"]
    assert len(FICTION_PROFILES) == 5


def _env(**kw):
    base = {"task_id": "f1", "mode": "from_scratch", "language": "zh",
            "form": "chapter", "intent": "写一章"}
    base.update(kw)
    return base


def test_brief_carries_profile_default_and_named():
    assert brief_from_envelope(_env())["profile"]["name"] == DEFAULT_PROFILE
    b = brief_from_envelope(_env(output_requirements={"profile": "literary_fiction"}))
    assert b["profile"]["name"] == "literary_fiction"
    assert b["profile"]["character_complexity"] == "high"


def test_unknown_profile_rejected_at_intake():
    with pytest.raises(Exception):
        brief_from_envelope(_env(output_requirements={"profile": "bogus"}))


def test_profile_does_not_bypass_intake_contract():
    with pytest.raises(Exception):
        brief_from_envelope(_env(form="quatrain",
                                 output_requirements={"profile": "web_fiction"}))
