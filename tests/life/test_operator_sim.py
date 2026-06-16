"""Tests for the long-tailed simulated human operator.

Covers:
- the intervention-style distribution is LONG-TAILED (head >> mid > tail) and
  the rare TAIL band still appears across a fixed-seed sweep;
- ``build_message`` falls back deterministically with ``runner=None`` and the
  fallback text is GENERAL (no task literals);
- ``build_message`` uses the runner's phrasing when one is supplied;
- ``gather_run_state`` reads the ground-truth gate / stage defensively;
- ``make_operator_guidance_provider`` returns one message per call and never
  raises.
"""
from __future__ import annotations

import random
from pathlib import Path

import pytest

from argus_skill.life import operator_sim
from argus_skill.life.operator_sim import (
    BAND_HEAD,
    BAND_MID,
    BAND_TAIL,
    STYLE_TABLE,
    OperatorStyle,
    RunState,
    SimulatedOperator,
    band_weights,
    gather_run_state,
    make_operator_guidance_provider,
    sample_style,
)

# Task-specific tokens that must NEVER appear in a general operator message /
# fallback — the operator reasons from whatever state it is handed, not from
# any baked-in benchmark/metric/hardware.
TASK_LITERALS = (
    "nanochat",
    "nanogpt",
    "karpathy",
    "mfu",
    "bpb",
    "a100",
    "gpu",
    "emnlp",
    "eval_solution",
    "tokens/sec",
)


def _assert_general(text: str) -> None:
    low = text.lower()
    for lit in TASK_LITERALS:
        assert lit not in low, f"task literal {lit!r} leaked into: {text!r}"


# ---------------------------------------------------------------------------
# Distribution shape
# ---------------------------------------------------------------------------


def test_band_weights_are_long_tailed() -> None:
    w = band_weights()
    # HEAD dominates, MID in the middle, TAIL rare — the long-tailed shape.
    assert w[BAND_HEAD] > w[BAND_MID] > w[BAND_TAIL] > 0.0
    assert w[BAND_HEAD] == pytest.approx(0.70, abs=1e-6)
    assert w[BAND_MID] == pytest.approx(0.25, abs=1e-6)
    assert w[BAND_TAIL] == pytest.approx(0.05, abs=1e-6)
    assert sum(w.values()) == pytest.approx(1.0, abs=1e-6)


def test_sample_distribution_is_long_tailed_and_tail_appears() -> None:
    # Fixed-seed sweep: deterministic and long-tailed. The rare TAIL band must
    # still surface at least once across the sweep.
    counts = {BAND_HEAD: 0, BAND_MID: 0, BAND_TAIL: 0}
    for seed in range(400):
        style = sample_style(random.Random(seed))
        counts[style.band] += 1
    assert counts[BAND_HEAD] > counts[BAND_MID] > counts[BAND_TAIL]
    assert counts[BAND_TAIL] >= 1, "tail style never appeared in the sweep"
    # Tail should be genuinely rare, not just smallest.
    assert counts[BAND_TAIL] < counts[BAND_HEAD] / 4


def test_sample_style_is_deterministic_per_seed() -> None:
    a = [sample_style(random.Random(7)).name for _ in range(1)]
    b = [sample_style(random.Random(7)).name for _ in range(1)]
    assert a == b
    # A single rng advances and produces a varied sequence.
    rng = random.Random(123)
    seq1 = [sample_style(rng).name for _ in range(20)]
    rng2 = random.Random(123)
    seq2 = [sample_style(rng2).name for _ in range(20)]
    assert seq1 == seq2


def test_every_style_has_fallbacks_and_a_known_band() -> None:
    for style in STYLE_TABLE:
        assert style.band in {BAND_HEAD, BAND_MID, BAND_TAIL}
        assert style.fallbacks, f"{style.name} has no fallback"
        for fb in style.fallbacks:
            assert fb.strip()
            _assert_general(fb)


# ---------------------------------------------------------------------------
# build_message: fallback (runner=None) + general
# ---------------------------------------------------------------------------


def test_build_message_fallback_with_no_runner_is_general() -> None:
    op = SimulatedOperator(runner=None, seed=0)
    state = RunState(objective="Improve the thing under a fixed budget")
    seen: set[str] = set()
    for style in STYLE_TABLE:
        msg = op.build_message(state, style, runner=None)
        assert msg, f"empty fallback for {style.name}"
        assert msg in style.fallbacks
        _assert_general(msg)
        seen.add(msg)
    # Several distinct fallbacks were produced (rng selects among them).
    assert len(seen) >= 2


