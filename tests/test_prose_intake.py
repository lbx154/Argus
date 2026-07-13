"""prose intake — consumes the shared Task Envelope (fourth consumer)."""
from __future__ import annotations

import pytest

from argus_skill.verticals.prose.intake import ProseIntakeError, brief_from_envelope


def _env(**kw):
    base = {"task_id": "pr1", "mode": "from_scratch", "language": "zh",
            "form": "抒情散文", "intent": "写一篇关于祖母厨房的抒情散文"}
    base.update(kw)
    return base


def test_envelope_becomes_prose_brief():
    brief = brief_from_envelope(_env(output_requirements={"min_paragraphs": 3}))
    assert brief["form"] == "抒情散文"
    assert brief["spec"]["min_paragraphs"] == 3
    assert brief["spec"]["language"] == "zh"


def test_english_memoir_allowed():
    brief = brief_from_envelope(_env(language="en", form="memoir",
                                     intent="a short memoir about my grandmother"))
    assert brief["language"] == "en"


def test_verse_form_rejected():
    with pytest.raises(ProseIntakeError, match="does not handle form"):
        brief_from_envelope(_env(form="五言绝句"))


def test_fiction_form_rejected():
    with pytest.raises(ProseIntakeError):
        brief_from_envelope(_env(form="short_story"))
