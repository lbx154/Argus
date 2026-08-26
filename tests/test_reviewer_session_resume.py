"""Reviewer prompt splitting and fresh-per-round session behavior."""
from __future__ import annotations

import json
from pathlib import Path

from argus_skill import SkillLoop, SkillLoopConfig
from argus_skill.adapters.memory_backend import CannedResponse, MemoryBackend
from argus_skill.core.models import ReviewDecision, RoundRecord
from argus_skill.engineer.round_reviewer import _previous_review_summary
from argus_skill.engineer.round_state import RoundLoopState
from argus_skill.reviewer import Reviewer
from argus_skill.reviewer._core import ReviewerConfig

# A static-preamble marker (lives in the rubric) + a delta marker (per round).
_STATIC_MARKER = "## Decision"
_DELTA_HEADER = "## Engineer's account of this round"
_REEVALUATE = "RE-EVALUATE INDEPENDENTLY"


def _review_json(status: str = "continue") -> str:
    return json.dumps({
        "status": status,
        "reason": "r",
        "next_action": "do the next thing",
        "round_summary_markdown": "# r\n",
        "completion_summary_markdown": "done" if status == "done" else "",
    })


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


def test_round1_reviewer_prompt_carries_full_rubric() -> None:
    backend = MemoryBackend()
    backend.queue("reviewer", CannedResponse(message=_review_json(), thread_id="rv1"))
    r = Reviewer(backend, skill_store=None)
    review = _evaluate(r)  # resume_thread_id defaults None → full send
    prompt = next(p for label, p, _ in backend.history if label == "reviewer")
    assert _STATIC_MARKER in prompt            # full static rubric present
    assert _DELTA_HEADER in prompt             # delta present too
    assert _REEVALUATE not in prompt           # not a resumed round
    # Side-channel fields populated for the loop to thread next round.
    assert review.thread_id == "rv1"
    assert review.static_fingerprint  # non-empty sha256


def test_reviewer_runner_receives_configured_working_dir(tmp_path: Path) -> None:
    backend = MemoryBackend()
    backend.queue("reviewer", CannedResponse(message=_review_json("done"), thread_id="rv1"))
    r = Reviewer(backend, skill_store=None)

    _evaluate(
        r,
        config=ReviewerConfig(
            model="m",
            reasoning_effort="high",
            working_dir=str(tmp_path),
        ),
    )

    _label, _prompt, options = backend.history[-1]
    assert options.working_dir == str(tmp_path)
    assert options.dangerous_yolo is False
    assert options.full_auto is False
    assert options.sandbox_mode == "read-only"


def test_matching_resume_request_sends_delta_only() -> None:
    backend = MemoryBackend()
    backend.queue("reviewer", CannedResponse(message=_review_json(), thread_id="rv1"))
    backend.queue("reviewer", CannedResponse(message=_review_json("done"), thread_id="rv1"))
    r = Reviewer(backend, skill_store=None)
    first = _evaluate(r)
    _evaluate(
        r, round_index=2, main_summary="ROUND TWO WORK",
        resume_thread_id="rv1", prior_static_fingerprint=first.static_fingerprint,
    )
    prompts = [p for label, p, _ in backend.history if label == "reviewer"]
    r2 = prompts[1]
    assert _STATIC_MARKER not in r2
    assert _REEVALUATE in r2
    assert _DELTA_HEADER in r2
    assert "ROUND TWO WORK" in r2
    assert "settled context" in r2
    assert "ONLY evidence" not in r2
    assert "from scratch" not in r2
    resumes = [t for label, t in backend.resume_history if label == "reviewer"]
    assert resumes == [None, "rv1"]


def test_stage_change_still_uses_a_fresh_full_prompt() -> None:
    backend = MemoryBackend()
    backend.queue("reviewer", CannedResponse(message=_review_json(), thread_id="rv1"))
    backend.queue("reviewer", CannedResponse(message=_review_json(), thread_id="rv1"))
    r = Reviewer(backend, skill_store=None)
    first = _evaluate(r, objective="objective A")
    _evaluate(
        r, round_index=2, objective="objective B is wholly different",
        resume_thread_id="rv1", prior_static_fingerprint=first.static_fingerprint,
    )
    resumes = [t for label, t in backend.resume_history if label == "reviewer"]
    assert resumes == [None, None]             # 2nd call did NOT resume
    r2 = [p for label, p, _ in backend.history if label == "reviewer"][1]
    assert _STATIC_MARKER in r2                # full rubric re-sent


