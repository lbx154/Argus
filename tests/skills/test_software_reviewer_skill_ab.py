from __future__ import annotations

from pathlib import Path

from argus_skill.core.models import RunnerResult
from argus_skill.reviewer import Reviewer, ReviewerConfig
from argus_skill.skills.builtins import seed_vertical_skills
from argus_skill.skills.store import SkillStore
from argus_skill.skills.vertical_select import persist_vertical


class _LibraryAwareBackend:
    """Probe that models agent-native discovery without a runtime matcher."""

    def __init__(self, library_root: Path | None = None) -> None:
        self.library_root = library_root.resolve() if library_root else None
        self.reviewer_prompts: list[str] = []

    def run_exec(self, *, prompt: str, run_label: str, **_kwargs) -> RunnerResult:
        assert run_label == "reviewer"
        self.reviewer_prompts.append(prompt)
        library_available = bool(
            self.library_root and f"`{self.library_root}`" in prompt
        )
        return RunnerResult(
            exit_code=0,
            agent_messages=[
                "\n".join(
                    (
                        f"STATUS={'continue' if library_available else 'done'}",
                        "REASON=The Reviewer can inspect its software review library."
                        if library_available
                        else "REASON=No Reviewer library was supplied.",
                        "NEXT_ACTION=Trace the changed signature through unchanged callers."
                        if library_available
                        else "NEXT_ACTION=",
                        "OPERATOR_QUESTION=none",
                        "FORWARD_PROGRESS=true",
                        "PLAN_SIGNAL=continue",
                    )
                )
            ],
        )


def _evaluate(reviewer: Reviewer, project: Path) -> object:
    return reviewer.evaluate(
        objective="Review a software patch that adds a required third argument.",
        round_index=1,
        session_id=None,
        main_summary=(
            "Changed _run_module(command, jobid) to require job_path_arg; "
            "existing callers were not discussed."
        ),
        main_error=None,
        config=ReviewerConfig(working_dir=str(project)),
    )


def test_software_reviewer_skill_is_agent_native_and_discoverable(tmp_path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    persist_vertical(project, "software")

    skill_root = tmp_path / "software-skills"
    seed_vertical_skills(skill_root, "software")
    skill_path = skill_root / "reviewer" / "software-change-review.md"
    text = skill_path.read_text(encoding="utf-8")
    assert "exact positional/keyword arguments" in " ".join(text.split())
    assert [line for line in text.split("---", 2)[1].splitlines() if line] == [
        'name: "Software Change Review"',
        'description: "Independently review a software patch for real call-path'
        ' behavior, compatibility, and honest verification without access to a'
        ' reference answer."',
    ]

    control_backend = _LibraryAwareBackend()
    control = _evaluate(Reviewer(control_backend), project)

    treatment_backend = _LibraryAwareBackend(skill_root)
    treatment = _evaluate(
        Reviewer(treatment_backend, skill_store=SkillStore(skill_root)),
        project,
    )

    assert control.status == "done"
    assert treatment.status == "continue"
    assert str(skill_root.resolve()) not in control_backend.reviewer_prompts[0]
    assert f"`{skill_root.resolve()}`" in treatment_backend.reviewer_prompts[0]
    # Main's agent-native contract supplies paths, never copied Skill bodies.
    assert "# Software Change Review" not in treatment_backend.reviewer_prompts[0]
