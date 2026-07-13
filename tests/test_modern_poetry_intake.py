"""modern_poetry intake — consumes the shared Task Envelope (third consumer)."""
from __future__ import annotations

import pytest

from argus_skill.verticals.modern_poetry.intake import (
    ModernPoetryIntakeError,
    brief_from_envelope,
)


def _env(**kw):
    base = {"task_id": "m1", "mode": "from_scratch", "language": "zh",
            "form": "free_verse", "intent": "写一首关于城市夜晚的自由诗"}
    base.update(kw)
    return base


def test_envelope_becomes_modern_brief():
    brief = brief_from_envelope(_env(
        output_requirements={"line_count": 3, "banned_words": ["岁月静好"]}))
    assert brief["form"] == "free_verse"
    assert brief["form_spec"]["line_count"] == 3
    assert brief["form_spec"]["banned_words"] == ["岁月静好"]


def test_english_free_verse_allowed():
    brief = brief_from_envelope(_env(language="en", form="prose_poem",
                                     intent="a prose poem about rain"))
    assert brief["language"] == "en"
    assert brief["form_spec"]["language"] == "en"


def test_classical_form_rejected():
    with pytest.raises(ModernPoetryIntakeError, match="does not handle form"):
        brief_from_envelope(_env(form="五言绝句"))


def test_narrative_form_rejected():
    with pytest.raises(ModernPoetryIntakeError):
        brief_from_envelope(_env(form="short_story"))