def test_backend_dead_review_still_reports_thread_and_fingerprint() -> None:
    backend = MemoryBackend()
    backend.queue("reviewer", CannedResponse(
        message="", thread_id="dead", fatal_error="before turn completion",
    ))
    r = Reviewer(backend, skill_store=None)
    review = _evaluate(r)
    assert review.backend_unavailable is True
    # Even the dead-backend branch carries the static fingerprint (it had a result).
    assert review.static_fingerprint


# --- full-loop integration tests --------------------------------------------

SKILL_MD = (
    "## Title\nDemo\n\n## Description\nFixed playbook.\n\n## Category\ndemo\n\n"
    "## When to use\n- demo\n\n## When NOT to use\n- prod\n\n"
    "## How to solve\n- do it\n\n## Examples\n- demo\n\n## Response shape\n- inline\n"
)


def _continue() -> str:
    return _review_json("continue")


def _done() -> str:
    return _review_json("done")


def _loop(backend: MemoryBackend, skills: Path) -> SkillLoop:
    return SkillLoop(
        skills_dir=skills,
        engineer_runner=backend,
        reviewer_runner=backend,
        config=SkillLoopConfig(
            engineer_model="m", reviewer_model="m", max_rounds=5,
            backend_failure_backoff_seconds=0,
        ),
    )


def test_reviewer_is_fresh_across_rounds(tmp_path: Path) -> None:
    backend = MemoryBackend()
    backend.queue("matcher", CannedResponse(message='{"matched": []}'))
    backend.queue("distiller", CannedResponse(message=SKILL_MD))
    backend.queue("engineer-r1", CannedResponse(message="r1 work", thread_id="e1"))
    backend.queue("reviewer", CannedResponse(message=_continue(), thread_id="rv1"))
    backend.queue("engineer-r2", CannedResponse(message="r2 work", thread_id="e2"))
    backend.queue("reviewer", CannedResponse(message=_done(), thread_id="rv2"))

    out = _loop(backend, tmp_path / "skills").run("task", workdir=tmp_path)
    assert out.successful
    reviewer_resumes = [
        (label, tid) for label, tid in backend.resume_history if label == "reviewer"
    ]
    assert reviewer_resumes == [("reviewer", None), ("reviewer", None)]
    reviewer_prompts = [
        prompt for label, prompt, _options in backend.history if label == "reviewer"
    ]
    assert "previous_review_summary" not in reviewer_prompts[0]
    assert "## Incremental re-review boundary" in reviewer_prompts[1]
    assert "Round 1 — continue: r" in reviewer_prompts[1]
    assert "do not invent a new unrelated repair round" in reviewer_prompts[1]


def test_previous_review_summary_keeps_only_last_three_one_line_verdicts() -> None:
    state = RoundLoopState()
    for index in range(1, 5):
        state.rounds.append(RoundRecord(
            round_index=index,
            engineer_message=f"work {index}",
            engineer_exit_code=0,
            review=ReviewDecision(
                status="continue",
                reason=f"Repeated reason {index}\nwith extra whitespace",
                next_action=f"action {index}",
            ),
        ))

    summary = _previous_review_summary(state)

    assert "Round 1" not in summary
    assert summary.splitlines() == [
        "Round 2 — continue: Repeated reason 2 with extra whitespace",
        "Round 3 — continue: Repeated reason 3 with extra whitespace",
        "Round 4 — continue: Repeated reason 4 with extra whitespace",
    ]
    assert "next_action" not in summary


def test_reviewer_retry_after_backend_death_starts_fresh_session(
    tmp_path: Path,
) -> None:
    backend = MemoryBackend()
    backend.queue("matcher", CannedResponse(message='{"matched": []}'))
    backend.queue("distiller", CannedResponse(message=SKILL_MD))
    backend.queue("engineer-r1", CannedResponse(message="r1", thread_id="e1"))
    backend.queue("reviewer", CannedResponse(message=_continue(), thread_id="rv1"))
    backend.queue("engineer-r2", CannedResponse(message="r2", thread_id="e2"))
    # Round 2 reviewer: first call dies (backend unavailable), retry succeeds.
    backend.queue("reviewer", CannedResponse(
        message="", thread_id="poison", fatal_error="before turn completion",
    ))
    backend.queue("reviewer", CannedResponse(message=_done(), thread_id="rv3"))

    out = _loop(backend, tmp_path / "skills").run("task", workdir=tmp_path)
    assert out.successful
    reviewer_resumes = [
        tid for label, tid in backend.resume_history if label == "reviewer"
    ]
    assert reviewer_resumes == [None, None, None]
