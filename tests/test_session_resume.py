"""Multi-turn integration test for ``resume_thread_id`` plumbing.

Verifies the four-layer chain wired in commit "life chat: resume codex
CLI session across rounds and missions":

    SkillLoop.run(seed_thread_id=…)
        → SupervisedEngineer.run(seed_thread_id=…)
            → engineer_runner.run_exec(resume_thread_id=…)
        → returns LoopOutcome.last_thread_id

Two scenarios:

1. Within one mission with two engineer rounds, round 2 should receive
   round 1's emitted ``thread_id`` as ``resume_thread_id``.
2. A second SkillLoop.run seeded with the previous outcome's
   ``last_thread_id`` should pass that id into round 1.
"""
from __future__ import annotations

import json
from pathlib import Path

from argus_skill import SkillLoop, SkillLoopConfig
from argus_skill.adapters.memory_backend import CannedResponse, MemoryBackend

SKILL_MD = (
    "## Title\nDemo skill\n\n"
    "## Description\nA fixed playbook for the resume test.\n\n"
    "## Category\ndemo\n\n"
    "## When to use\n- demo task\n- second demo task\n\n"
    "## When NOT to use\n- production code\n\n"
    "## How to solve\n- Do the thing.\n\n"
    "## Examples\n- demo → done\n\n"
    "## Response shape\n- Reply inline.\n"
)


def _continue_review() -> str:
    return json.dumps({
        "status": "continue",
        "confidence": 0.4,
        "reason": "More work needed.",
        "next_action": "Finish the work.",
        "round_summary_markdown": "# r1\n",
        "completion_summary_markdown": "",
    })


def _done_review() -> str:
    return json.dumps({
        "status": "done",
        "confidence": 0.95,
        "reason": "Met criterion.",
        "next_action": "—",
        "round_summary_markdown": "# done\n",
        "completion_summary_markdown": "Done.",
    })


def _build_loop(backend: MemoryBackend, skills_dir: Path) -> SkillLoop:
    config = SkillLoopConfig(
        scientist_model="m",
        engineer_model="m",
        reviewer_model="m",
        max_rounds=3,
        check_commands=[],
        skill_writeback=False,
        distill_on_miss=True,
    )
    return SkillLoop(
        skills_dir=skills_dir,
        scientist_runner=backend,
        engineer_runner=backend,
        reviewer_runner=backend,
        config=config,
    )


def test_resume_thread_id_flows_round_to_round_and_run_to_run(tmp_path: Path) -> None:
    backend = MemoryBackend()

    # First run: two engineer rounds, each emitting its own thread_id.
    backend.queue("matcher", CannedResponse(message='{"matched": []}'))
    backend.queue("distiller", CannedResponse(message=SKILL_MD))
    backend.queue("engineer-r1", CannedResponse(
        message="round 1 work", thread_id="tid-A1",
    ))
    backend.queue("reviewer", CannedResponse(message=_continue_review()))
    backend.queue("engineer-r2", CannedResponse(
        message="round 2 work", thread_id="tid-A2",
    ))
    backend.queue("reviewer", CannedResponse(message=_done_review()))

    loop = _build_loop(backend, tmp_path / "skills1")
    out1 = loop.run("first task", workdir=tmp_path)

    assert out1.successful
    assert out1.last_thread_id == "tid-A2", out1.last_thread_id

    resume_by_label = {label: tid for label, tid in backend.resume_history}
    # Round 1 has no seed, so resume should be None.
    r1_seeds = [tid for label, tid in backend.resume_history if label == "engineer-r1"]
    r2_seeds = [tid for label, tid in backend.resume_history if label == "engineer-r2"]
    assert r1_seeds == [None], r1_seeds
    # Round 2 must reuse round 1's emitted thread_id.
    assert r2_seeds == ["tid-A1"], r2_seeds
    # Reviewer / matcher / distiller must NOT inherit the engineer thread.
    assert resume_by_label.get("reviewer") is None
    assert resume_by_label.get("matcher") is None
    assert resume_by_label.get("distiller") is None

    # Second run: seed with previous outcome's last_thread_id. First
    # engineer round should see it as resume_thread_id.
    backend2 = MemoryBackend()
    backend2.queue("matcher", CannedResponse(message=json.dumps({
        "matched": [{
            "skill_name": "Demo skill",
            "score": 0.9,
            "rationale": "exact match",
        }],
    })))
    # Reuse the skill file written by run 1 if any; otherwise the matcher
    # would refer to a missing file. Distill again to be safe in a fresh
    # skills dir for run 2.
    backend2.queue("distiller", CannedResponse(message=SKILL_MD))
    backend2.queue("engineer-r1", CannedResponse(
        message="second-task work", thread_id="tid-B1",
    ))
    backend2.queue("reviewer", CannedResponse(message=_done_review()))

    loop2 = _build_loop(backend2, tmp_path / "skills2")
    out2 = loop2.run(
        "second task", workdir=tmp_path, seed_thread_id=out1.last_thread_id,
    )
    assert out2.successful
    assert out2.last_thread_id == "tid-B1"
    seeds_run2 = [tid for label, tid in backend2.resume_history if label == "engineer-r1"]
    assert seeds_run2 == ["tid-A2"], seeds_run2


def test_resume_thread_id_falls_back_when_engineer_emits_no_thread(tmp_path: Path) -> None:
    """If a round's RunnerResult has thread_id=None, the next round should
    keep using the most recent non-None thread_id rather than reset."""
    backend = MemoryBackend()
    backend.queue("matcher", CannedResponse(message='{"matched": []}'))
    backend.queue("distiller", CannedResponse(message=SKILL_MD))
    backend.queue("engineer-r1", CannedResponse(
        message="r1", thread_id="sticky-1",
    ))
    backend.queue("reviewer", CannedResponse(message=_continue_review()))
    backend.queue("engineer-r2", CannedResponse(
        message="r2", thread_id=None,  # backend transiently omits thread_id
    ))
    backend.queue("reviewer", CannedResponse(message=_continue_review()))
    backend.queue("engineer-r3", CannedResponse(
        message="r3", thread_id="sticky-3",
    ))
    backend.queue("reviewer", CannedResponse(message=_done_review()))

    loop = _build_loop(backend, tmp_path / "skills")
    out = loop.run("task", workdir=tmp_path)

    assert out.successful
    seeds = {label: tid for label, tid in backend.resume_history if label.startswith("engineer-")}
    assert seeds["engineer-r1"] is None
    assert seeds["engineer-r2"] == "sticky-1"
    # r3 keeps using sticky-1 because r2 returned thread_id=None.
    assert seeds["engineer-r3"] == "sticky-1"
    # Final outcome should reflect the latest non-None thread_id.
    assert out.last_thread_id == "sticky-3"
