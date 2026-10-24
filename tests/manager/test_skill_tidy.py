from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from argus.core.models import RunnerOptions, RunnerResult
from argus.manager.skill_tidy import names_the_verifier, propagate_after_mission
from argus.skills.layered import LayeredSkillStore
from argus.skills.missions import EngineerMission


@pytest.fixture(autouse=True)
def selected_software_vertical(monkeypatch):
    monkeypatch.setattr('argus.manager.skill_tidy.resolve_skill_scope', lambda _: 'software')


@dataclass
class _PromotingBackend:
    shared_root: Path
    calls: list[dict[str, Any]] = field(default_factory=list)

    def run_exec(
        self,
        *,
        prompt: str,
        options: RunnerOptions,
        run_label: str,
        resume_thread_id: str | None = None,
    ) -> RunnerResult:
        self.calls.append({
            "prompt": prompt,
            "options": options,
            "run_label": run_label,
            "resume_thread_id": resume_thread_id,
        })
        destination = self.shared_root / "engineer" / "verified-debugging.md"
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            "---\n"
            "name: verified debugging\n"
            "description: Reuse a verified debugging procedure\n"
            "---\n\n"
            "Verify the reduced reproducer before changing the implementation.\n",
            encoding="utf-8",
        )
        return RunnerResult(exit_code=0, agent_messages=["review complete"])


def test_team_learning_promotes_to_vertical_and_matching_session_discovers_it(
    tmp_path: Path,
) -> None:
    project = tmp_path / "workspace"
    state = tmp_path / "session-state"
    global_root = tmp_path / "profile-skills"
    shared = global_root / "_shared_verticals" / "software"
    project.mkdir()
    candidate = project / ".agents" / "skills" / "debugging-candidate" / "SKILL.md"
    candidate.parent.mkdir(parents=True)
    candidate.write_text(
        "---\n"
        "name: debugging candidate\n"
        "description: Candidate procedure from this project\n"
        "---\n",
        encoding="utf-8",
    )
    backend = _PromotingBackend(shared)
    events: list[dict[str, Any]] = []

    counts = propagate_after_mission(
        project,
        backend,
        project_state_dir=state,
        shared_root=global_root,
        mission_objective="Repair the parser and verify the reduced reproducer",
        on_event=events.append,
    )

    promoted_dir = (shared / "engineer").resolve()
    next_session = LayeredSkillStore(
        project_dir=tmp_path / "next-session-skills",
        global_dir=global_root,
        vertical_dir=shared,
    )
    assert backend.calls[0]["options"].sandbox_mode == "workspace-write"
    assert backend.calls[0]["options"].dangerous_yolo is False
    assert "cannot publish or edit global Skills" in backend.calls[0]["prompt"]
    assert "checked different-input or boundary case" in backend.calls[0]["prompt"]
    assert counts["to_vertical_shared"] == 1
    assert counts["to_shared"] == 0
    assert not (global_root / "engineer").exists()
    assert counts["quarantined"] == 0
    assert candidate.exists()
    assert promoted_dir in EngineerMission(next_session).libraries().native_paths
    assert backend.calls[0]["run_label"] == "team-learning-review"
    assert backend.calls[0]["options"].working_dir == str(shared.resolve())
    assert backend.calls[0]["options"].add_dirs is None
    assert "only location you may edit" in backend.calls[0]["prompt"]
    assert "Project-specific or still-unverified learning stays" in backend.calls[0]["prompt"]
    assert "Candidate procedure from this project" in backend.calls[0]["prompt"]
    assert "Never inspect the project or session directories" in backend.calls[0]["prompt"]
    assert "`agent_io.jsonl`" in backend.calls[0]["prompt"]
    assert "may be promoted after that one success" in backend.calls[0]["prompt"]
    assert "Do not reject it merely because it came from one session" in (
        backend.calls[0]["prompt"]
    )
    assert "done verdict verifies only that mission's accepted output" in (
        backend.calls[0]["prompt"]
    )
    assert "phase attribution/profiling or a controlled comparison" in (
        backend.calls[0]["prompt"]
    )
    assert [event["type"] for event in events] == [
        "team.learning.review.started",
        "team.learning.review.completed",
    ]

    second_events: list[dict[str, Any]] = []
    second = propagate_after_mission(
        project,
        backend,
        project_state_dir=state,
        shared_root=global_root,
        mission_objective="Repair another parser edge case",
        on_event=second_events.append,
    )

    assert second["to_shared"] == 0
    assert len(backend.calls) == 1
    assert [event["type"] for event in second_events] == [
        "team.learning.review.skipped"
    ]
    assert second_events[0]["reason"] == "no project skill delta"

    candidate.write_text(
        candidate.read_text(encoding="utf-8")
        + "\nCheck the minimized case against the original input.\n",
        encoding="utf-8",
    )
    propagate_after_mission(
        project,
        backend,
        project_state_dir=state,
        shared_root=global_root,
        mission_objective="Repair a changed parser procedure",
    )
    assert len(backend.calls) == 2


