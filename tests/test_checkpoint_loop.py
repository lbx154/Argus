"""Integration test: curated-checkpoint injection + structural session roll.

Verifies the amnesia-loop fix end to end through SkillLoop:
  1. The reviewer-authored `checkpoint` reaches the NEXT engineer round's
     prompt as curated working memory.
  2. The Codex session is proactively ROLLED once a thread reaches the shift
     limit — the post-roll engineer round resumes from no thread id (fresh
     session), so per-session context cannot grow without bound.
"""
from __future__ import annotations

import json
from pathlib import Path

from argus_skill import SkillLoop, SkillLoopConfig
from argus_skill.adapters.memory_backend import CannedResponse, MemoryBackend

SKILL_MD = (
    "## Title\nDemo skill\n\n"
    "## Description\nA fixed playbook for the checkpoint test.\n\n"
    "## Category\ndemo\n\n"
    "## When to use\n- demo task\n\n"
    "## When NOT to use\n- production code\n\n"
    "## How to solve\n- Do the thing.\n\n"
    "## Examples\n- demo → done\n\n"
    "## Response shape\n- Reply inline.\n"
)


def _continue_review(checkpoint: dict | None = None) -> str:
    payload = {
        "status": "continue",
        "confidence": 0.4,
        "reason": "More work needed.",
        "next_action": "Keep going.",
        "round_summary_markdown": "# r\n",
        "completion_summary_markdown": "",
    }
    if checkpoint is not None:
        payload["checkpoint"] = checkpoint
    return json.dumps(payload)


def _done_review() -> str:
    return json.dumps({
        "status": "done",
        "confidence": 0.95,
        "reason": "Met criterion.",
        "next_action": "—",
        "round_summary_markdown": "# done\n",
        "completion_summary_markdown": "Done.",
    })


def test_checkpoint_injection_and_session_roll(tmp_path: Path, monkeypatch) -> None:
    # Roll the codex session after just 2 rounds on a thread.
    monkeypatch.setenv("ARGUS_SKILL_SHIFT_ROUND_LIMIT", "2")

    backend = MemoryBackend()
    backend.queue("matcher", CannedResponse(message='{"matched": []}'))
    backend.queue("distiller", CannedResponse(message=SKILL_MD))

    # Round 1: engineer on fresh thread "t1"; reviewer authors a checkpoint
    # carrying a distinctive marker the next round must see.
    backend.queue("engineer-r1", CannedResponse(
        message="Round 1 work.", thread_id="t1",
    ))
    backend.queue("reviewer", CannedResponse(message=_continue_review({
        "goal": "ship the thing",
        "done": ["MARKER_ALPHA: wrote the module"],
        "tried_and_failed": ["MARKER_BETA: approach X collapses"],
        "open_blocker": "MARKER_GAMMA: conditions identical",
        "next_step": "MARKER_DELTA: redesign separation",
    })))

    # Round 2: same thread "t1" (rounds_on_thread reaches the limit=2).
    backend.queue("engineer-r2", CannedResponse(
        message="Round 2 work.", thread_id="t1",
    ))
    backend.queue("reviewer", CannedResponse(message=_continue_review({
        "goal": "ship the thing",
        "done": ["MARKER_ALPHA: wrote the module"],
        "tried_and_failed": [],
        "open_blocker": "",
        "next_step": "finish up",
    })))

    # Round 3: MUST start a fresh session (rolled) → resume_thread_id is None.
    backend.queue("engineer-r3", CannedResponse(
        message="Round 3 work.", thread_id="t2",
    ))
    backend.queue("reviewer", CannedResponse(message=_done_review()))

    loop = SkillLoop(
        skills_dir=tmp_path / "skills",
        engineer_runner=backend,
        reviewer_runner=backend,
        config=SkillLoopConfig(max_rounds=5),
    )
    outcome = loop.run("demo task", workdir=tmp_path)
    assert outcome.successful, f"{outcome.status}: {outcome.reason}"
    assert outcome.round_count == 3, outcome

    history = {label: prompt for label, prompt, _ in backend.history}

    # (1) Round-1 checkpoint reached round 2's engineer prompt as memory.
    r2 = history["engineer-r2"]
    assert "CURATED WORKING MEMORY" in r2
    assert "MARKER_ALPHA" in r2
    assert "MARKER_BETA" in r2   # tried_and_failed must survive
    assert "MARKER_GAMMA" in r2  # open blocker must survive
    assert "MARKER_DELTA" in r2  # next step must survive

    # Round-1 engineer had no prior memory.
    assert "first session" in history["engineer-r1"].lower()

    # (2) Session rolled before round 3 → fresh session, no resume id.
    resume = dict(
        (label, tid) for label, tid in backend.resume_history
        if label.startswith("engineer-")
    )
    assert resume["engineer-r1"] is None      # nothing to resume yet
    assert resume["engineer-r2"] == "t1"      # still within shift window
    assert resume["engineer-r3"] is None      # ROLLED — fresh session


def test_no_checkpoint_keeps_prior_memory(tmp_path: Path, monkeypatch) -> None:
    """A reviewer verdict that omits `checkpoint` must not wipe memory."""
    monkeypatch.setenv("ARGUS_SKILL_SHIFT_ROUND_LIMIT", "0")  # disable roll

    backend = MemoryBackend()
    backend.queue("matcher", CannedResponse(message='{"matched": []}'))
    backend.queue("distiller", CannedResponse(message=SKILL_MD))

    backend.queue("engineer-r1", CannedResponse(message="r1", thread_id="t1"))
    backend.queue("reviewer", CannedResponse(message=_continue_review({
        "goal": "g", "done": ["MARKER_KEEP"], "tried_and_failed": [],
        "open_blocker": "", "next_step": "",
    })))
    # Round 2 reviewer omits checkpoint entirely.
    backend.queue("engineer-r2", CannedResponse(message="r2", thread_id="t1"))
    backend.queue("reviewer", CannedResponse(message=_continue_review(None)))
    # Round 3 must still carry MARKER_KEEP from round 1.
    backend.queue("engineer-r3", CannedResponse(message="r3", thread_id="t1"))
    backend.queue("reviewer", CannedResponse(message=_done_review()))

    loop = SkillLoop(
        skills_dir=tmp_path / "skills",
        engineer_runner=backend,
        reviewer_runner=backend,
        config=SkillLoopConfig(max_rounds=5),
    )
    outcome = loop.run("demo task", workdir=tmp_path)
    assert outcome.successful, f"{outcome.status}: {outcome.reason}"

    history = {label: prompt for label, prompt, _ in backend.history}
    assert "MARKER_KEEP" in history["engineer-r3"]
