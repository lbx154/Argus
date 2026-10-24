from __future__ import annotations

from pathlib import Path

from argus import SkillLoop, SkillLoopConfig
from argus.adapters.memory_backend import CannedResponse, MemoryBackend
from argus.core import role_session as role_session_module
from argus.core.role_session import _checkpoint_open_items
from argus.planner import Planner, PlannerConfig
from tests.hand_off import hand_off, plan


def _review(status: str) -> tuple[str, dict]:
    return hand_off("done" if status == "done" else "engineer", f"review-{status}")


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
        CannedResponse(action=_review("done"), thread_id="pi-reviewer"),
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


def test_planner_result_survives_capsule_write_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    backend = MemoryBackend()
    backend.backend = "pi"  # type: ignore[attr-defined]
    backend.queue(
        "planner.cycle0",
        CannedResponse(
            action=plan('done', summary='verified complete'),
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
