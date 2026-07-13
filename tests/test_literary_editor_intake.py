"""literary_editor intake — consumes the shared Task Envelope (fifth consumer)."""
from __future__ import annotations

import pytest

from argus_skill.verticals.literary_editor.intake import (
    EditorIntakeError,
    brief_from_envelope,
)


def _env(**kw):
    base = {
        "task_id": "e1", "mode": "polish", "language": "zh",
        "form": "short_story", "intent": "润色这段文字",
        "reference_inputs": [{"role": "source_text", "ref": "draft.md"}],
    }
    base.update(kw)
    return base


def test_editing_envelope_becomes_brief():
    brief = brief_from_envelope(_env(
        output_requirements={"must_not_break": ["主角的动机"]}))
    assert brief["mode"] == "polish"
    assert brief["must_keep"] == ["主角的动机"]
    assert brief["source_ref"] == "draft.md"


def test_expand_allows_new_facts_polish_does_not():
    ex = brief_from_envelope(_env(mode="expand"))
    assert ex["allow_new_facts"] is True
    po = brief_from_envelope(_env(mode="polish"))
    assert po["allow_new_facts"] is False


def test_from_scratch_mode_rejected():
    with pytest.raises(EditorIntakeError, match="editing modes"):
        brief_from_envelope(_env(mode="from_scratch",
                                 reference_inputs=[]))


def test_editing_mode_without_source_rejected():
    # the shared envelope rule already requires a source for editing modes
    with pytest.raises(Exception):
        brief_from_envelope(_env(reference_inputs=[]))
