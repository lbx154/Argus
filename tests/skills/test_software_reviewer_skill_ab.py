from __future__ import annotations

import json

from argus_skill.core.models import RunnerResult
from argus_skill.loop import SkillLoopConfig
from argus_skill.reviewer import Reviewer, ReviewerConfig
from argus_skill.skills.builtins import seed_vertical_skills
from argus_skill.skills.store import SkillStore
from argus_skill.skills.vertical_select import persist_vertical


class _PromptDrivenABBackend:
    """Deterministic wiring probe; model-quality A/B remains an external eval."""

    def __init__(self) -> None:
        self.reviewer_prompts: list[str] = []

    def run_exec(self, *, prompt: str, run_label: str, **_kwargs) -> RunnerResult:
        if run_label == "matcher":
            return RunnerResult(
                exit_code=0,
                agent_messages=[json.dumps({
                    "matched": [{
                        "name": "Software Change Review",
                        "fit": "high",
                        "why": "software patch review",
                    }],
                })],
            )
        assert run_label == "reviewer"
        self.reviewer_prompts.append(prompt)
        treatment = "# Software Change Review" in prompt
        status = "continue" if treatment else "done"
        next_action = (
            "Preserve the two-argument public signature and rerun its existing caller."
            if treatment
            else ""
        )
        return RunnerResult(
            exit_code=0,
            agent_messages=[
                "\n".join((
                    f"STATUS={status}",
                    "REASON=The treatment audits the unchanged caller contract."
                    if treatment
                    else "REASON=The implementation summary appears complete.",
                    f"NEXT_ACTION={next_action}",
                    "OPERATOR_QUESTION=none",
                    "FORWARD_PROGRESS=true",
                    "PLAN_SIGNAL=continue",
                ))
            ],
        )


def _evaluate(
    reviewer: Reviewer,
    project,
    *,
    skill_matching_enabled: bool,
) -> object:
    return reviewer.evaluate(
        objective="Review a software patch that adds a required third argument.",
        round_index=1,
        session_id=None,
        main_summary=(
            "Changed _run_module(command, jobid) to require job_path_arg; "
            "existing callers were not discussed."
        ),
        main_error=None,
        config=ReviewerConfig(
            working_dir=str(project),
            skill_matching_enabled=skill_matching_enabled,
        ),
    )


def test_reviewer_skill_ab_knob_controls_skill_loop_config(monkeypatch) -> None:
    monkeypatch.setenv("ARGUS_SKILL_REVIEWER_SKILL_MATCHING", "0")
    assert SkillLoopConfig().reviewer_skill_matching_enabled is False
    monkeypatch.setenv("ARGUS_SKILL_REVIEWER_SKILL_MATCHING", "1")
    assert SkillLoopConfig().reviewer_skill_matching_enabled is True


def test_software_reviewer_skill_ab_reaches_the_reviewer(tmp_path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    persist_vertical(project, "software")

    control_skill_dir = tmp_path / "control-skills"
    seed_vertical_skills(control_skill_dir, "software")
    control_backend = _PromptDrivenABBackend()
    control_store = SkillStore(
        control_skill_dir,
        runner=control_backend,
        matcher_model="test-model",
    )
    control = _evaluate(
        Reviewer(
            control_backend,
            skill_store=control_store,
            memory_maintenance_enabled=False,
        ),
        project,
        skill_matching_enabled=False,
    )

    treatment_skill_dir = tmp_path / "treatment-skills"
    seed_vertical_skills(treatment_skill_dir, "software")
    treatment_backend = _PromptDrivenABBackend()
    treatment_store = SkillStore(
        treatment_skill_dir,
        runner=treatment_backend,
        matcher_model="test-model",
    )
    treatment = _evaluate(
        Reviewer(
            treatment_backend,
            skill_store=treatment_store,
            memory_maintenance_enabled=False,
        ),
        project,
        skill_matching_enabled=True,
    )

    assert control.status == "done"
    assert treatment.status == "continue"
    assert "# Software Change Review" not in control_backend.reviewer_prompts[0]
    assert "# Software Change Review" in treatment_backend.reviewer_prompts[0]
    assert "exact positional/keyword arguments" in " ".join(
        treatment_backend.reviewer_prompts[0].split()
    )
    assert len(treatment_backend.reviewer_prompts[0]) - len(
        control_backend.reviewer_prompts[0]
    ) < 2_500
