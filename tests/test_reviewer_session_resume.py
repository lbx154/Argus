"""Reviewer prompt splitting and fresh-per-round session behavior."""
from __future__ import annotations

from pathlib import Path

from argus.adapters.memory_backend import CannedResponse, MemoryBackend
from argus.reviewer import Reviewer
from argus.reviewer._core import ReviewerConfig
from tests.hand_off import hand_off

# A static-preamble marker (lives in the rubric) + a delta marker (per round).
_STATIC_MARKER = "## Reviewer role"


def _review_action(status: str = "continue") -> tuple[str, dict]:
    return hand_off("done" if status == "done" else "engineer", "r")


def _evaluate(reviewer: Reviewer, **over):
    kw = dict(
        objective="make the kernel faster",
        round_index=1,
        session_id=None,
        main_summary="HANDOFF: tried X. RESULT correct=true cand_ms=0.5",
        main_error=None,
        config=ReviewerConfig(model="m", reasoning_effort="high"),
    )
    kw.update(over)
    return reviewer.evaluate(**kw)


# --- evaluate / _render unit tests -----------------------------------------


def test_build_prompt_equals_static_plus_delta() -> None:
    r = Reviewer(runner=None, skill_store=None)
    kw = dict(
        objective="o", operator_messages=["m"], planner_review_instruction="",
        round_index=1, session_id=None, main_summary="S",
        main_error=None, prior_checkpoint={},
    )
    assert r._build_prompt(**kw) == (
        r._build_static_preamble(**kw) + r._build_round_delta(resumed=False, **kw)
    )


def test_static_preamble_byte_stable_across_main_summary() -> None:
    r = Reviewer(runner=None, skill_store=None)
    base = dict(
        objective="o", operator_messages=["m"], planner_review_instruction="",
        round_index=1, session_id=None, main_error=None,
        prior_checkpoint={},
    )
    s1 = r._build_static_preamble(main_summary="ROUND ONE SUMMARY", **base)
    s2 = r._build_static_preamble(main_summary="ROUND TWO DIFFERENT", **base)
    assert s1 == s2, "static preamble drifted when only main_summary changed"
    # The per-round summary belongs to the DELTA, never the static prefix.
    assert "ROUND ONE SUMMARY" not in s1
    d1 = r._build_round_delta(resumed=False, main_summary="ROUND ONE SUMMARY", **base)
    assert "ROUND ONE SUMMARY" in d1
    assert _STATIC_MARKER in s1 and _STATIC_MARKER not in d1


def test_static_preamble_byte_stable_across_missions() -> None:
    """A new objective or new Planner guidance must not move the static bytes.

    These two blocks vary with every mission; while they lived in the static
    preamble, every mission changed the static text, so a same-role
    session never resumed and each round re-sent the full rubric cold. They
    now travel in the delta — still delivered every round, just not
    fingerprinted.
    """
    r = Reviewer(runner=None, skill_store=None)
    base = dict(
        operator_messages=[], round_index=1, session_id=None,
        main_summary="S", main_error=None, prior_checkpoint={},
    )
    s1 = r._build_static_preamble(
        objective="MISSION ALPHA objective",
        planner_review_instruction="watch the ALPHA evidence",
        **base,
    )
    s2 = r._build_static_preamble(
        objective="MISSION BETA is wholly different",
        planner_review_instruction="watch the BETA evidence",
        **base,
    )
    assert s1 == s2, "static preamble drifted when only the mission changed"
    assert "MISSION ALPHA" not in s1 and "ALPHA evidence" not in s1
    # Both blocks are necessary context and must still reach every round.
    d1 = r._build_round_delta(
        resumed=False,
        objective="MISSION ALPHA objective",
        planner_review_instruction="watch the ALPHA evidence",
        **base,
    )
    assert "Task objective:\nMISSION ALPHA objective" in d1
    assert "Planner guidance:\nwatch the ALPHA evidence" in d1


def test_stage_change_still_uses_a_fresh_full_prompt(tmp_path: Path) -> None:
    from argus.core.pipeline_state import read_pipeline_state, write_pipeline_state
    from argus.skills.vertical_select import persist_vertical

    persist_vertical(tmp_path, "research")
    state = read_pipeline_state(tmp_path)
    state["current_stage"] = "experiment"
    write_pipeline_state(tmp_path, state)
    backend = MemoryBackend()
    backend.queue("reviewer", CannedResponse(action=_review_action(), thread_id="rv1"))
    backend.queue("reviewer", CannedResponse(action=_review_action(), thread_id="rv1"))
    r = Reviewer(backend, skill_store=None)
    config = ReviewerConfig(working_dir=str(tmp_path), active_vertical="research")
    first = _evaluate(r, config=config)
    state = read_pipeline_state(tmp_path)
    state["current_stage"] = "review"
    write_pipeline_state(tmp_path, state)
    second = _evaluate(
        r, config=config, round_index=2,
        resume_thread_id="rv1", prior_static_fingerprint=first.static_fingerprint,
    )
    assert first.static_fingerprint != second.static_fingerprint
    resumes = [t for label, t in backend.resume_history if label == "reviewer"]
    assert resumes == [None, None]             # 2nd call did NOT resume
    r2 = [p for label, p, _ in backend.history if label == "reviewer"][1]
    assert _STATIC_MARKER in r2                # full rubric re-sent


# --- full-loop integration tests --------------------------------------------
