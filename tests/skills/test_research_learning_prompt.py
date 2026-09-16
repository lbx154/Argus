"""Research roles are told to keep surveys and setups as Skills, not chat answers."""
from __future__ import annotations

from argus.verticals.research.prompt_policy import render_role_prompt_fragment


def _fragment(role: str, stage: str, operation: str = "evaluate") -> str:
    return render_role_prompt_fragment(
        role=role, operation=operation, stage=stage, scope="", project_root=None,
    )


def test_engineer_records_infrastructure_surveys_as_project_skills() -> None:
    for stage in ("idea", "experiment", "paper"):
        text = _fragment("engineer", stage, operation="execute")
        assert "## Durable research learning" in text
        assert "rl-infrastructure-survey.md" in text
        assert "training-infrastructure-guide.md" in text
        assert "promotes reviewed project Skills into the shared research layer" in text
    assert "## Durable research learning" not in _fragment("engineer", "review", operation="narrative_edit")


def test_manager_keeps_chat_surveys_as_skills() -> None:
    text = _fragment("manager", "experiment", operation="chat")
    assert "## Durable research learning" in text
    assert "not only as a chat answer" in text
