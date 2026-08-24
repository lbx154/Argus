from __future__ import annotations

import json

from argus_skill.apps._runtime import _workflow_mode_for_project_root
from argus_skill.manager import Manager
from argus_skill.manager.domain_author import build_vertical_decision_prompt
from argus_skill.reviewer import Reviewer, ReviewerConfig
from argus_skill.skills.vertical_select import (
    VERTICAL_PURPOSES,
    VERTICALS,
    persist_vertical,
)
from argus_skill.verticals._base import (
    load_vertical,
    vertical_role_banner,
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
        tool_activity_observed = True

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
    prompt = build_vertical_decision_prompt(
        "创作一篇《秋江赋》，语言典雅但可读，给我最终成品。",
        verticals_with_purpose=VERTICAL_PURPOSES,
    )

    assert "capability VERTICAL and independent execution WORKFLOW" in prompt
    assert "`direct` for one coherent Engineer work package" in prompt
    assert "coupled output files" in prompt
    assert "Reviewer is Host-invoked after Engineer" in prompt
    assert "no task work or Live View" in prompt
    assert "Omit `execution_task` for a standalone existing route" in prompt


def test_manager_prompt_routes_short_repair_to_software_direct() -> None:
    prompt = build_vertical_decision_prompt(
        "Fix the gRPC middleware bug in this repository; the existing tests define success.",
        verticals_with_purpose=VERTICAL_PURPOSES,
    )

    assert "software" in prompt
    assert "`direct` for one coherent Engineer work package" in prompt


def test_direct_reviewer_receives_skill_library_paths(tmp_path) -> None:
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
    calls: list[bool] = []
    reviewer.mission.libraries = lambda: (
        calls.append(True)
        or SimpleNamespace(block="## Skill libraries\n- `/semantic/library`")
    )

    reviewer.evaluate(
        objective="创建一个单文件番茄钟",
        round_index=1,
        session_id=None,
        main_summary="index.html created",
        main_error=None,
        config=ReviewerConfig(working_dir=str(tmp_path)),
    )
    assert calls == [True]


def test_direct_reviewer_uses_contract_not_stage_pipeline(tmp_path) -> None:
    persist_vertical(tmp_path, "research", workflow_mode="direct")
    reviewer = Reviewer(runner=None, skill_store=None)

    prompt = reviewer._build_prompt(
        objective="Compare the supplied sources and write the two named artifacts.",
        original_objective="Compare the supplied sources and write the two named artifacts.",
        operator_messages=[],
        planner_review_instruction="Verify both artifacts against the supplied sources.",
        round_index=1,
        session_id=None,
        main_summary="Both artifacts exist and the focused check passed.",
        main_error=None,
        working_dir=str(tmp_path),
        scope="bounded",
    )

    assert reviewer.last_prompt_block_stats["stage_checklist"]["chars"] == 0
    assert "done` closes a bounded direct task" in prompt
    assert "## Upstream defects" not in prompt


def test_direct_engineer_and_reviewer_keep_selected_vertical_banners(
    tmp_path,
) -> None:
    from argus_skill.roles.prompts.engineer import build_mission_prompt

    persist_vertical(tmp_path, "kernel_engineering", workflow_mode="direct")
    vertical = load_vertical("kernel_engineering", project_root=tmp_path)
    engineer_banner = vertical_role_banner(vertical, "engineer")
    engineer_prompt = build_mission_prompt(
        task="Profile one decode and patch the measured hot path.",
        skill_text="",
        next_action=None,
        role_banner=engineer_banner,
        compact_team=True,
    )
    reviewer = Reviewer(runner=None, skill_store=None)
    reviewer_prompt = reviewer._build_prompt(
        objective="Profile one decode and patch the measured hot path.",
        original_objective="Profile one decode and patch the measured hot path.",
        operator_messages=[],
        planner_review_instruction="Check correctness and paired throughput.",
        round_index=1,
        session_id=None,
        main_summary="The paired benchmark completed.",
        main_error=None,
        working_dir=str(tmp_path),
        scope="bounded",
    )

    assert "## Active vertical role" in engineer_prompt
    assert "Treat unattended benchmark and profiler runs as asynchronous" in (
        engineer_prompt
    )
    assert "never fail work merely because" in reviewer_prompt
