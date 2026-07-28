from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from argus_skill.core.models import RunnerResult
from argus_skill.reviewer import Reviewer, _load_wiki_curator_skill_if_present
from argus_skill.reviewer._core import ReviewerConfig
from argus_skill.skills.store import SkillStore
from argus_skill.wiki.bootstrap import init_wiki


def test_returns_skill_text_when_wiki_present(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.chdir(tmp_path)
    init_wiki("demo", base=tmp_path)
    text = _load_wiki_curator_skill_if_present()
    assert text is not None
    assert "knowledge curator" in text.lower()


def test_returns_none_when_no_wiki(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.chdir(tmp_path)
    text = _load_wiki_curator_skill_if_present()
    assert text is None


def test_explicit_workdir_wins_over_process_cwd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    process_cwd = tmp_path / "process"
    project = tmp_path / "project"
    process_cwd.mkdir()
    project.mkdir()
    monkeypatch.chdir(process_cwd)
    init_wiki("demo", base=project)

    text = _load_wiki_curator_skill_if_present(project)

    assert text is not None


def test_curator_not_loaded_for_uninitialized_wiki(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".autors" / "demo" / "wiki").mkdir(parents=True)
    assert _load_wiki_curator_skill_if_present() is None


def test_reviewer_prompt_includes_fixed_wiki_curator_when_wiki_present(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    from argus_skill.skills.vertical_select import persist_vertical

    monkeypatch.chdir(tmp_path)
    init_wiki("demo", base=tmp_path)
    # Real missions persist the Manager-decided vertical before the reviewer
    # builds a prompt; resolve_vertical is fail-hard, so seed research.
    persist_vertical(tmp_path, "research")
    reviewer = Reviewer(runner=object())

    prompt = reviewer._build_prompt(
        objective="diagnose a training failure",
        operator_messages=[],
        planner_review_instruction="",
        round_index=0,
        session_id=None,
        main_summary="summary",
        main_error=None,
    )

    assert "Wiki curator (fixed when a wiki exists" in prompt
    assert "knowledge curator" in prompt.lower()
    assert str(tmp_path / ".autors" / "demo" / "wiki") in prompt
    assert "directly edit" in prompt
    assert "Do not emit proposed wiki operations in JSON" in prompt


def test_reviewer_prompt_uses_configured_workdir_for_wiki(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    from argus_skill.skills.vertical_select import persist_vertical

    process_cwd = tmp_path / "process"
    project = tmp_path / "project"
    process_cwd.mkdir()
    project.mkdir()
    monkeypatch.chdir(process_cwd)
    init_wiki("demo", base=project)
    persist_vertical(project, "research")
    reviewer = Reviewer(runner=object())

    prompt = reviewer._build_prompt(
        objective="diagnose a training failure",
        operator_messages=[],
        planner_review_instruction="",
        round_index=0,
        session_id=None,
        main_summary="summary",
        main_error=None,
        working_dir=project,
    )

    assert "Wiki curator (fixed when a wiki exists" in prompt
    assert str(project / ".autors" / "demo" / "wiki") in prompt


def test_reviewer_prompt_injects_project_skill_path_for_direct_edits(
    tmp_path: Path,
) -> None:
    from argus_skill.skills.vertical_select import persist_vertical

    project = tmp_path / "project"
    skill_dir = tmp_path / "state" / "skills"
    project.mkdir()
    skill_dir.mkdir(parents=True)
    persist_vertical(project, "research")
    store = SimpleNamespace(
        project=SimpleNamespace(skills_dir=skill_dir),
        global_=SimpleNamespace(skills_dir=tmp_path / "global-skills"),
        find_relevant=lambda *args, **kwargs: ([], 0),
    )
    reviewer = Reviewer(runner=object(), skill_store=store)

    prompt = reviewer._build_prompt(
        objective="review the trajectory",
        operator_messages=[],
        planner_review_instruction="",
        round_index=1,
        session_id="m1",
        main_summary="summary",
        main_error=None,
        working_dir=project,
    )

    assert f"Project skill directory (project layer only): {skill_dir.resolve()}" in prompt
    assert str((tmp_path / "global-skills").resolve()) not in prompt
    assert "/home/argustest" not in prompt
    assert "edit the project memory directly BEFORE your final verdict" in prompt


def test_reviewer_directly_edits_skill_and_wiki_before_final_verdict(
    tmp_path: Path,
) -> None:
    from argus_skill.skills.vertical_select import persist_vertical

    project = tmp_path / "project"
    skill_dir = tmp_path / "state" / "skills"
    project.mkdir()
    skill_dir.mkdir(parents=True)
    wiki_root = init_wiki("demo", base=project)
    persist_vertical(project, "direct")

    class DirectEditRunner:
        def run_exec(self, **kwargs):
            prompt = kwargs["prompt"]
            assert str(skill_dir.resolve()) in prompt
            assert str(wiki_root.resolve()) in prompt
            (skill_dir / "trajectory-lesson.md").write_text(
                "---\nname: Trajectory Lesson\ndescription: Reuse verified trajectory "
                "lessons.\ncategory: learning\nversion: 1\n---\n\n# Lesson\n",
                encoding="utf-8",
            )
            (wiki_root / "pages" / "patterns" / "trajectory-lesson.md").write_text(
                "---\nid: trajectory-lesson\ntype: pattern\nstatus: scratch\n---\n\n"
                "# Trajectory lesson\n",
                encoding="utf-8",
            )
            return RunnerResult(
                exit_code=0,
                agent_messages=[json.dumps({
                    "status": "continue",
                    "reason": "Reusable learning was persisted directly.",
                    "next_action": "Apply the corrected method.",
                    "operator_question": None,
                    "round_summary_markdown": "# Review\n",
                    "completion_summary_markdown": "",
                    "achievement": None,
                    "failure_cause": "method_failure",
                    "progress_class": "evidence",
                    "scope": "bounded",
                    "planner_report": {
                        "forward_progress": True,
                        "headline": "lesson persisted",
                        "blocker": "",
                        "recommended_next": "apply it",
                        "plan_signal": "continue",
                        "plan_signal_reason": "",
                        "evidence_files": [],
                    },
                    "checklist": [],
                    "checklist_feedback": None,
                    "step_back": None,
                })],
            )

    reviewer = Reviewer(DirectEditRunner(), skill_store=SkillStore(skill_dir))
    decision = reviewer.evaluate(
        objective="review the trajectory",
        round_index=1,
        session_id="m1",
        main_summary="summary",
        main_error=None,
        config=ReviewerConfig(
            model="test",
            reasoning_effort="high",
            working_dir=str(project),
        ),
    )

    assert decision.status == "continue"
    assert decision.skill_ops == []
    assert (skill_dir / "trajectory-lesson.md").is_file()
    assert (wiki_root / "pages" / "patterns" / "trajectory-lesson.md").is_file()
