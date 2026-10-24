from __future__ import annotations

from argus.adapters.memory_backend import CannedResponse, MemoryBackend
from argus.reviewer import Reviewer, ReviewerConfig
from argus.skills.vertical_select import (
    persist_vertical,
)
from argus.verticals._base import load_vertical_contract
from tests.hand_off import hand_off


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
        or SimpleNamespace(block="## Skill libraries\n- `/semantic/library`", library_roots=[])
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
    assert 'done` when a direct task meets its requirements and decisive check' in prompt
    assert "## Upstream defects" not in prompt


def test_reviewer_workflow_change_refreshes_the_session_rubric(tmp_path) -> None:
    backend = MemoryBackend()
    response = CannedResponse(
        action=hand_off("done", 'Complete.'),
        thread_id="reviewer-thread",
    )
    backend.queue("reviewer", response)
    backend.queue("reviewer", response)
    reviewer = Reviewer(backend)
    first = reviewer.evaluate(
        objective="Complete the task.", round_index=1, session_id=None,
        main_summary="Complete.", main_error=None,
        config=ReviewerConfig(
            working_dir=str(tmp_path), active_vertical="software", workflow_mode="staged",
        ),
    )

    second = reviewer.evaluate(
        objective="Complete the task.", round_index=2, session_id=None,
        main_summary="Complete.", main_error=None,
        config=ReviewerConfig(
            working_dir=str(tmp_path), active_vertical="software", workflow_mode="direct",
        ),
        resume_thread_id=first.thread_id,
        prior_static_fingerprint=first.static_fingerprint,
    )

    assert second.session_resumed is False
    assert backend.resume_history == [("reviewer", None), ("reviewer", None)]
    prompt = backend.history[-1][1]
    assert "## Reviewer role" in prompt
    assert "when a direct task meets its requirements and decisive check" in prompt


def test_direct_engineer_and_reviewer_keep_selected_vertical_banners(
    tmp_path,
) -> None:
    from argus.roles.prompts.engineer import build_mission_prompt

    persist_vertical(tmp_path, "argus_maintenance", workflow_mode="direct")
    contract = load_vertical_contract("argus_maintenance", project_root=tmp_path)
    engineer_banner = contract.banner("engineer")
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
    assert "ARGUS MAINTENANCE: improve Argus itself" in engineer_prompt
    assert "Review the changed causal surface" in reviewer_prompt
