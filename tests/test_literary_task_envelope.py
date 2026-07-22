"""Closed-loop tests for the shared Task Envelope contract (literary platform).

Covers producer(normalize)/validator(schema+semantic)/negative paths. There is
NO test that merely asserts "the schema loads" — every case exercises accept or
reject behavior with a concrete envelope.
"""
from __future__ import annotations

import copy

import pytest

from argus_skill.verticals.literary.shared.task_envelope import (
    VALID_MODES,
    EnvelopeError,
    normalize_envelope,
    validate_envelope,
)

_BASE = {
    "task_id": "t1",
    "mode": "from_scratch",
    "language": "zh",
    "form": "short_story",
    "intent": "写一个都市悬疑短篇",
}


def _env(**over):
    e = copy.deepcopy(_BASE)
    e.update(over)
    return e


# --- happy path + normalization ------------------------------------------- #
def test_minimal_valid_envelope_normalizes_and_fills_defaults():
    out = normalize_envelope(_env())
    assert out["retrieval_policy"] == "none"
    assert out["constraints"] == []
    assert out["reference_inputs"] == []
    assert out["target_length"] is None
    assert out["output_requirements"] == {}
    # input not mutated
    assert "retrieval_policy" not in _BASE


def test_validate_accepts_a_normalized_envelope():
    validate_envelope(normalize_envelope(_env()))  # must not raise


def test_all_declared_modes_are_accepted_when_source_supplied():
    for mode in VALID_MODES:
        env = _env(mode=mode)
        # editing/continuation modes need a source ref to be coherent
        env["reference_inputs"] = [{"role": "source_text", "ref": "prior.md"}]
        normalize_envelope(env)  # must not raise for any declared mode


# --- structural rejections ------------------------------------------------- #
def test_unknown_mode_rejected():
    with pytest.raises(EnvelopeError):
        normalize_envelope(_env(mode="freestyle"))


def test_blank_intent_rejected():
    with pytest.raises(EnvelopeError):
        normalize_envelope(_env(intent="   "))


def test_missing_language_rejected():
    e = _env()
    del e["language"]
    with pytest.raises(EnvelopeError):
        normalize_envelope(e)


def test_bad_language_rejected():
    with pytest.raises(EnvelopeError):
        normalize_envelope(_env(language="fr"))


def test_stray_top_level_key_rejected():
    with pytest.raises(EnvelopeError):
        normalize_envelope(_env(vibe="mysterious"))


def test_non_object_rejected():
    with pytest.raises(EnvelopeError):
        normalize_envelope(["not", "an", "object"])  # type: ignore[arg-type]


# --- semantic cross-field rule: editing modes need a source ---------------- #
def test_continuation_without_source_rejected():
    with pytest.raises(EnvelopeError):
        normalize_envelope(_env(mode="continuation"))


def test_polish_without_source_rejected():
    with pytest.raises(EnvelopeError):
        normalize_envelope(_env(mode="polish"))


def test_continuation_with_prior_state_accepted():
    env = _env(mode="continuation",
               reference_inputs=[{"role": "prior_state", "ref": "story_state.json"}])
    out = normalize_envelope(env)
    assert out["mode"] == "continuation"


def test_style_reference_alone_does_not_satisfy_source_requirement():
    # a style sample is not the text being edited
    env = _env(mode="rewrite",
               reference_inputs=[{"role": "style_reference", "ref": "sample.md"}])
    with pytest.raises(EnvelopeError):
        normalize_envelope(env)
