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
_STATIC_MARKER = "## Reviewer role"
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


def test_static_preamble_byte_stable_across_missions() -> None:
    """A new objective or new Planner guidance must not move the static bytes.

    These two blocks vary with every mission; while they lived in the static
    preamble, every mission rotated the sha256 fingerprint, so a same-role
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


def test_review_artifact_changes_keep_session_and_refresh_delta(tmp_path: Path) -> None:
    from argus_skill.core.pipeline_state import read_pipeline_state, write_pipeline_state
    from argus_skill.skills.vertical_select import persist_vertical

    persist_vertical(tmp_path, "research")
    state = read_pipeline_state(tmp_path)
    state["current_stage"] = "review"
    write_pipeline_state(tmp_path, state)
    paper = tmp_path / "paper"
    paper.mkdir()
    review_path = paper / "REVIEW.md"
    review_path.write_text("PRIOR REVIEW CONTENT", encoding="utf-8")
    backend = MemoryBackend()
    backend.queue("reviewer", CannedResponse(message=_review_json(), thread_id="rv1"))
    backend.queue("reviewer", CannedResponse(message=_review_json("done"), thread_id="rv1"))
    reviewer = Reviewer(backend)
    config = ReviewerConfig(working_dir=str(tmp_path), active_vertical="research")
    first = _evaluate(reviewer, config=config)
    review_path.write_text("CURRENT REVIEW CONTENT HAS CHANGED", encoding="utf-8")
    _evaluate(
        reviewer, config=config, round_index=2, resume_thread_id="rv1",
        prior_static_fingerprint=first.static_fingerprint,
    )
    prompts = [p for label, p, _ in backend.history if label == "reviewer"]
    assert "PRIOR REVIEW CONTENT" in prompts[0]
    assert "CURRENT REVIEW CONTENT HAS CHANGED" in prompts[1]
    assert "PRIOR REVIEW CONTENT" not in prompts[1]
    assert _STATIC_MARKER not in prompts[1]
    assert [tid for label, tid in backend.resume_history if label == "reviewer"] == [None, "rv1"]


def test_live_gpu_and_checkpoint_changes_keep_reviewer_session(tmp_path: Path, monkeypatch) -> None:
    from argus_skill.core.pipeline_state import read_pipeline_state, write_pipeline_state
    from argus_skill.skills.vertical_select import persist_vertical
    from argus_skill.verticals.research import prompt_policy

    persist_vertical(tmp_path, "research")
    state = read_pipeline_state(tmp_path)
    state["current_stage"] = "experiment"
    write_pipeline_state(tmp_path, state)
    gpu = ["GPU 0: 8 GB free"]
    models = ["cached-checkpoint-old"]
    # The live-usage lines come from this machine's real GPUs; keep them out
    # so the assertions below only see the stand-in readings.
    monkeypatch.setattr(prompt_policy, "_query_local_gpus", lambda: [])
    monkeypatch.setattr(prompt_policy, "local_hardware_block", lambda: gpu[0])
    monkeypatch.setattr(prompt_policy, "local_model_inventory_block", lambda _root=None: models[0])
    backend = MemoryBackend()
    backend.queue("reviewer", CannedResponse(message=_review_json(), thread_id="rv1"))
    backend.queue("reviewer", CannedResponse(message=_review_json("done"), thread_id="rv1"))
    reviewer = Reviewer(backend)
    config = ReviewerConfig(working_dir=str(tmp_path), active_vertical="research")
    first = _evaluate(reviewer, config=config)
    gpu[0] = "GPU 0: 32 GB free"
    models[0] = "cached-checkpoint-new"
    second = _evaluate(
        reviewer, config=config, round_index=2, resume_thread_id="rv1",
        prior_static_fingerprint=first.static_fingerprint,
    )
    prompts = [prompt for label, prompt, _ in backend.history if label == "reviewer"]
    assert "8 GB free" in prompts[0] and "cached-checkpoint-old" in prompts[0]
    assert "32 GB free" in prompts[1] and "cached-checkpoint-new" in prompts[1]
    assert "8 GB free" not in prompts[1] and "cached-checkpoint-old" not in prompts[1]
    assert first.static_fingerprint == second.static_fingerprint
    assert _STATIC_MARKER not in prompts[1]
    assert [tid for label, tid in backend.resume_history if label == "reviewer"] == [None, "rv1"]


def test_new_objective_keeps_the_fingerprint_and_resumes() -> None:
    """Same role, two missions with different objectives: one static fingerprint.

    This is the failure the 48-hour bill made measurable: 172 fingerprint
    rotations, 1,222 cold starts against 452 resumes, because the objective
    lived in the fingerprinted preamble. A mission change must now ride in the
    delta and let the session resume.
    """
    backend = MemoryBackend()
    backend.queue("reviewer", CannedResponse(message=_review_json(), thread_id="rv1"))
    backend.queue("reviewer", CannedResponse(message=_review_json("done"), thread_id="rv1"))
    r = Reviewer(backend, skill_store=None)
    first = _evaluate(
        r, objective="objective A",
        planner_review_instruction="judge mission A on its own evidence",
    )
    second = _evaluate(
        r, round_index=2, objective="objective B is wholly different",
        planner_review_instruction="judge mission B on its own evidence",
        resume_thread_id="rv1", prior_static_fingerprint=first.static_fingerprint,
    )
    assert first.static_fingerprint == second.static_fingerprint
    resumes = [t for label, t in backend.resume_history if label == "reviewer"]
    assert resumes == [None, "rv1"]            # 2nd call resumed
    r2 = [p for label, p, _ in backend.history if label == "reviewer"][1]
    assert _STATIC_MARKER not in r2            # rubric not re-sent
    # The new mission's context still arrived in the delta.
    assert "objective B is wholly different" in r2
    assert "judge mission B on its own evidence" in r2


def test_stage_change_still_uses_a_fresh_full_prompt(tmp_path: Path) -> None:
    from argus_skill.core.pipeline_state import read_pipeline_state, write_pipeline_state
    from argus_skill.skills.vertical_select import persist_vertical

    persist_vertical(tmp_path, "research")
    state = read_pipeline_state(tmp_path)
    state["current_stage"] = "experiment"
    write_pipeline_state(tmp_path, state)
    backend = MemoryBackend()
    backend.queue("reviewer", CannedResponse(message=_review_json(), thread_id="rv1"))
    backend.queue("reviewer", CannedResponse(message=_review_json(), thread_id="rv1"))
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