@dataclass
class _SilentBackend:
    """Records the prompt and writes nothing."""

    calls: list[dict[str, Any]] = field(default_factory=list)

    def run_exec(
        self,
        *,
        prompt: str,
        options: RunnerOptions,
        run_label: str,
        resume_thread_id: str | None = None,
    ) -> RunnerResult:
        self.calls.append({"prompt": prompt, "options": options})
        return RunnerResult(exit_code=0, agent_messages=["no durable procedure"])


def test_names_the_verifier_reports_which_name_it_found() -> None:
    assert names_the_verifier("run `complete_final_stage` first") == (
        "complete_final_stage"
    )
    assert names_the_verifier("edit .argus/PIPELINE_STATE.json") == (
        "PIPELINE_STATE.json"
    )
    assert names_the_verifier("reduce the reproducer before editing") == ""
    assert names_the_verifier("") == ""


def test_a_candidate_naming_the_verifier_is_withheld_from_the_evidence(
    tmp_path: Path,
) -> None:
    """Withheld, not shown-and-forbidden.

    A reviewer that reads a plausible, well-argued repair procedure and is then
    told not to act on it is being asked to hold a line under argument. One
    that never sees it is not.
    """
    project = tmp_path / "workspace"
    state = tmp_path / "session-state"
    global_root = tmp_path / "profile-skills"
    project.mkdir()
    skills = state / "skills" / "manager"
    skills.mkdir(parents=True)
    (skills / "gate-repair.md").write_text(
        "---\n"
        "name: gate repair\n"
        "description: Finish a stage the gate will not release\n"
        "---\n\n"
        "Call advance_stage with the objective mode already resolved.\n",
        encoding="utf-8",
    )
    (skills / "reproducer.md").write_text(
        "---\n"
        "name: reduced reproducer\n"
        "description: Shrink the failing case before changing anything\n"
        "---\n\n"
        "Cut the input in half and rerun until it stops failing.\n",
        encoding="utf-8",
    )
    backend = _SilentBackend()

    propagate_after_mission(
        project,
        backend,
        project_state_dir=state,
        shared_root=global_root,
        mission_objective="Prove the conjecture",
        mission_result="status=done",
    )

    prompt = backend.calls[0]["prompt"]
    assert "<withheld_candidate>" in prompt
    assert "Call advance_stage with the objective mode" not in prompt
    assert "gate-repair.md" in prompt, "the file is named; only its text is held back"
    assert "Cut the input in half" in prompt, "ordinary candidates are unaffected"


def test_no_project_skill_directory_skips_team_learning(
    tmp_path: Path,
) -> None:
    """The rule is stated even with no candidate to withhold.

    The mission result is in the prompt, and a result reading "unblocked the
    scope gate by completing the stage" carries the whole procedure — a
    reviewer can write the skill from that alone, having seen no candidate.
    """
    project = tmp_path / "workspace"
    global_root = tmp_path / "profile-skills"
    project.mkdir()
    backend = _SilentBackend()

    propagate_after_mission(
        project,
        backend,
        project_state_dir=None,
        shared_root=global_root,
        mission_objective="Prove the conjecture",
        mission_result="status=done",
    )

    assert backend.calls == []


