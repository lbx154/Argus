"""Consumer-side closed-loop tests: fiction intake consumes the Task Envelope.

Proves the shared envelope is ACTUALLY consumed by fiction (not merely defined):
a valid narrative envelope yields a creative_brief; a poetry form is rejected.
"""
from __future__ import annotations

import pytest

from argus_skill.verticals.fiction_writing.intake import (
    FictionIntakeError,
    brief_from_envelope,
)


def test_envelope_becomes_fiction_brief():
    env = {
        "task_id": "t-fic-1",
        "mode": "from_scratch",
        "language": "zh",
        "form": "short_story",
        "intent": "写一个都市悬疑短篇",
        "genre_profile": "suspense",
        "target_length": 400,
        "output_requirements": {"viewpoint": "first", "tense": "past"},
    }
    brief = brief_from_envelope(env)
    assert brief["language"] == "zh"
    assert brief["form"] == "short_story"
    assert brief["mode"] == "from_scratch"
    assert brief["genre"] == "suspense"
    assert brief["length"] == 400
    assert brief["viewpoint"] == "first"
    assert brief["tense"] == "past"


def test_defaults_applied_when_output_requirements_absent():
    env = {
        "task_id": "t-fic-2", "mode": "from_scratch", "language": "en",
        "form": "chapter", "intent": "write chapter one",
    }
    brief = brief_from_envelope(env)
    assert brief["viewpoint"] == "third_limited"
    assert brief["tense"] == "past"
    assert brief["genre"] == "unspecified"


def test_continuation_carries_reference_inputs():
    env = {
        "task_id": "t-fic-3", "mode": "continuation", "language": "en",
        "form": "chapter", "intent": "continue the story",
        "reference_inputs": [{"role": "prior_state", "ref": "story_state.json"}],
    }
    brief = brief_from_envelope(env)
    assert brief["mode"] == "continuation"
    assert brief["reference_inputs"][0]["role"] == "prior_state"


def test_poetry_form_rejected_by_fiction():
    env = {
        "task_id": "t-fic-4", "mode": "from_scratch", "language": "zh",
        "form": "quatrain", "intent": "写一首七言绝句",
    }
    with pytest.raises(FictionIntakeError):
        brief_from_envelope(env)


def test_editing_mode_without_source_still_rejected_through_fiction():
    # the shared semantic rule is enforced even when entered via fiction intake
    env = {
        "task_id": "t-fic-5", "mode": "polish", "language": "zh",
        "form": "short_story", "intent": "润色这篇小说",
    }
    with pytest.raises(Exception):
        brief_from_envelope(env)
