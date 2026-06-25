"""FrozenJudge against THIS session's real nanochat-B200 failure cases.

Proves the slim spine's soul structurally kills the exact bugs that trapped the
OLD harness (and that I spent this session patching in meta/saturation + the
nanochat floor logic):

  * floor mis-anchoring onto a rejected candidate whose decision text merely
    CONTAINS "promoted" (a382, a regression) — impossible here: a regressed
    metric can never move the floor, and no string/LLM decides the win;
  * sub-noise dips below the floor counted as progress (a420/a421) — the noise
    gate honestly refuses them;
  * the win is a number vs the published floor + noise gate, decided by NO LLM.

Numbers are the real nanochat-mission-b200 metrics (val_bpb, lower = better).
"""
from __future__ import annotations

from argus_skill.argus.judge import FrozenJudge, JudgeConfig

A374_FLOOR = 0.963634   # the genuine promoted floor (re-measured on our hardware)
A382 = 0.967003         # rejected + REGRESSED — old harness mis-anchored the floor onto this
A421 = 0.963273         # below the floor but only ~0.00036 → inside the ~0.0015 noise band
CLEAR_WIN = 0.961500    # a real win: > noise below the floor


def _judge() -> FrozenJudge:
    return FrozenJudge(
        JudgeConfig(floor=A374_FLOOR, noise=0.0015, n_seed=10,
                    forbidden=("optimized_from_karpathy", "optimized_from_vanilla"))
    )


def test_regressed_candidate_is_not_a_win_and_never_becomes_floor():
    # The old "promote" substring trap re-anchored the floor onto a382 (worse).
    # Here a regression simply fails and CANNOT move the floor.
    j = _judge()
    ev = j.score(A382)
    assert not ev.passed
    assert j.floor == A374_FLOOR


def test_sub_noise_dip_below_floor_is_honestly_not_a_win():
    # a421 is genuinely below the floor but within run-to-run noise → coin flip.
    j = _judge()
    ev = j.score(A421)
    assert not ev.passed
    assert "noise" in ev.note.lower()
    assert j.floor == A374_FLOOR  # sub-noise jitter never lowers the floor


def test_clear_win_passes_and_moves_floor():
    j = _judge()
    ev = j.score(CLEAR_WIN)
    assert ev.passed
    assert j.floor == CLEAR_WIN


def test_reading_the_published_answer_disqualifies():
    j = _judge()
    ev = j.score(0.5, refs=["copied optimized_from_karpathy solution"])
    assert not ev.passed
    assert "DISQUALIF" in ev.note.upper()
