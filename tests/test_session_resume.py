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

_FATAL_EMPTY_OUTPUT_ERROR = (
    "Codex ran out of room in the model's context window. "
    "Start a new thread or clear earlier history before retrying."
)

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
        "reason": "More work needed.",
        "next_action": "Finish the work.",
        "round_summary_markdown": "# r1\n",
        "completion_summary_markdown": "",
    })


def _done_review() -> str:
    return json.dumps({
        "status": "done",
        "reason": "Met criterion.",
        "next_action": "—",
        "round_summary_markdown": "# done\n",
        "completion_summary_markdown": "Done.",
    })


def _build_loop(backend: MemoryBackend, skills_dir: Path) -> SkillLoop:
    config = SkillLoopConfig(
        engineer_model="m",
        reviewer_model="m",
        max_rounds=3,
        check_commands=[],
        backend_failure_backoff_seconds=0,
    )
    return SkillLoop(
        skills_dir=skills_dir,
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


def test_resume_thread_id_clears_after_no_progress_mission(tmp_path: Path) -> None:
    backend = MemoryBackend()
    backend.queue("matcher", CannedResponse(message='{"matched": []}'))
    backend.queue("distiller", CannedResponse(message=SKILL_MD))
    backend.queue("engineer-r1", CannedResponse(message="", thread_id="poison-1"))
    backend.queue("reviewer", CannedResponse(message=_continue_review()))
    backend.queue("engineer-r2", CannedResponse(message="", thread_id="poison-2"))
    backend.queue("reviewer", CannedResponse(message=_continue_review()))

    loop = _build_loop(backend, tmp_path / "skills1")
    out1 = loop.run("first task", workdir=tmp_path)

    assert out1.status == "no_progress"
    assert out1.last_thread_id is None
    r1_seeds = [tid for label, tid in backend.resume_history if label == "engineer-r1"]
    r2_seeds = [tid for label, tid in backend.resume_history if label == "engineer-r2"]
    assert r1_seeds == [None], r1_seeds
    assert r2_seeds == ["poison-1"], r2_seeds

    backend2 = MemoryBackend()
    backend2.queue("matcher", CannedResponse(message='{"matched": []}'))
    backend2.queue("distiller", CannedResponse(message=SKILL_MD))
    backend2.queue("engineer-r1", CannedResponse(
        message="recovered work", thread_id="tid-B1",
    ))
    backend2.queue("reviewer", CannedResponse(message=_done_review()))

    loop2 = _build_loop(backend2, tmp_path / "skills2")
    out2 = loop2.run("second task", workdir=tmp_path, seed_thread_id=out1.last_thread_id)

    assert out2.successful
    assert out2.last_thread_id == "tid-B1"
    assert [tid for label, tid in backend2.resume_history if label == "engineer-r1"] == [None]


def test_resume_thread_id_clears_after_fatal_empty_output_mission(
    tmp_path: Path,
) -> None:
    backend = MemoryBackend()
    backend.queue("matcher", CannedResponse(message='{"matched": []}'))
    backend.queue("distiller", CannedResponse(message=SKILL_MD))
    backend.queue(
        "engineer-r1",
        CannedResponse(message="", thread_id="fatal-1", fatal_error=_FATAL_EMPTY_OUTPUT_ERROR),
    )
    backend.queue("reviewer", CannedResponse(message=_done_review()))

    loop = _build_loop(backend, tmp_path / "skills")
    out1 = loop.run("first task", workdir=tmp_path)

    assert out1.successful
    assert out1.last_thread_id is None
    assert [tid for label, tid in backend.resume_history if label == "engineer-r1"] == [None]

    backend2 = MemoryBackend()
    backend2.queue("matcher", CannedResponse(message='{"matched": []}'))
    backend2.queue("distiller", CannedResponse(message=SKILL_MD))
    backend2.queue("engineer-r1", CannedResponse(
        message="follow-up", thread_id="tid-C1",
    ))
    backend2.queue("reviewer", CannedResponse(message=_done_review()))

    loop2 = _build_loop(backend2, tmp_path / "skills2")
    out2 = loop2.run("second task", workdir=tmp_path, seed_thread_id=out1.last_thread_id)

    assert out2.successful
    assert out2.last_thread_id == "tid-C1"
    assert [tid for label, tid in backend2.resume_history if label == "engineer-r1"] == [None]


def test_thread_rolls_when_prior_round_exceeds_token_limit(
    tmp_path: Path, monkeypatch,
) -> None:
    """A resumed thread that reports input_tokens >= the configured limit must
    be dropped: the next round starts a fresh session (resume=None) instead of
    resuming the bloated thread. This bounds the cross-mission thread growth
    that otherwise forces codex's lossy auto-compaction (the amnesia loop)."""
    monkeypatch.setenv("ARGUS_SKILL_THREAD_TOKEN_LIMIT", "1000")

    backend = MemoryBackend()
    backend.queue("matcher", CannedResponse(message='{"matched": []}'))
    backend.queue("distiller", CannedResponse(message=SKILL_MD))
    # Round 1 succeeds but reports a bloated context (>= 1000 token cap).
    backend.queue("engineer-r1", CannedResponse(
        message="round 1 work", thread_id="tid-A1", input_tokens=5000,
    ))
    backend.queue("reviewer", CannedResponse(message=_continue_review()))
    backend.queue("engineer-r2", CannedResponse(
        message="round 2 work", thread_id="tid-A2", input_tokens=200,
    ))
    backend.queue("reviewer", CannedResponse(message=_done_review()))

    loop = _build_loop(backend, tmp_path / "skills")
    out = loop.run("task", workdir=tmp_path)

    assert out.successful
    r1_seeds = [tid for label, tid in backend.resume_history if label == "engineer-r1"]
    r2_seeds = [tid for label, tid in backend.resume_history if label == "engineer-r2"]
    assert r1_seeds == [None], r1_seeds
    # Round 1 used 5000 input tokens (>= 1000), so round 2 must NOT resume it.
    assert r2_seeds == [None], r2_seeds


def test_thread_resumes_when_token_limit_not_reached(
    tmp_path: Path, monkeypatch,
) -> None:
    """Below the cap, the normal cross-round resume must be preserved."""
    monkeypatch.setenv("ARGUS_SKILL_THREAD_TOKEN_LIMIT", "100000")

    backend = MemoryBackend()
    backend.queue("matcher", CannedResponse(message='{"matched": []}'))
    backend.queue("distiller", CannedResponse(message=SKILL_MD))
    backend.queue("engineer-r1", CannedResponse(
        message="round 1 work", thread_id="tid-A1", input_tokens=5000,
    ))
    backend.queue("reviewer", CannedResponse(message=_continue_review()))
    backend.queue("engineer-r2", CannedResponse(
        message="round 2 work", thread_id="tid-A2", input_tokens=6000,
    ))
    backend.queue("reviewer", CannedResponse(message=_done_review()))

    loop = _build_loop(backend, tmp_path / "skills")
    out = loop.run("task", workdir=tmp_path)

    assert out.successful
    r2_seeds = [tid for label, tid in backend.resume_history if label == "engineer-r2"]
    # 5000 < 100000 cap → round 2 resumes round 1's thread as usual.
    assert r2_seeds == ["tid-A1"], r2_seeds


def test_token_roll_disabled_with_zero_limit(
    tmp_path: Path, monkeypatch,
) -> None:
    """A 0 limit disables the token roll even for huge contexts."""
    monkeypatch.setenv("ARGUS_SKILL_THREAD_TOKEN_LIMIT", "0")

    backend = MemoryBackend()
    backend.queue("matcher", CannedResponse(message='{"matched": []}'))
    backend.queue("distiller", CannedResponse(message=SKILL_MD))
    backend.queue("engineer-r1", CannedResponse(
        message="round 1 work", thread_id="tid-A1", input_tokens=10_000_000,
    ))
    backend.queue("reviewer", CannedResponse(message=_continue_review()))
    backend.queue("engineer-r2", CannedResponse(
        message="round 2 work", thread_id="tid-A2", input_tokens=10_000_000,
    ))
    backend.queue("reviewer", CannedResponse(message=_done_review()))

    loop = _build_loop(backend, tmp_path / "skills")
    out = loop.run("task", workdir=tmp_path)

    assert out.successful
    r2_seeds = [tid for label, tid in backend.resume_history if label == "engineer-r2"]
    assert r2_seeds == ["tid-A1"], r2_seeds


def test_backend_failure_retries_without_poisoned_resume_thread(tmp_path: Path) -> None:
    backend = MemoryBackend()
    backend.queue("matcher", CannedResponse(message='{"matched": []}'))
    backend.queue("distiller", CannedResponse(message=SKILL_MD))
    backend.queue(
        "engineer-r1",
        CannedResponse(
            message="",
            thread_id="poison-backend-thread",
            fatal_error="502 Bad Gateway",
        ),
    )
    backend.queue(
        "engineer-r2",
        CannedResponse(message="recovered after backend restart", thread_id="healthy-thread"),
    )
    backend.queue("reviewer", CannedResponse(message=_done_review()))

    loop = _build_loop(backend, tmp_path / "skills")
    out = loop.run("task", workdir=tmp_path, seed_thread_id="incoming-thread")

    assert out.successful
    assert out.last_thread_id == "healthy-thread"
    engineer_seeds = [
        tid for label, tid in backend.resume_history if label.startswith("engineer-")
    ]
    assert engineer_seeds == ["incoming-thread", None]
    assert [label for label, _, _ in backend.history] == [
        "engineer-r1",
        "engineer-r2",
        "reviewer",
    ]


def test_curated_checkpoint_persists_across_missions_via_file(tmp_path: Path) -> None:
    """With ``checkpoint_path`` set, the reviewer-authored handoff is written to
    disk in mission A and re-loaded into mission B's first engineer round —
    realizing the cross-mission / cross-restart handoff file."""
    ckpt = tmp_path / "state" / "checkpoint.json"

    def _review_with_checkpoint(status: str) -> str:
        return json.dumps({
            "status": status,
            "reason": "handoff authored",
            "next_action": "continue" if status == "continue" else "—",
            "round_summary_markdown": "# r\n",
            "completion_summary_markdown": "done" if status == "done" else "",
            "checkpoint": {
                "goal": "Wire the official BFCL evaluator",
                "done": ["installed tree_sitter parser deps"],
                "open_blocker": "no trained adapter checkpoints exist yet",
                "next_step": "add code/run_condition.py runner",
            },
        })

    def _make_loop(backend: MemoryBackend, skills: Path) -> SkillLoop:
        config = SkillLoopConfig(
            engineer_model="m", reviewer_model="m",
            max_rounds=3, check_commands=[],
            backend_failure_backoff_seconds=0,
            checkpoint_path=ckpt,
        )
        return SkillLoop(
            skills_dir=skills, engineer_runner=backend,
            reviewer_runner=backend, config=config,
        )

    backend_a = MemoryBackend()
    backend_a.queue("matcher", CannedResponse(message='{"matched": []}'))
    backend_a.queue("distiller", CannedResponse(message=SKILL_MD))
    backend_a.queue("engineer-r1", CannedResponse(message="mission A work", thread_id="A1"))
    backend_a.queue("reviewer", CannedResponse(message=_review_with_checkpoint("done")))
    out_a = _make_loop(backend_a, tmp_path / "skillsA").run("task A", workdir=tmp_path)
    assert out_a.successful
    assert ckpt.exists()
    saved = json.loads(ckpt.read_text())
    assert saved["goal"] == "Wire the official BFCL evaluator"
    assert saved["next_step"] == "add code/run_condition.py runner"

    backend_b = MemoryBackend()
    backend_b.queue("matcher", CannedResponse(message='{"matched": []}'))
    backend_b.queue("distiller", CannedResponse(message=SKILL_MD))
    backend_b.queue("engineer-r1", CannedResponse(message="mission B work", thread_id="B1"))
    backend_b.queue("reviewer", CannedResponse(message=_done_review()))
    out_b = _make_loop(backend_b, tmp_path / "skillsB").run("task B", workdir=tmp_path)
    assert out_b.successful

    r1_prompts = [p for label, p, _ in backend_b.history if label == "engineer-r1"]
    assert r1_prompts, "mission B should have an engineer-r1 call"
    prompt = r1_prompts[0]
    assert "CURATED WORKING MEMORY" in prompt
    assert "Wire the official BFCL evaluator" in prompt
    assert "add code/run_condition.py runner" in prompt


def test_no_checkpoint_path_keeps_handoff_in_memory_only(tmp_path: Path) -> None:
    """Without ``checkpoint_path`` (default None), nothing is written to disk
    (legacy behaviour preserved)."""
    def _review_with_cp() -> str:
        return json.dumps({
            "status": "done", "reason": "ok",
            "next_action": "—", "round_summary_markdown": "# r\n",
            "completion_summary_markdown": "done",
            "checkpoint": {"goal": "G", "next_step": "N"},
        })

    backend = MemoryBackend()
    backend.queue("matcher", CannedResponse(message='{"matched": []}'))
    backend.queue("distiller", CannedResponse(message=SKILL_MD))
    backend.queue("engineer-r1", CannedResponse(message="work", thread_id="A1"))
    backend.queue("reviewer", CannedResponse(message=_review_with_cp()))

    loop = _build_loop(backend, tmp_path / "skills")
    out = loop.run("task", workdir=tmp_path)
    assert out.successful
    assert not list(tmp_path.rglob("checkpoint.json"))
