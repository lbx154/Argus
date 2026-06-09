"""Tests for ``SkillLoopConfig.resolved_matcher_model`` env override."""
from __future__ import annotations

from collections.abc import Iterator

import pytest

from argus_skill.loop import SkillLoopConfig


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.delenv("ARGUS_SKILL_MATCHER_MODEL", raising=False)
    yield


def test_default_fallback_to_engineer_model():
    cfg = SkillLoopConfig(engineer_model="gpt-5.4")
    assert cfg.resolved_matcher_model() == "gpt-5.4"


def test_explicit_matcher_model_field_wins_over_engineer():
    cfg = SkillLoopConfig(
        engineer_model="gpt-5.4", matcher_model="gpt-5.4-mini"
    )
    assert cfg.resolved_matcher_model() == "gpt-5.4-mini"


def test_env_overrides_both_field_and_engineer(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ARGUS_SKILL_MATCHER_MODEL", "gpt-4o-mini")
    cfg = SkillLoopConfig(
        engineer_model="gpt-5.4", matcher_model="gpt-5.4-mini"
    )
    assert cfg.resolved_matcher_model() == "gpt-4o-mini"


def test_env_whitespace_only_does_not_override(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ARGUS_SKILL_MATCHER_MODEL", "   ")
    cfg = SkillLoopConfig(engineer_model="gpt-5.4")
    assert cfg.resolved_matcher_model() == "gpt-5.4"
