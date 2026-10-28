from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from argus.core.vertical_contract import VerticalLibraryContext
from argus.skills.loop_skill_library import SkillLibraryMixin
from argus.skills.loop_state import MissionContext
from argus.verticals.research.library_preparation import prepare_skill_libraries
from argus.verticals.research.stages import stage_completion_issues


def _state(root: Path, **overrides: object) -> Path:
    path = root / ".argus" / "PIPELINE_STATE.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({
            "vertical": "research",
            "current_stage": "idea",
            "research_target_level": "publishable",
            "research_direction_mode": "broad",
            "selected_idea": None,
            "current_verdict": "in_progress",
            "next_action": "select",
            **overrides,
        }),
        encoding="utf-8",
    )
    return path


def _prepare(root: Path, *, workflow_mode: str = "staged", paper_mission: bool = True):
    required: list[str] = []
    prompt_blocks: list[str] = []
    events: list[dict] = []
    prepare_skill_libraries(
        VerticalLibraryContext(
            workdir=root,
            state_root=root,
            stage="idea",
            objective="select one strong idea",
            direction="reliable agents",
            workflow_mode=workflow_mode,
            paper_mission=paper_mission,
            runner=None,
            model=None,
            emit=events.append,
            required_skill_paths=required,
            prompt_blocks=prompt_blocks,
        )
    )
    return required, prompt_blocks, events


def test_a_broad_paper_idea_is_the_agents_own_work(tmp_path: Path) -> None:
    _state(tmp_path)

    required, prompt_blocks, events = _prepare(tmp_path)

    assert required == ["research-idea-playbook.md"]
    assert prompt_blocks == []
    assert events == []
    assert not (tmp_path / ".argus" / "teams").exists()
    assert stage_completion_issues("idea", tmp_path) == ()


def test_locked_and_direct_ideas_use_only_the_playbook(tmp_path: Path) -> None:
    _state(tmp_path, research_direction_mode="locked")
    assert _prepare(tmp_path)[0] == ["research-idea-playbook.md"]
    assert _prepare(tmp_path, workflow_mode="direct")[0] == ["research-idea-playbook.md"]
    assert _prepare(tmp_path / "plain", paper_mission=False)[0] == [
        "research-idea-playbook.md"
    ]


def test_skill_library_state_includes_vertical_prompt_blocks() -> None:
    class Harness(SkillLibraryMixin):
        engineer_mission = SimpleNamespace(
            libraries=lambda **_kwargs: SimpleNamespace(block="library index")
        )
        reviewer = SimpleNamespace(
            mission=SimpleNamespace(
                libraries=lambda: SimpleNamespace(block="reviewer index")
            )
        )

        def _prepare_vertical_libraries(
            self,
            _mission: MissionContext,
        ) -> tuple[tuple[str, ...], tuple[str, ...]]:
            return (("required.md",), ("vertical block",))

    state = Harness()._prepare_skill_libraries(
        MissionContext(
            workdir=Path("/project"),
            run_id="run-1",
            task="task",
            skill_task="task",
            request_anchor="request",
            active_vertical="research",
            engineer_role_banner="",
            seed_thread_id=None,
            scope="",
        )
    )

    assert state.skill_text == "library index\n\nvertical block"
    assert state.reviewer_skill_block == "reviewer index"


def test_idea_bar_is_a_solid_paper_and_overlap_is_a_risk() -> None:
    from argus.verticals.research.prompt_policy import render_role_prompt_fragment
    from argus.verticals.research.stages import role_banner

    skills = Path(__file__).resolve().parents[2] / "argus/verticals/research/skills"
    playbook = (skills / "research-idea-playbook.md").read_text(encoding="utf-8")
    assert "aspiration, not a gate" in playbook
    assert "Related work existing is not a rejection" in playbook
    # No fixed source count or one-copy rule: depth follows the route.
    assert "5–8" not in playbook and "raw download" not in playbook
    assert "pass and list the risks" in playbook
    novelty = (skills / "engineer/novelty-check.md").read_text(encoding="utf-8")
    assert "5–8" not in novelty and "raw download" not in novelty
    assert "without repeating the search" in novelty

    idea_review = render_role_prompt_fragment(
        role="reviewer", operation="evaluate", stage="idea", scope="", project_root=None
    )
    assert "## Idea route bar" in idea_review
    assert "do not redo the literature search" in idea_review
    assert "## Idea route bar" not in render_role_prompt_fragment(
        role="reviewer", operation="evaluate", stage="experiment", scope="", project_root=None
    )
    assert "not an Oral or Best Paper" in role_banner("reviewer")
