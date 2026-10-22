from __future__ import annotations

import json
import subprocess
from dataclasses import replace
from pathlib import Path

from argus import SkillLoop, SkillLoopConfig
from argus.adapters.memory_backend import CannedResponse, MemoryBackend
from argus.core.models import RunnerResult
from argus.core.role_session import (
    RoleSessionCapsule,
    signal_role_session_file,
)
from argus.planner import Planner, PlannerConfig


def _review(status: str) -> tuple[str, dict]:
    return "submit_review", {'review': f'review-{status}\n\n' + ('finish\n→ engineer' if status == 'continue' else 'none\n→ done')}


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


def _resumable() -> MemoryBackend:
    """A deterministic runner labelled as a CLI that resumes provider threads."""
    backend = MemoryBackend()
    backend.backend = "pi"  # type: ignore[misc]
    return backend


def _loop(
    backend: MemoryBackend,
    tmp_path: Path,
    context: Path,
    checkpoint: Path,
    *,
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
        ),
        on_event=events.append if events is not None else None,
    )


def test_resumable_backend_resumes_each_role_without_crossing_roles(tmp_path: Path) -> None:
    context, checkpoint = _context(tmp_path)
    backend = _resumable()
    backend.queue("engineer-r1", CannedResponse(message="engineer one", thread_id="e1"))
    backend.queue("reviewer", CannedResponse(review_action=_review("continue"), thread_id="v1"))
    backend.queue("engineer-r2", CannedResponse(message="engineer two", thread_id="e1"))
    backend.queue("reviewer", CannedResponse(review_action=_review("done"), thread_id="v1"))
    events: list[dict] = []

    outcome = _loop(
        backend,
        tmp_path,
        context,
        checkpoint,
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
    # Only the Reviewer's closing `→ done` turn ends the work.
    assert [bool(event.get("ends_work")) for event in turns] == [False, False, False, True]


def test_fresh_backend_repeats_task_contract_on_continuation_round(
    tmp_path: Path,
) -> None:
    context, checkpoint = _context(tmp_path)
    backend = MemoryBackend()
    backend.queue("engineer-r1", CannedResponse(message="one", thread_id="e1"))
    backend.queue(
        "reviewer", CannedResponse(review_action=_review("continue"), thread_id="v1")
    )
    backend.queue("engineer-r2", CannedResponse(message="two", thread_id="e2"))
    backend.queue("reviewer", CannedResponse(review_action=_review("done"), thread_id="v2"))

    outcome = _loop(
        backend,
        tmp_path,
        context,
        checkpoint,
    ).run("implement the contract-preserving change", workdir=tmp_path)

    assert outcome.successful
    engineer_prompts = [
        prompt
        for label, prompt, _options in backend.history
        if label.startswith("engineer-")
    ]
    assert len(engineer_prompts) == 2
    assert all('## Task authority' in prompt for prompt in engineer_prompts)
    assert all(
        "implement the contract-preserving change" in prompt
        for prompt in engineer_prompts
    )


def test_resumed_reviewer_reduces_prompt_bytes_without_changing_verdict(
    tmp_path: Path,
) -> None:
    def run(backend: MemoryBackend, root: Path) -> tuple[str, int]:
        context, checkpoint = _context(root)
        backend.queue("engineer-r1", CannedResponse(message="one", thread_id="e1"))
        backend.queue(
            "reviewer", CannedResponse(review_action=_review("continue"), thread_id="v1")
        )
        backend.queue("engineer-r2", CannedResponse(message="two", thread_id="e2"))
        backend.queue(
            "reviewer", CannedResponse(review_action=_review("done"), thread_id="v2")
        )
        outcome = _loop(
            backend,
            root,
            context,
            checkpoint,
        ).run("same matched task", workdir=root)
        reviewer_chars = sum(
            len(prompt)
            for label, prompt, _options in backend.history
            if label == "reviewer"
        )
        return outcome.status, reviewer_chars

    fresh_status, fresh_chars = run(MemoryBackend(), tmp_path / "fresh")
    resumed_status, resumed_chars = run(_resumable(), tmp_path / "resumed")

    assert fresh_status == resumed_status == "done"
    assert resumed_chars < fresh_chars


def test_a_role_with_no_thread_to_resume_is_told_the_round(tmp_path: Path) -> None:
    context, checkpoint = _context(tmp_path)
    backend = _resumable()
    # The provider reports no thread, so round 2 cannot resume round 1.
    backend.queue("engineer-r1", CannedResponse(message="one"))
    backend.queue("reviewer", CannedResponse(review_action=_review("continue")))
    backend.queue("engineer-r2", CannedResponse(message="two"))
    backend.queue("reviewer", CannedResponse(review_action=_review("done")))

    outcome = _loop(
        backend,
        tmp_path,
        context,
        checkpoint,
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


def test_capsules_restore_same_mission_after_process_restart(tmp_path: Path) -> None:
    context, checkpoint = _context(tmp_path)
    first = _resumable()
    first.queue("engineer-r1", CannedResponse(message="partial", thread_id="e1"))
    first.queue("reviewer", CannedResponse(review_action=_review("continue"), thread_id="v1"))
    first_loop = _loop(first, tmp_path, context, checkpoint)
    first_loop.config.max_rounds = 1
    interrupted = first_loop.run("same objective", workdir=tmp_path)

    assert interrupted.status == "max_rounds"
    assert not interrupted.successful
    assert [label for label, _prompt, _options in first.history] == [
        "engineer-r1",
        "reviewer",
    ]
    capsules = context.parent / "role-sessions"
    engineer_before_restart = json.loads(
        (capsules / "engineer.json").read_text(encoding="utf-8")
    )
    reviewer_before_restart = json.loads(
        (capsules / "reviewer.json").read_text(encoding="utf-8")
    )
    assert engineer_before_restart["thread_id"] == "e1"
    assert engineer_before_restart["decisive_output"] == "partial"
    assert reviewer_before_restart["thread_id"] == "v1"
    assert "partial" not in reviewer_before_restart["decisive_output"]

    second = _resumable()
    second.queue("engineer-r1", CannedResponse(message="finished", thread_id="e1"))
    second.queue("reviewer", CannedResponse(review_action=_review("done"), thread_id="v1"))
    outcome = _loop(second, tmp_path, context, checkpoint).run(
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
    assert str(context.parent / "role-sessions" / "reviewer.json") not in prompt
    assert [label for label, _prompt, _options in second.history] == [
        "engineer-r1",
        "reviewer",
    ]


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

    assert capsule.prepare() is None
    assert capsule.action == "rotated"
    assert capsule.rotation_reason == "branch_changed"


def test_a_long_session_keeps_resuming(tmp_path: Path) -> None:
    capsule = RoleSessionCapsule.open(
        role="engineer",
        policy="rolling",
        objective_revision="v1",
        workdir=tmp_path,
        backend="codex",
        model="model",
        checkpoint_path=None,
        path=tmp_path / "state" / "engineer.json",
    )
    for _ in range(30):
        capsule.prepare()
        capsule.complete(RunnerResult(
            exit_code=0, thread_id="thread-1", input_tokens=400_000, cached_input_tokens=0,
        ))

    # The backend compacts its own context; Argus never rotates on size or turns.
    assert capsule.prepare() == "thread-1"
    assert capsule.action == "resumed"
    assert capsule.turns == 30


def test_planner_session_survives_new_planner_instance(tmp_path: Path) -> None:
    backend = _resumable()
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
        role_session_path=capsule_path,
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


def test_a_manager_decision_on_the_same_objective_keeps_the_planner_session(
    tmp_path: Path,
) -> None:
    """2026-10-20: every Manager WAIT or steer advanced the campaign generation,
    which was part of the Planner session's identity, so each Planner turn
    opened a new session ("rotated") and paid for its whole context again."""
    from argus.daemon.state import write_continuous_config
    from argus.life.event_log import JsonlEventSink
    from argus.life.memory import LifeMemory
    from argus.life.supervisor import LifeSupervisor, LifeSupervisorConfig

    life = tmp_path / "life"
    memory = LifeMemory.open(life)
    write_continuous_config(life, enabled=True, objective="pick A or B, then change truncate")
    supervisor = LifeSupervisor(
        memory=memory, runner=object(), planner_runner=object(),
        sink=JsonlEventSink(None, life_dir=memory.root),
        config=LifeSupervisorConfig(
            continuous=True, continuous_objective="pick A or B, then change truncate",
            project_worktree=tmp_path, artifact_root=tmp_path,
        ),
    )
    backend = _resumable()
    for cycle in range(2):
        backend.queue(
            f"planner.cycle{cycle}",
            CannedResponse(message="PROJECT_DONE=false\nREASON=waiting\n→ operator: A or B?", thread_id="p1"),
        )
    backend.queue("planner.cycle2", CannedResponse(message="PROJECT_DONE=true\nREASON=done", thread_id="p1"))
    events: list[dict] = []
    for cycle in range(2):
        # A Manager WAIT or steer reserves the campaign: a new generation, same objective.
        write_continuous_config(life, enabled=True, objective="pick A or B, then change truncate")
        Planner(backend).plan_next(
            continuous_objective="pick A or B, then change truncate",
            planning_cycle=cycle,
            config=replace(supervisor._planner_config(), on_event=events.append),
        )

    assert [thread for label, thread in backend.resume_history] == [None, "p1"]
    assert [event["action"] for event in events if event["type"] == "role.session.turn"] == [
        "fresh", "resumed",
    ]
    # Declaring the project done ends the work; a turn that waits does not.
    Planner(backend).plan_next(
        continuous_objective="pick A or B, then change truncate", planning_cycle=2,
        config=replace(supervisor._planner_config(), on_event=events.append),
    )
    assert [bool(event.get("ends_work")) for event in events if event["type"] == "role.session.turn"] == [
        False, False, True,
    ]


def test_explicit_reviewer_quality_signal_rotates_only_target_role(tmp_path: Path) -> None:
    context, checkpoint = _context(tmp_path)
    backend = _resumable()
    backend.queue("engineer-r1", CannedResponse(message="first", thread_id="e1"))
    backend.queue(
        "reviewer",
        CannedResponse(
            review_action=("submit_review", {'review': (('The Engineer repeated an obsolete repair.') + '\n\n' + ('Use the current frontier and repair the active cluster.')) + "\n→ engineer", 'session_signal': {'kind': 'quality_degradation', 'target': 'engineer', 'detail': 'Repeated the obsolete repair after a current handoff.'}}),
            thread_id="v1",
        ),
    )
    backend.queue("engineer-r2", CannedResponse(message="second", thread_id="e2"))
    backend.queue("reviewer", CannedResponse(review_action=_review("done"), thread_id="v1"))
    events: list[dict] = []

    outcome = _loop(
        backend,
        tmp_path,
        context,
        checkpoint,
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
        "policy": "rolling",
        "thread_id": "old-thread",
        "turns": 4,
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


def test_reviewer_planner_signal_rotates_the_project_planner_capsule(tmp_path: Path) -> None:
    context, checkpoint = _context(tmp_path)
    # The Planner's capsule is project-level, beside ``handoffs/``.
    planner_capsule = tmp_path / "state" / "role-sessions" / "planner.json"
    planner_capsule.parent.mkdir(parents=True)
    planner_capsule.write_text(json.dumps({
        "schema_version": 2,
        "role": "planner",
        "policy": "rolling",
        "thread_id": "planner-old",
        "turns": 3,
    }), encoding="utf-8")
    backend = _resumable()
    backend.queue("engineer-r1", CannedResponse(message="first", thread_id="e1"))
    backend.queue(
        "reviewer",
        CannedResponse(
            review_action=("submit_review", {'review': 'The plan step is complete.\n→ done', 'session_signal': {'kind': 'repeated_contradiction', 'target': 'planner', 'detail': 'The plan keeps asserting a stage that the record contradicts.'}}),
            thread_id="v1",
        ),
    )
    events: list[dict] = []

    outcome = _loop(
        backend, tmp_path, context, checkpoint, events=events,
    ).run("finish the plan step", workdir=tmp_path)

    assert outcome.successful
    payload = json.loads(planner_capsule.read_text(encoding="utf-8"))
    assert payload["thread_id"] == ""
    assert payload["signal_kind"] == "repeated_contradiction"
    assert not (context.parent / "role-sessions" / "planner.json").exists()
    signal = next(
        event for event in events
        if event.get("rotation_reason") == "signal:repeated_contradiction"
    )
    assert signal["role"] == "planner"
    assert signal["capsule_path"] == str(planner_capsule)


def test_rotated_capsule_does_not_resume_the_seed_thread(tmp_path: Path) -> None:
    path = tmp_path / "role-sessions" / "engineer.json"
    common = {
        "role": "engineer",
        "policy": "rolling",
        "workdir": tmp_path,
        "backend": "memory",
        "model": "model",
        "checkpoint_path": None,
        "path": path,
    }
    first = RoleSessionCapsule.open(objective_revision="objective-a", **common)
    first.prepare()
    first.complete(RunnerResult(exit_code=0, thread_id="thread-old"))

    same = RoleSessionCapsule.open(
        objective_revision="objective-a", seed_thread_id="thread-old", **common,
    )
    assert same.prepare() == "thread-old"

    changed = RoleSessionCapsule.open(
        objective_revision="objective-b", seed_thread_id="thread-old", **common,
    )
    assert changed.rotation_reason == "objective_revision_changed"
    assert changed.prepare() is None

    changed.complete(RunnerResult(exit_code=0, thread_id="thread-new"))
    assert signal_role_session_file(path, "quality_degradation")
    signalled = RoleSessionCapsule.open(
        objective_revision="objective-b", seed_thread_id="thread-new", **common,
    )
    assert signalled.prepare() is None
