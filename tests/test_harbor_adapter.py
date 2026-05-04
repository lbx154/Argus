"""Tests for the offline-testable helpers in benchmarks/harbor_adapter.py.

We can't unit-test the full ``ArgusSkillCodex.run`` without Harbor's
runtime (it needs a ``BaseEnvironment``), but we can exhaustively test
the logic that runs on host: codex JSON parsing, prompt builder,
host-prep ablation flags.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def adapter():
    """Load benchmarks/harbor_adapter as a module without installing harbor."""
    repo_root = Path(__file__).resolve().parent.parent
    src = repo_root / "benchmarks" / "harbor_adapter.py"
    spec = importlib.util.spec_from_file_location(
        "argus_skill_benchmarks_harbor_adapter", src
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_parse_agent_messages_picks_up_completed_items(adapter):
    text = (
        '{"type":"thread.started","thread_id":"thr_x"}\n'
        '{"type":"item.started","item":{"type":"agent_message"}}\n'
        '{"type":"item.completed","item":{"type":"agent_message","text":"first"}}\n'
        'arbitrary non-json\n'
        '{"type":"item.completed","item":{"type":"reasoning","text":"ignore me"}}\n'
        '{"type":"item.completed","item":{"type":"agent_message","text":"second"}}\n'
        '{"type":"turn.completed"}\n'
    )
    msgs = adapter._parse_agent_messages_from_jsonl(text)
    assert msgs == ["first", "second"]


def test_parse_agent_messages_handles_empty(adapter):
    assert adapter._parse_agent_messages_from_jsonl("") == []
    assert adapter._parse_agent_messages_from_jsonl("not json\nnot json either\n") == []


def test_extract_thread_id(adapter):
    text = (
        'foo bar\n'
        '{"type":"thread.started","thread_id":"thr_abc"}\n'
        '{"type":"item.completed","item":{"type":"agent_message","text":"x"}}\n'
    )
    assert adapter._extract_thread_id_from_jsonl(text) == "thr_abc"
    assert adapter._extract_thread_id_from_jsonl("nothing") is None


def test_round_prompt_with_skill_and_feedback(adapter):
    prompt = adapter.ArgusSkillCodex._build_round_prompt(
        instruction="fix the bug in foo.py",
        skill_text="## Title\nDebug regression\n",
        review_feedback="run pytest first",
        round_idx=2,
        total_rounds=3,
    )
    assert "## Skill guide" in prompt
    assert "## Reviewer feedback (from round 1)" in prompt
    assert "## Task\nfix the bug in foo.py" in prompt
    assert "round 2 of 3" in prompt


def test_round_prompt_without_skill(adapter):
    prompt = adapter.ArgusSkillCodex._build_round_prompt(
        instruction="task",
        skill_text="",
        review_feedback=None,
        round_idx=1,
        total_rounds=1,
    )
    assert "## Skill guide" not in prompt
    assert "## Reviewer feedback" not in prompt
    assert "## Task\ntask" in prompt
    # Single-round runs shouldn't mention "round 1 of 1" hint.
    assert "round 1 of 1" not in prompt


def test_bool_env_handles_falsey_values(adapter, monkeypatch):
    monkeypatch.setenv("FOO", "")
    assert adapter._bool_env("FOO") is False
    monkeypatch.setenv("FOO", "0")
    assert adapter._bool_env("FOO") is False
    monkeypatch.setenv("FOO", "no")
    assert adapter._bool_env("FOO") is False
    monkeypatch.setenv("FOO", "false")
    assert adapter._bool_env("FOO") is False
    monkeypatch.delenv("FOO")
    assert adapter._bool_env("FOO", default=True) is True
    assert adapter._bool_env("FOO", default=False) is False


def test_bool_env_handles_truthy_values(adapter, monkeypatch):
    for value in ("1", "true", "yes", "on", "anything-not-explicit-false"):
        monkeypatch.setenv("FOO", value)
        assert adapter._bool_env("FOO") is True


def test_int_and_float_env_default_on_invalid(adapter, monkeypatch):
    monkeypatch.setenv("BAR", "not-a-number")
    assert adapter._int_env("BAR", 7) == 7
    assert adapter._float_env("BAR", 1.25) == 1.25
    monkeypatch.setenv("BAR", "42")
    assert adapter._int_env("BAR", 7) == 42
    assert adapter._float_env("BAR", 1.25) == 42.0


def test_no_skill_ablation_skips_host_prep(adapter, monkeypatch):
    monkeypatch.setenv("ARGUS_SKILL_HARBOR_NO_SKILL", "1")
    prep = adapter._do_host_prep("any task")
    assert prep.skill_used is False
    assert prep.skill_text == ""
    assert prep.fallback_reason == "no_skill_ablation"
    assert prep.scientist_tokens == 0