def test_next_message_never_raises_without_runner() -> None:
    op = SimulatedOperator(runner=None, seed=3)
    state = gather_run_state(Path("/nonexistent-xyz"), objective="do something")
    for _ in range(10):
        msg = op.next_message(state)
        assert isinstance(msg, str) and msg.strip()
        _assert_general(msg)


def test_build_message_swallows_runner_errors() -> None:
    class _Boom:
        def run_exec(self, **_kw):
            raise RuntimeError("backend exploded")

    op = SimulatedOperator(runner=_Boom(), seed=1)
    style = STYLE_TABLE[0]
    msg = op.build_message(RunState(objective="x"), style)
    # Falls back deterministically rather than raising.
    assert msg in style.fallbacks


# ---------------------------------------------------------------------------
# build_message: uses the runner when present
# ---------------------------------------------------------------------------


class _FakeResult:
    def __init__(self, text: str) -> None:
        self.last_agent_message = text


class _FakeRunner:
    def __init__(self, text: str) -> None:
        self.text = text
        self.calls: list[dict] = []

    def run_exec(self, *, prompt, options, run_label, resume_thread_id=None):
        self.calls.append({"prompt": prompt, "run_label": run_label})
        return _FakeResult(self.text)


def test_build_message_uses_runner_phrasing() -> None:
    runner = _FakeRunner("Keep going — what's the real bottleneck right now?")
    op = SimulatedOperator(runner=runner, seed=0)
    state = RunState(objective="Reduce something", stage="optimize")
    msg = op.build_message(state, STYLE_TABLE[0])
    assert msg == "Keep going — what's the real bottleneck right now?"
    assert runner.calls and runner.calls[0]["run_label"] == "operator-sim"
    # The grounding state is fed into the prompt.
    assert "optimize" in runner.calls[0]["prompt"]


def test_runner_empty_message_falls_back() -> None:
    op = SimulatedOperator(runner=_FakeRunner("   "), seed=2)
    style = STYLE_TABLE[0]
    msg = op.build_message(RunState(objective="x"), style)
    assert msg in style.fallbacks


def test_clean_message_clamps_to_a_few_sentences() -> None:
    long = "One. Two. Three. Four. Five. Six."
    op = SimulatedOperator(runner=_FakeRunner(long), seed=0)
    msg = op.build_message(RunState(), STYLE_TABLE[0])
    # Clamped to <= 3 sentences.
    assert msg.count(".") <= 3
    assert "Four" not in msg


# ---------------------------------------------------------------------------
# gather_run_state grounding
# ---------------------------------------------------------------------------


def test_gather_run_state_detects_ground_truth_gate(tmp_path: Path) -> None:
    st = gather_run_state(tmp_path, objective="obj")
    assert st.has_ground_truth is False
    research = tmp_path / "research"
    research.mkdir()
    (research / "GROUND_TRUTH.md").write_text("verified facts", encoding="utf-8")
    st2 = gather_run_state(tmp_path, objective="obj")
    assert st2.has_ground_truth is True
    assert st2.has_research_dir is True
    # The summary reflects the gate state and never raises.
    assert "GROUND_TRUTH" in st2.summarize()


def test_summarize_truncates_long_objective() -> None:
    st = RunState(objective="x" * 5000)
    out = st.summarize()
    assert len(out) <= 1700  # bounded


# ---------------------------------------------------------------------------
# make_operator_guidance_provider
# ---------------------------------------------------------------------------


def test_provider_returns_one_message_and_is_general(tmp_path: Path) -> None:
    provider = make_operator_guidance_provider(
        project_root=tmp_path,
        objective="optimize the artifact within budget",
        runner=None,
        seed=5,
    )
    out = provider()
    assert isinstance(out, list) and len(out) == 1
    assert out[0].strip()
    _assert_general(out[0])


def test_provider_never_raises_on_bad_state(monkeypatch, tmp_path: Path) -> None:
    def _boom(*_a, **_k):
        raise RuntimeError("grounding failed")

    monkeypatch.setattr(operator_sim, "gather_run_state", _boom)
    provider = make_operator_guidance_provider(
        project_root=tmp_path, objective="x", runner=None, seed=1
    )
    assert provider() == []
