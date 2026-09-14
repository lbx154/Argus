from __future__ import annotations

import json
from pathlib import Path

from argus import SkillLoop, SkillLoopConfig
from argus.adapters.memory_backend import CannedResponse, MemoryBackend
from argus.core import role_session as role_session_module
from argus.core.role_session import _checkpoint_open_items
from argus.planner import Planner, PlannerConfig


def _review(status: str) -> str:
    return json.dumps({
        "status": status,
        "reason": f"review-{status}",
        "next_action": "finish" if status == "done" else "continue",
    })


def test_default_auto_policy_resumes_pi_role_threads_without_machine_configuration(
    tmp_path: Path,
) -> None:
    """A Pi-labelled runner gets rolling role sessions from product defaults."""
    backend = MemoryBackend()
    # The deterministic runner has the same run_exec/resume contract as the
    # real Pi adapter; naming the backend proves auto resolution happens in
    # Argus, not through a local Pi settings file or environment override.
    backend.backend = "pi"  # type: ignore[attr-defined]
    backend.queue("engineer-r1", CannedResponse(message="first", thread_id="pi-engineer"))
    backend.queue("reviewer", CannedResponse(message=_review("continue"), thread_id="pi-reviewer"))
    backend.queue("engineer-r2", CannedResponse(message="second", thread_id="pi-engineer"))
    backend.queue("reviewer", CannedResponse(message=_review("done"), thread_id="pi-reviewer"))

    loop = SkillLoop(
        skills_dir=tmp_path / "skills",
        engineer_runner=backend,
        reviewer_runner=backend,
        config=SkillLoopConfig(
            engineer_model="model",
            reviewer_model="model",
            max_rounds=3,
            backend_failure_backoff_seconds=0,
            context_packet_path=str(tmp_path / "context" / "mission.json"),
        ),
    )
    outcome = loop.run("complete the task", workdir=tmp_path)

    assert outcome.successful
    assert [
        (label, thread)
        for label, thread in backend.resume_history
        if label.startswith("engineer-") or label == "reviewer"
    ] == [
        ("engineer-r1", None),
        ("reviewer", None),
        ("engineer-r2", "pi-engineer"),
        ("reviewer", "pi-reviewer"),
    ]


def test_auto_pi_turn_succeeds_when_optional_checkpoint_was_never_created(
    tmp_path: Path,
) -> None:
    backend = MemoryBackend()
    backend.backend = "pi"  # type: ignore[attr-defined]
    backend.queue(
        "engineer-r1",
        CannedResponse(message="validated successfully", thread_id="pi-engineer"),
    )
    backend.queue(
        "reviewer",
        CannedResponse(message=_review("done"), thread_id="pi-reviewer"),
    )
    context = tmp_path / "handoffs" / "mission-1" / "mission.json"
    checkpoint = context.parent / "CHECKPOINT.md"
    events: list[dict] = []

    outcome = SkillLoop(
        skills_dir=tmp_path / "skills",
        engineer_runner=backend,
        reviewer_runner=backend,
        config=SkillLoopConfig(
            engineer_model="model",
            reviewer_model="model",
            max_rounds=1,
            backend_failure_backoff_seconds=0,
            context_packet_path=str(context),
            checkpoint_path=checkpoint,
        ),
        on_event=events.append,
    ).run("run the decisive validation", workdir=tmp_path)

    assert outcome.successful
    assert outcome.final_message == "validated successfully"
    assert not checkpoint.exists()
    turns = [event for event in events if event.get("type") == "role.session.turn"]
    assert [event["role"] for event in turns] == ["engineer", "reviewer"]
    assert all(event["metadata_persisted"] is True for event in turns)


def test_checkpoint_reader_tolerates_delete_race_and_unreadable_file(
    tmp_path: Path,
    monkeypatch,
) -> None:
    checkpoint = tmp_path / "CHECKPOINT.md"
    checkpoint.write_text("# Open Questions / Blockers\n\n- pending\n", encoding="utf-8")
    checkpoint.unlink()
    assert _checkpoint_open_items(str(checkpoint)) == []

    checkpoint.write_text("unreadable", encoding="utf-8")
    original_read_text = Path.read_text

    def denied(path: Path, *args, **kwargs):
        if path == checkpoint:
            raise PermissionError("simulated unreadable checkpoint")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", denied)
    assert _checkpoint_open_items(str(checkpoint)) == []


def test_capsule_write_failure_cannot_override_engineer_or_reviewer_success(
    tmp_path: Path,
    monkeypatch,
) -> None:
    backend = MemoryBackend()
    backend.backend = "pi"  # type: ignore[attr-defined]
    backend.queue(
        "engineer-r1",
        CannedResponse(message="successful summary", thread_id="pi-engineer"),
    )
    backend.queue(
        "reviewer",
        CannedResponse(message=_review("done"), thread_id="pi-reviewer"),
    )
    context = tmp_path / "handoffs" / "mission-2" / "mission.json"
    events: list[dict] = []
    real_replace = role_session_module.os.replace

    def fail_capsule_replace(source, destination):
        if "role-sessions" in str(destination):
            raise PermissionError("simulated capsule write denial")
        return real_replace(source, destination)

    monkeypatch.setattr(role_session_module.os, "replace", fail_capsule_replace)
    outcome = SkillLoop(
        skills_dir=tmp_path / "skills",
        engineer_runner=backend,
        reviewer_runner=backend,
        config=SkillLoopConfig(
            engineer_model="model",
            reviewer_model="model",
            max_rounds=1,
            backend_failure_backoff_seconds=0,
            context_packet_path=str(context),
            checkpoint_path=context.parent / "CHECKPOINT.md",
        ),
        on_event=events.append,
    ).run("preserve a successful result", workdir=tmp_path)

    assert outcome.successful
    assert outcome.final_message == "successful summary"
    assert (context.parent / "round-0001-engineer.json").is_file()
    turns = [event for event in events if event.get("type") == "role.session.turn"]
    assert [event["role"] for event in turns] == ["engineer", "reviewer"]
    assert all(event["metadata_persisted"] is False for event in turns)
    assert all("PermissionError" in event["persistence_warning"] for event in turns)


def test_planner_result_survives_capsule_write_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    backend = MemoryBackend()
    backend.backend = "pi"  # type: ignore[attr-defined]
    backend.queue(
        "planner.cycle0",
        CannedResponse(
            message="PROJECT_DONE=true\nREASON=verified complete",
            thread_id="pi-planner",
        ),
    )
    events: list[dict] = []
    real_replace = role_session_module.os.replace

    def fail_planner_capsule(source, destination):
        if str(destination).endswith("planner.json"):
            raise PermissionError("simulated planner capsule write denial")
        return real_replace(source, destination)

    monkeypatch.setattr(role_session_module.os, "replace", fail_planner_capsule)
    verdict = Planner(backend).plan_next(
        continuous_objective="verify completion",
        planning_cycle=0,
        config=PlannerConfig(
            working_dir=str(tmp_path),
            role_session_path=tmp_path / "role-sessions" / "planner.json",
            on_event=events.append,
        ),
    )

    assert verdict.project_done is True
    turn = next(event for event in events if event.get("type") == "role.session.turn")
    assert turn["role"] == "planner"
    assert turn["metadata_persisted"] is False
    assert "PermissionError" in turn["persistence_warning"]
