from __future__ import annotations

import json

from argus_skill.apps._runtime import _workflow_mode_for_project_root
from argus_skill.manager import Manager
from argus_skill.manager.domain_author import build_vertical_decision_prompt
from argus_skill.skills.vertical_select import (
    VERTICAL_PURPOSES,
    VERTICALS,
    persist_vertical,
)
from argus_skill.verticals._base import (
    load_vertical,
    vertical_workflow_mode,
)


def test_direct_vertical_is_registered_and_lean(tmp_path) -> None:
    assert "direct" in VERTICALS
    assert "one-off" in VERTICAL_PURPOSES["direct"]
    module = load_vertical("direct", project_root=tmp_path)
    assert module.STAGE_ORDER == ["delivery"]
    assert module.completion_gate == "none"
    assert vertical_workflow_mode(module) == "direct"


def test_runtime_resolves_direct_workflow(tmp_path) -> None:
    persist_vertical(tmp_path, "direct")

    assert _workflow_mode_for_project_root(tmp_path) == "direct"
    assert Manager(project_root=tmp_path).plan_stages("direct") == ["delivery"]


def test_manager_can_commit_direct_vertical(tmp_path) -> None:
    class _Result:
        last_agent_message = json.dumps({
            "choice": "existing",
            "vertical": "direct",
            "execution_task": "创作一篇《秋江赋》，语言典雅但可读。",
        })
        agent_messages = [last_agent_message]
        thread_id = "manager-direct"

    class _Runner:
        def run_exec(self, **kwargs):
            return _Result()

    division = Manager(project_root=tmp_path, runner=_Runner()).divide(
        "创作一篇《秋江赋》，语言典雅但可读。"
    )

    assert division.vertical == "direct"
    assert division.stages == ["delivery"]
    assert division.execution_task == "创作一篇《秋江赋》，语言典雅但可读。"


def test_manager_prompt_prefers_direct_without_inventing_requirements() -> None:
    prompt = build_vertical_decision_prompt(
        "创作一篇《秋江赋》，语言典雅但可读，给我最终成品。",
        verticals_with_purpose=VERTICAL_PURPOSES,
    )

    assert "Use `direct` for a bounded one-off deliverable" in prompt
    assert "do NOT invent mandatory word counts" in prompt
    assert "acceptance gates that the operator did not request" in prompt
