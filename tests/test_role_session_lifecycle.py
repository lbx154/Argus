from __future__ import annotations

import json
import subprocess
from pathlib import Path

from argus_skill import SkillLoop, SkillLoopConfig
from argus_skill.adapters.memory_backend import CannedResponse, MemoryBackend
from argus_skill.core.models import RunnerResult
from argus_skill.core.role_session import (
    RoleSessionCapsule,
    signal_role_session_file,
)
from argus_skill.planner import Planner, PlannerConfig


def _review(status: str) -> str:
    return json.dumps(
        {
            "status": status,
            "reason": f"review-{status}",
            "next_action": "finish" if status == "continue" else "none",
        }
    )


def _context(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "state" / "handoffs" / "mission-1"
    root.mkdir(parents=True)
    context = root / "mission.json"
    context.write_text(
        json.dumps(
            {
                "kind": "mission_context",
                "mission_id": "mission-1",
                "context_refs": [
                    {"kind": "source", "ref": "src/core.py", "why": "target"}
                ],
            }
        ),
        encoding="utf-8",
    )
    checkpoint = root / "CHECKPOINT.md"
    checkpoint.write_text(
        "# Open Questions / Blockers\n\n- verify the edge case\n",
        encoding="utf-8",
    )
    return context, checkpoint


def _loop(
    backend: MemoryBackend,
    tmp_path: Path,
    context: Path,
    checkpoint: Path,
    *,
    policy: str,
    max_turns: int = 6,
    events: list[dict] | None = None,
) -> SkillLoop:
    return SkillLoop(
        skills_dir=tmp_path / "skills",
        engineer_runner=backend,
        reviewer_runner=backend,
        config=SkillLoopConfig(
            engineer_model="model",
            reviewer_model="model",
            max_rounds=3,
            backend_failure_backoff_seconds=0,
            context_packet_path=str(context),
            checkpoint_path=checkpoint,
            role_session_policy=policy,
            role_session_max_turns=max_turns,
        ),
        on_event=events.append if events is not None else None,
    )


def test_mission_policy_resumes_each_role_without_crossing_roles(tmp_path: Path) -> None:
    context, checkpoint = _context(tmp_path)
    backend = MemoryBackend()
    backend.queue("engineer-r1", CannedResponse(message="engineer one", thread_id="e1"))
    backend.queue("reviewer", CannedResponse(message=_review("continue"), thread_id="v1"))
    backend.queue("engineer-r2", CannedResponse(message="engineer two", thread_id="e1"))
    backend.queue("reviewer", CannedResponse(message=_review("done"), thread_id="v1"))
    events: list[dict] = []

    outcome = _loop(
        backend,
        tmp_path,
        context,
        checkpoint,
        policy="mission",
        events=events,
    ).run("implement the change", workdir=tmp_path)

    assert outcome.successful
    assert [
        (label, thread)
        for label, thread in backend.resume_history
        if label.startswith("engineer-") or label == "reviewer"
    ] == [
        ("engineer-r1", None),
        ("reviewer", None),
        ("engineer-r2", "e1"),
        ("reviewer", "v1"),
    ]
    capsules = context.parent / "role-sessions"
    engineer = json.loads((capsules / "engineer.json").read_text(encoding="utf-8"))
    reviewer = json.loads((capsules / "reviewer.json").read_text(encoding="utf-8"))
    assert engineer["inspected_paths"] == ["src/core.py"]
    assert engineer["open_hypotheses"] == ["- verify the edge case"]
    assert engineer["decisive_output"] == "engineer two"
    assert "engineer two" not in reviewer["decisive_output"]
    turns = [event for event in events if event.get("type") == "role.session.turn"]
    assert [(event["role"], event["action"]) for event in turns] == [
        ("engineer", "fresh"),
        ("reviewer", "fresh"),
        ("engineer", "resumed"),
        ("reviewer", "resumed"),
    ]


def test_fresh_policy_repeats_task_contract_on_continuation_round(
    tmp_path: Path,
) -> None:
    context, checkpoint = _context(tmp_path)
    backend = MemoryBackend()
    backend.queue("engineer-r1", CannedResponse(message="one", thread_id="e1"))
    backend.queue(
        "reviewer", CannedResponse(message=_review("continue"), thread_id="v1")
    )
    backend.queue("engineer-r2", CannedResponse(message="two", thread_id="e2"))
    backend.queue("reviewer", CannedResponse(message=_review("done"), thread_id="v2"))

    outcome = _loop(
        backend,
        tmp_path,
        context,
        checkpoint,
        policy="fresh",
    ).run("implement the contract-preserving change", workdir=tmp_path)

    assert outcome.successful
    engineer_prompts = [
        prompt
        for label, prompt, _options in backend.history
        if label.startswith("engineer-")
    ]
    assert len(engineer_prompts) == 2
    assert all("## Effective task contract" in prompt for prompt in engineer_prompts)
    assert all(
        "implement the contract-preserving change" in prompt
        for prompt in engineer_prompts
    )


def test_resumed_reviewer_reduces_prompt_bytes_without_changing_verdict(
    tmp_path: Path,
) -> None:
    def run(policy: str, root: Path) -> tuple[str, int]:
        context, checkpoint = _context(root)
        backend = MemoryBackend()
        backend.queue("engineer-r1", CannedResponse(message="one", thread_id="e1"))
        backend.queue(
            "reviewer", CannedResponse(message=_review("continue"), thread_id="v1")
        )
        backend.queue("engineer-r2", CannedResponse(message="two", thread_id="e2"))
        backend.queue(
            "reviewer", CannedResponse(message=_review("done"), thread_id="v2")
        )
        outcome = _loop(
            backend,
            root,
            context,
            checkpoint,
            policy=policy,
        ).run("same matched task", workdir=root)
        reviewer_chars = sum(
            len(prompt)
            for label, prompt, _options in backend.history
            if label == "reviewer"
        )
        return outcome.status, reviewer_chars

    fresh_status, fresh_chars = run("fresh", tmp_path / "fresh")
    mission_status, mission_chars = run("mission", tmp_path / "mission")

    assert fresh_status == mission_status == "done"
    assert mission_chars < fresh_chars


def test_rolling_policy_rotates_at_the_turn_limit(tmp_path: Path) -> None:
    context, checkpoint = _context(tmp_path)
    backend = MemoryBackend()
    backend.queue("engineer-r1", CannedResponse(message="one", thread_id="e1"))
    backend.queue("reviewer", CannedResponse(message=_review("continue"), thread_id="v1"))
    backend.queue("engineer-r2", CannedResponse(message="two", thread_id="e2"))
    backend.queue("reviewer", CannedResponse(message=_review("done"), thread_id="v2"))
    events: list[dict] = []

    outcome = _loop(
        backend,
        tmp_path,
        context,
        checkpoint,
        policy="rolling",
        max_turns=1,
        events=events,
    ).run("implement the change", workdir=tmp_path)

    assert outcome.successful
    engineer_prompts = [
        prompt for label, prompt, _options in backend.history
        if label.startswith("engineer-")
    ]
    assert all("## Current mission task" in prompt for prompt in engineer_prompts)
    assert "This is mission round 2, not round 1" in engineer_prompts[1]
    assert "do not yield to ask for that approval again" in engineer_prompts[1]
    reviewer_prompts = [
        prompt for label, prompt, _options in backend.history if label == "reviewer"
    ]
    assert "This is Reviewer round 2, not round 1" in reviewer_prompts[1]
    assert "Do not reenact an earlier Engineer stage" in reviewer_prompts[1]
    assert [
        thread
        for label, thread in backend.resume_history
        if label.startswith("engineer-") or label == "reviewer"
    ] == [None, None, None, None]
    rotations = [
        event
        for event in events
        if event.get("type") == "role.session.turn"
        and event.get("action") == "rotated"
    ]
    assert [(event["role"], event["rotation_reason"]) for event in rotations] == [
        ("engineer", "turn_limit"),
        ("reviewer", "turn_limit"),
    ]


def test_capsules_restore_same_mission_after_process_restart(tmp_path: Path) -> None:
    context, checkpoint = _context(tmp_path)
    first = MemoryBackend()
    first.queue("engineer-r1", CannedResponse(message="partial", thread_id="e1"))
    first.queue("reviewer", CannedResponse(message=_review("continue"), thread_id="v1"))
    first_loop = _loop(first, tmp_path, context, checkpoint, policy="mission")
    first_loop.config.max_rounds = 1
    first_loop.config.hard_escalate_rounds = 0
    assert first_loop.run("same objective", workdir=tmp_path).status == "max_rounds"

    second = MemoryBackend()
    second.queue("engineer-r1", CannedResponse(message="finished", thread_id="e1"))
    second.queue("reviewer", CannedResponse(message=_review("done"), thread_id="v1"))
    outcome = _loop(second, tmp_path, context, checkpoint, policy="mission").run(
        "same objective", workdir=tmp_path
    )

    assert outcome.successful
    assert [
        (label, thread)
        for label, thread in second.resume_history
        if label.startswith("engineer-") or label == "reviewer"
    ] == [("engineer-r1", "e1"), ("reviewer", "v1")]
    prompt = next(prompt for label, prompt, _ in second.history if label == "engineer-r1")
    assert str(context.parent / "role-sessions" / "engineer.json") in prompt


def test_rolling_capsule_rotates_when_branch_changes(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    capsule_path = tmp_path / "state" / "engineer.json"
    capsule = RoleSessionCapsule.open(
        role="engineer",
        policy="rolling",
        objective_revision="v1",
        workdir=tmp_path,
        backend="codex",
        model="model",
        checkpoint_path=None,
        path=capsule_path,
    )
    capsule.complete(RunnerResult(exit_code=0, thread_id="thread-1"))
    subprocess.run(
        ["git", "-C", str(tmp_path), "checkout", "-q", "-b", "other"],
        check=True,
    )

    assert capsule.prepare(max_turns=6, max_input_tokens=120_000) is None
    assert capsule.action == "rotated"
    assert capsule.rotation_reason == "branch_changed"


def test_planner_mission_session_survives_new_planner_instance(tmp_path: Path) -> None:
    backend = MemoryBackend()
    backend.queue(
        "planner.cycle0",
        CannedResponse(message="PROJECT_DONE=true\nREASON=done", thread_id="p1"),
    )
    backend.queue(
        "planner.cycle1",
        CannedResponse(message="PROJECT_DONE=true\nREASON=done", thread_id="p1"),
    )
    capsule_path = tmp_path / "state" / "planner.json"
    config = PlannerConfig(
        model="model",
        working_dir=str(tmp_path),
        role_session_policy="mission",
        role_session_path=capsule_path,
        objective_revision="generation-1",
    )

    Planner(backend).plan_next(
        continuous_objective="maintain the project",
        planning_cycle=0,
        config=config,
    )
    Planner(backend).plan_next(
        continuous_objective="maintain the project",
        planning_cycle=1,
        config=config,
    )

    assert [
        thread for label, thread in backend.resume_history if label.startswith("planner.")
    ] == [None, "p1"]
    assert json.loads(capsule_path.read_text(encoding="utf-8"))["role"] == "planner"
    planner_prompts = [
        prompt for label, prompt, _options in backend.history if label.startswith("planner.")
    ]
    assert "## Continued Planner cycle" in planner_prompts[1]
    assert len(planner_prompts[1]) < len(planner_prompts[0])


def test_explicit_reviewer_quality_signal_rotates_only_target_role(tmp_path: Path) -> None:
    context, checkpoint = _context(tmp_path)
    backend = MemoryBackend()
    backend.queue("engineer-r1", CannedResponse(message="first", thread_id="e1"))
    backend.queue(
        "reviewer",
        CannedResponse(
            message=json.dumps({
                "status": "continue",
                "reason": "The Engineer repeated an obsolete repair.",
                "next_action": "Use the current frontier and repair the active cluster.",
                "session_signal": {
                    "kind": "quality_degradation",
                    "target": "engineer",
                    "detail": "Repeated the obsolete repair after a current handoff.",
                },
            }),
            thread_id="v1",
        ),
    )
    backend.queue("engineer-r2", CannedResponse(message="second", thread_id="e2"))
    backend.queue("reviewer", CannedResponse(message=_review("done"), thread_id="v1"))
    events: list[dict] = []

    outcome = _loop(
        backend,
        tmp_path,
        context,
        checkpoint,
        policy="mission",
        events=events,
    ).run("repair the current cluster", workdir=tmp_path)

    assert outcome.successful
    assert [
        (label, thread)
        for label, thread in backend.resume_history
        if label.startswith("engineer-") or label == "reviewer"
    ] == [
        ("engineer-r1", None),
        ("reviewer", None),
        ("engineer-r2", None),
        ("reviewer", "v1"),
    ]
    signal = next(
        event
        for event in events
        if event.get("rotation_reason") == "signal:quality_degradation"
    )
    assert signal["role"] == "engineer"
    assert signal["signal_detail"].startswith("Repeated the obsolete")


def test_cross_role_signal_file_migrates_old_capsule_and_forces_fresh_thread(
    tmp_path: Path,
) -> None:
    path = tmp_path / "planner.json"
    path.write_text(json.dumps({
        "schema_version": 1,
        "role": "planner",
        "policy": "mission",
        "thread_id": "old-thread",
        "turns": 4,
        "input_tokens": 100,
    }), encoding="utf-8")

    assert signal_role_session_file(
        path,
        "repeated_contradiction",
        "Two turns asserted incompatible stage facts.",
    )

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 2
    assert payload["thread_id"] == ""
    assert payload["turns"] == 0
    assert payload["signal_kind"] == "repeated_contradiction"
