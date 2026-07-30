from __future__ import annotations

import json

from argus_skill.apps._runtime import _workflow_mode_for_project_root
from argus_skill.manager import Manager
from argus_skill.manager.domain_author import build_fast_vertical_decision_prompt
from argus_skill.reviewer import Reviewer, ReviewerConfig
from argus_skill.skills.vertical_select import (
    VERTICAL_PURPOSES,
    VERTICALS,
    persist_vertical,
)
from argus_skill.verticals._base import (
    load_vertical,
    vertical_workflow_mode,
)


def test_software_vertical_is_separate_from_direct_workflow(tmp_path) -> None:
    assert "direct" not in VERTICALS
    assert "software" in VERTICALS
    assert "software engineering" in VERTICAL_PURPOSES["software"]
    module = load_vertical("software", project_root=tmp_path)
    assert module.STAGE_ORDER == ["delivery"]
    assert module.completion_gate == "none"
    assert vertical_workflow_mode(module) == "staged"


def test_runtime_resolves_direct_workflow(tmp_path) -> None:
    persist_vertical(tmp_path, "software", workflow_mode="direct")

    assert _workflow_mode_for_project_root(tmp_path) == "direct"
    assert Manager(project_root=tmp_path).plan_stages("software") == ["delivery"]


def test_manager_can_commit_software_with_direct_workflow(tmp_path) -> None:
    class _Result:
        last_agent_message = json.dumps(
            {
                "choice": "existing",
                "vertical": "software",
                "workflow_mode": "direct",
                "confidence": 0.95,
                "execution_task": "创作一篇《秋江赋》，语言典雅但可读。",
            }
        )
        agent_messages = [last_agent_message]
        thread_id = "manager-direct"

    class _Runner:
        def run_exec(self, **kwargs):
            return _Result()

    division = Manager(project_root=tmp_path, runner=_Runner()).divide(
        "创作一篇《秋江赋》，语言典雅但可读。"
    )

    assert division.vertical == "software"
    assert division.workflow_mode == "direct"
    assert division.stages == ["delivery"]
    assert division.execution_task.startswith(
        "创作一篇《秋江赋》，语言典雅但可读。"
    )


def test_manager_prompt_separates_capability_from_execution_mode() -> None:
    prompt = build_fast_vertical_decision_prompt(
        "创作一篇《秋江赋》，语言典雅但可读，给我最终成品。",
        verticals_with_purpose=VERTICAL_PURPOSES,
    )

    assert "`vertical` is the workflow/capability" in prompt
    assert "workflow_mode=direct" in prompt
    assert "expand the task" in prompt
    assert "execution_task" not in prompt


def test_manager_prompt_routes_short_repair_to_software_direct() -> None:
    prompt = build_fast_vertical_decision_prompt(
        "Fix the gRPC middleware bug in this repository; the existing tests define success.",
        verticals_with_purpose=VERTICAL_PURPOSES,
    )

    assert "software" in prompt
    assert "workflow_mode=direct" in prompt


def test_direct_reviewer_still_calls_role_skill_matcher(tmp_path) -> None:
    from types import SimpleNamespace

    persist_vertical(tmp_path, "software", workflow_mode="direct")

    class _Result:
        agent_messages: list[str] = []
        exit_code = 1
        fatal_error = "test stop"
        input_tokens = 0
        cached_input_tokens = 0
        output_tokens = 0

    class _Runner:
        def run_exec(self, **kwargs):
            return _Result()

    reviewer = Reviewer(runner=_Runner(), skill_store=object())
    calls: list[str] = []
    reviewer.mission.match = lambda objective, **kwargs: (
        calls.append(objective) or SimpleNamespace(block="")
    )

    reviewer.evaluate(
        objective="创建一个单文件番茄钟",
        round_index=1,
        session_id=None,
        main_summary="index.html created",
        main_error=None,
        config=ReviewerConfig(working_dir=str(tmp_path)),
    )
    assert calls == ["创建一个单文件番茄钟"]