def test_a_workspace_skill_is_offered_with_the_code_beside_it(tmp_path: Path) -> None:
    """The Engineer writes inside its workspace; its Agent Skill is a project candidate."""
    project = tmp_path / "workspace"
    state = tmp_path / "session-state"
    global_root = tmp_path / "profile-skills"
    skill = project / ".agents" / "skills" / "dependency-closed-check"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: dependency-closed-check\n"
        "description: Validate a module whose dependencies close\n---\n\n"
        "Run `python check.py <module>`.\n",
        encoding="utf-8",
    )
    (skill / "check.py").write_text("print('ok')\n", encoding="utf-8")
    old = state / "skills" / "engineer" / "old-procedure.md"
    old.parent.mkdir(parents=True)
    old.write_text("---\nname: old procedure\ndescription: kept by an older release\n---\n", encoding="utf-8")
    backend = _SilentBackend()

    propagate_after_mission(project, backend, project_state_dir=state, shared_root=global_root)

    (call,) = backend.calls
    assert "- engineer/dependency-closed-check/SKILL.md" in call["prompt"]
    assert "old-procedure" not in call["prompt"], "Engineer procedures live in the workspace"
    assert f"code beside it in `{skill.resolve()}`: check.py" in call["prompt"]
    assert "promote it whole" in call["prompt"]
    assert call["options"].add_dirs == [str(skill.resolve())]


def test_a_skill_naming_another_vertical_is_reviewed_into_that_vertical(tmp_path: Path) -> None:
    project = tmp_path / "workspace"
    state = tmp_path / "session-state"
    global_root = tmp_path / "profile-skills"
    for name, vertical in (("serving-check", "research"), ("build-check", "no-such")):
        (project / ".agents" / "skills" / name).mkdir(parents=True)
        (project / ".agents" / "skills" / name / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: A reusable check\nvertical: {vertical}\n---\n",
            encoding="utf-8",
        )
    backend = _SilentBackend()

    propagate_after_mission(project, backend, project_state_dir=state, shared_root=global_root)

    by_root = {call["options"].working_dir: call["prompt"] for call in backend.calls}
    research = str((global_root / "_shared_verticals" / "research").resolve())
    software = str((global_root / "_shared_verticals" / "software").resolve())
    assert sorted(by_root) == sorted([research, software])
    assert "serving-check" in by_root[research] and "build-check" not in by_root[research]
    assert "build-check" in by_root[software], "an unknown vertical keeps the project's own"


def test_unseen_candidate_batch_remains_pending_for_next_success(
    tmp_path: Path,
) -> None:
    project = tmp_path / "workspace"
    state = tmp_path / "session-state"
    global_root = tmp_path / "profile-skills"
    project.mkdir()
    skill_root = project / ".agents" / "skills"
    for index in range(9):
        (skill_root / f"candidate-{index}").mkdir(parents=True)
        (skill_root / f"candidate-{index}" / "SKILL.md").write_text(
            "---\n"
            f"name: candidate {index}\n"
            f"description: Reusable procedure {index}\n"
            "---\n",
            encoding="utf-8",
        )
    backend = _SilentBackend()

    propagate_after_mission(
        project,
        backend,
        project_state_dir=state,
        shared_root=global_root,
    )
    propagate_after_mission(
        project,
        backend,
        project_state_dir=state,
        shared_root=global_root,
    )
    propagate_after_mission(
        project,
        backend,
        project_state_dir=state,
        shared_root=global_root,
    )

    assert len(backend.calls) == 2
    assert "candidate-8/SKILL.md" not in backend.calls[0]["prompt"]
    assert "candidate-8/SKILL.md" in backend.calls[1]["prompt"]


def test_missing_scope_keeps_candidates_local_without_a_model_call(tmp_path, monkeypatch):
    state = tmp_path / 'project'
    candidate = state / 'skills/manager/date-reading.md'
    candidate.parent.mkdir(parents=True)
    candidate.write_text('A project-specific birthdate procedure.')
    shared = tmp_path / 'shared'
    monkeypatch.setattr('argus.manager.skill_tidy.resolve_skill_scope', lambda _: '')
    backend = _PromotingBackend(shared)
    events = []
    counts = propagate_after_mission(state, backend, project_state_dir=state,
                                     shared_root=shared, on_event=events.append)
    assert backend.calls == []
    assert counts['to_shared'] == counts['to_vertical_shared'] == 0
    assert counts['stayed'] == 1
    assert candidate.exists() and not shared.exists()
    assert 'no resolved vertical' in events[-1]['reason']
