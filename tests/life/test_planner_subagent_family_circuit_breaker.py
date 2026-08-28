"""Integration tests: subagent family failure streak → L4 planner circuit
breaker (`life/supervisor/_core.py::_plan_next_work`).

Regression coverage for the 2-day SWE-bench full-canary retry storm: the
planner rewords its own task titles/objectives every cycle, so the existing
exact-text duplicate/recent-failure dedup never caught "the same experiment
keeps failing" — and the missions themselves were graded successes (the
engineer really did resubmit + monitor + document real work), so the
journal-level no_progress dedup never fired either. These tests verify the
NEW mechanism: reading ``.argus_subagents/*.json`` directly and skipping a
new task that targets a family with an unresolved failure streak, plus
surfacing that fact in the planner's own prompt context.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace

from argus_skill.core.models import RunnerResult
from argus_skill.life.memory import BacklogItem, LifeMemory
from argus_skill.life.supervisor._config import LifeSupervisorConfig
from argus_skill.life.supervisor._constants import (
    PLAN_ERROR,
    PLAN_RETRY,
    PLAN_TERMINAL_IDLE,
)
from argus_skill.life.supervisor._core import LifeSupervisor


def _with_mission_quality(text: str) -> str:
    lines: list[str] = []
    for line in text.splitlines():
        lines.append(line)
        if line.strip().startswith("TASK_OBJECTIVE="):
            lines.extend([
                "TASK_HYPOTHESIS=The task tests its stated mechanism.",
                "TASK_GOAL_CONTRIBUTION=Advance the requested project outcome.",
                "TASK_EXPECTED_REGRESSIONS=Local checks may regress during repair.",
                "TASK_DECISION_RULE=Replan if evidence refutes the mechanism.",
            ])
    return "\n".join(lines)


class _CapturingPlannerRunner:
    """Fake planner backend that returns a fixed key-value verdict and records
    every prompt it was called with, so tests can assert on advisory text."""

    def __init__(self, verdict_text: str) -> None:
        self._verdict_text = _with_mission_quality(verdict_text)
        self.prompts: list[str] = []

    def run_exec(self, *, prompt, options, run_label, resume_thread_id=None):
        self.prompts.append(prompt)
        return RunnerResult(
            exit_code=0,
            agent_messages=[self._verdict_text],
            stdout_lines=[],
            stderr_lines=[],
            thread_id=None,
            fatal_error=None,
            input_tokens=0,
            cached_input_tokens=0,
            output_tokens=0,
        )


class _NullSink:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def handle_event(self, event):  # pragma: no cover - trivial
        self.events.append(event)


class _NullRunner:
    """Mission runner; never invoked in the planning-only path under test."""


def _make_supervisor(
    tmp_path: Path,
    monkeypatch,
    verdict_text: str,
    *,
    project_worktree: Path,
) -> LifeSupervisor:
    memory = LifeMemory.open(tmp_path / "life")
    config = LifeSupervisorConfig(
        continuous=True,
        continuous_objective="keep improving the project",
        paper_mission=False,
        final_certification_gate=False,
        open_ended=False,
        project_worktree=project_worktree,
    )
    sink = _NullSink()
    planner_runner = _CapturingPlannerRunner(verdict_text)
    sup = LifeSupervisor(
        memory=memory,
        runner=_NullRunner(),
        sink=sink,
        config=config,
        planner_runner=planner_runner,
    )
    sup._test_sink = sink  # type: ignore[attr-defined]
    sup._test_planner_runner = planner_runner  # type: ignore[attr-defined]

    monkeypatch.setattr(sup, "_maybe_idle_after_unchanged_open_ended_done", lambda: None)
    monkeypatch.setattr(sup, "_resolve_vertical_once", lambda: None)
    monkeypatch.setattr(sup, "_wiki_collect_task_if_due_under_blocker", lambda: None)
    monkeypatch.setattr(sup, "_render_journal_for_planner", lambda: "")
    monkeypatch.setattr(sup, "_recent_no_progress_failures", lambda: {})
    monkeypatch.setattr(sup, "_effective_final_certification_gate", lambda *_a, **_k: False)
    monkeypatch.setattr(sup, "_planner_runtime_with_idle_note", lambda: "")
    return sup


def _write_error_streak(project_root: Path, family: str, *, count: int = 5) -> None:
    registry = project_root / ".argus_subagents"
    registry.mkdir(parents=True, exist_ok=True)
    now = time.time()
    for i in range(count):
        task_id = f"{family}-2026070{i}T000000Z"
        payload = {
            "state": "error",
            "task_id": task_id,
            "started_at": now - i * 3600,
            "stop_reason": "git_apply_check_failed",
        }
        (registry / f"{task_id}.json").write_text(json.dumps(payload), encoding="utf-8")


def _flat_verdict_kv(*tasks: tuple[str, str, str]) -> str:
    """Build a flat key-value verdict from (title, objective, evidence)."""
    lines = [
        "PROJECT_DONE=false",
        "REASON=keep pushing the pipeline forward",
    ]
    for index, (title, objective, evidence) in enumerate(tasks):
        lines.extend(
            [
                f"TASK_KEY=task-{index}",
                "TASK_DEPS=",
                f"TASK_TITLE={title}",
                f"TASK_OBJECTIVE={objective}",
                "TASK_IMPACT_SCORE=5",
                "TASK_IMPACT_AREA=reliability",
                f"TASK_EVIDENCE={evidence}",
                "TASK_SCOPE=bounded",
                "TASK_STAGE_CLOSING=false",
                "TASK_REQUIRE_INDEPENDENT_REVIEW=false",
                "TASK_SKIP_STAGE_TRANSITION=false",
            ]
        )
    return "\n".join(lines)


def test_planner_structured_stage_request_advances_before_enqueue(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project_root = tmp_path / "project"
    pipeline = project_root / ".argus" / "PIPELINE_STATE.json"
    pipeline.parent.mkdir(parents=True)
    pipeline.write_text(
        json.dumps({"vertical": "research", "current_stage": "plan"}),
        encoding="utf-8",
    )
    supervisor = _make_supervisor(
        tmp_path,
        monkeypatch,
        "\n".join(
            [
                "PROJECT_DONE=false",
                "REASON=run the real benchmark next",
                "ADVANCE_TO_STAGE=benchmark",
                "TASK_KEY=benchmark",
                "TASK_TITLE=Run real benchmark",
                "TASK_OBJECTIVE=Execute the real public benchmark.",
            ]
        ),
        project_worktree=project_root,
    )
    supervisor.config.project_state_dir = project_root

    assert supervisor._plan_next_work() is True
    assert json.loads(pipeline.read_text())["current_stage"] == "benchmark"
    item = supervisor.memory.backlog.all()[0]
    assert "stage:benchmark" in item.tags


def test_missing_parent_context_ref_is_dropped_without_rejecting_batch(
    tmp_path,
    monkeypatch,
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    verdict = "\n".join(
        [
            "PROJECT_DONE=false",
            "REASON=probe then summarize",
            "TASK_KEY=parent",
            "TASK_DEPS=",
            "TASK_TITLE=Run parent probe",
            "TASK_OBJECTIVE=Read the candidate and produce primary evidence.",
            "TASK_CONTEXT_REFS=artifact::research/missing.md::required input",
            "TASK_SCOPE=bounded",
            "TASK_STAGE_CLOSING=false",
            "TASK_REQUIRE_INDEPENDENT_REVIEW=false",
            "TASK_SKIP_STAGE_TRANSITION=false",
            "TASK_KEY=child",
            "TASK_DEPS=parent",
            "TASK_TITLE=Summarize parent probe",
            "TASK_OBJECTIVE=Read the parent output and write the conclusion.",
            "TASK_SCOPE=bounded",
            "TASK_STAGE_CLOSING=false",
            "TASK_REQUIRE_INDEPENDENT_REVIEW=false",
            "TASK_SKIP_STAGE_TRANSITION=false",
        ]
    )
    supervisor = _make_supervisor(
        tmp_path,
        monkeypatch,
        verdict,
        project_worktree=project_root,
    )

    assert supervisor._plan_next_work() is True
    titles = [item.title for item in supervisor.memory.backlog.all()]
    assert titles == ["Run parent probe", "Summarize parent probe"]
    parent = supervisor.memory.backlog.all()[0]
    assert getattr(parent, "context_refs", []) == []


def test_active_dedup_ignores_planner_owned_review_routing(
    tmp_path,
    monkeypatch,
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    verdict = "\n".join(
        [
            "PROJECT_DONE=false",
            "REASON=Add a separate review-only task.",
            "TASK_KEY=review-only",
            "TASK_DEPS=",
            "TASK_TITLE=Review candidate",
            "TASK_OBJECTIVE=Assess the bounded candidate.",
            "TASK_SCOPE=bounded",
            "TASK_STAGE_CLOSING=false",
            "TASK_REQUIRE_INDEPENDENT_REVIEW=true",
            "TASK_SKIP_STAGE_TRANSITION=true",
        ]
    )
    supervisor = _make_supervisor(
        tmp_path,
        monkeypatch,
        verdict,
        project_worktree=project_root,
    )
    supervisor.memory.backlog.add(
        BacklogItem.new(
            title="Review candidate",
            objective="Assess the bounded candidate.",
            tags=["planner", "scope:bounded", "stage_closing", "review:required"],
        )
    )

    assert supervisor._plan_next_work() is True
    items = supervisor.memory.backlog.all()

    assert len(items) == 2
    assert any("stage_closing" in item.tags for item in items)
    assert not any("stage_transition:skip" in item.tags for item in items)


def test_planner_stage_closing_flag_does_not_force_review_or_dedup(
    tmp_path,
    monkeypatch,
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    verdict = "\n".join(
        [
            "PROJECT_DONE=false",
            "REASON=Close the current stage.",
            "TASK_KEY=close-stage",
            "TASK_DEPS=",
            "TASK_TITLE=Review stage evidence",
            "TASK_OBJECTIVE=Certify whether the current stage can advance.",
            "TASK_SCOPE=bounded",
            "TASK_STAGE_CLOSING=true",
            "TASK_REQUIRE_INDEPENDENT_REVIEW=false",
            "TASK_SKIP_STAGE_TRANSITION=false",
        ]
    )
    supervisor = _make_supervisor(
        tmp_path,
        monkeypatch,
        verdict,
        project_worktree=project_root,
    )
    supervisor.memory.backlog.add(
        BacklogItem.new(
            title="Review stage evidence",
            objective="Certify whether the current stage can advance.",
            tags=["planner", "scope:bounded", "stage_closing", "review:required"],
        )
    )

    assert supervisor._plan_next_work() is True
    items = supervisor.memory.backlog.all()
    assert len(items) == 2
    newest = items[-1]
    assert "stage_closing" not in newest.tags
    assert "review:required" not in newest.tags


def test_revision_rejection_helper_opens_existing_circuit_breaker(
    tmp_path,
    monkeypatch,
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    supervisor = _make_supervisor(
        tmp_path,
        monkeypatch,
        "PROJECT_DONE=true\nREASON=unused",
        project_worktree=project_root,
    )
    item = BacklogItem.new(
        title="Revise failed node",
        objective="Replace the invalid plan node.",
        plan_id="plan-old",
        plan_version=1,
    )
    item.replan_rejections = 2
    supervisor.memory.backlog.add(item)
    state = SimpleNamespace(
        revision_request={"item_id": item.id},
        revision_active_items=[item],
        expected_plan_id="plan-old",
        expected_plan_version=1,
    )

    result = supervisor._pc_record_revision_rejection(
        state,
        reason="replacement DAG has unresolved dependencies",
        nonterminal_result=PLAN_ERROR,
    )
    updated = next(
        stored
        for stored in supervisor.memory.backlog.all()
        if stored.id == item.id
    )

    assert result == PLAN_TERMINAL_IDLE
    assert updated.replan_rejections == 3
    assert updated.status == "failed"


def test_dedup_uses_canonical_scope_and_acceptance_metadata(
    tmp_path,
    monkeypatch,
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    verdict = "\n".join(
        [
            "PROJECT_DONE=false",
            "REASON=Repeat an already active task.",
            "TASK_KEY=duplicate",
            "TASK_DEPS=",
            "TASK_TITLE=Validate candidate",
            "TASK_OBJECTIVE=Run the deterministic validator.",
            "TASK_EVIDENCE=validator exits zero",
            "TASK_ACCEPTANCE_CHECK=validator exits zero",
            "TASK_SCOPE=final_submission",
            "TASK_STAGE_CLOSING=false",
            "TASK_REQUIRE_INDEPENDENT_REVIEW=true",
            "TASK_SKIP_STAGE_TRANSITION=false",
        ]
    )
    supervisor = _make_supervisor(
        tmp_path,
        monkeypatch,
        verdict,
        project_worktree=project_root,
    )
    supervisor.memory.backlog.add(
        BacklogItem.new(
            title="Validate candidate",
            objective="Run the deterministic validator.",
            acceptance_check="validator exits zero",
            tags=["planner", "scope:bounded", "bounded_dag_node"],
        )
    )

    supervisor._plan_next_work()

    assert len(supervisor.memory.backlog.all()) == 1


def test_duplicate_prerequisite_key_maps_to_existing_backlog_item(
    tmp_path,
    monkeypatch,
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    verdict = "\n".join(
        [
            "PROJECT_DONE=false",
            "REASON=Reuse the active prerequisite and enqueue its child.",
            "TASK_KEY=parent",
            "TASK_DEPS=",
            "TASK_TITLE=Prepare inputs",
            "TASK_OBJECTIVE=Prepare the validated input bundle.",
            "TASK_EVIDENCE=input bundle exists",
            "TASK_ACCEPTANCE_CHECK=input bundle exists",
            "TASK_SCOPE=bounded",
            "TASK_STAGE_CLOSING=false",
            "TASK_REQUIRE_INDEPENDENT_REVIEW=true",
            "TASK_SKIP_STAGE_TRANSITION=false",
            "TASK_KEY=child",
            "TASK_DEPS=parent",
            "TASK_TITLE=Run child analysis",
            "TASK_OBJECTIVE=Analyze the validated input bundle.",
            "TASK_EVIDENCE=analysis report exists",
            "TASK_ACCEPTANCE_CHECK=analysis report exists",
            "TASK_SCOPE=bounded",
            "TASK_STAGE_CLOSING=false",
            "TASK_REQUIRE_INDEPENDENT_REVIEW=true",
            "TASK_SKIP_STAGE_TRANSITION=false",
        ]
    )
    supervisor = _make_supervisor(
        tmp_path,
        monkeypatch,
        verdict,
        project_worktree=project_root,
    )
    parent = supervisor.memory.backlog.add(
        BacklogItem.new(
            title="Prepare inputs",
            objective="Prepare the validated input bundle.",
            acceptance_check="input bundle exists",
            tags=["planner", "scope:bounded", "bounded_dag_node"],
        )
    )

    result = supervisor._plan_next_work()

    items = supervisor.memory.backlog.all()
    child = next(item for item in items if item.title == "Run child analysis")
    assert result is True
    assert child.deps == [parent.id]


def test_recent_no_progress_failure_still_quarantines_expanded_task_signature(
    tmp_path,
    monkeypatch,
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    verdict = _flat_verdict_kv(
        (
            "Retry failed probe",
            "Run the same probe again.",
            "the prior failure remains unresolved",
        )
    )
    supervisor = _make_supervisor(
        tmp_path,
        monkeypatch,
        verdict,
        project_worktree=project_root,
    )
    failed = SimpleNamespace(
        title="Retry failed probe",
        extra={
            "item_id": "failed-item",
            "objective": "Run the same probe again.",
            "terminal_status": "no_progress",
        },
    )
    monkeypatch.setattr(
        supervisor,
        "_recent_no_progress_failures",
        lambda: {("retry failed probe", "run the same probe again."): failed},
    )

    assert supervisor._plan_next_work() == PLAN_RETRY
    assert supervisor.memory.backlog.all() == []
    skipped = [
        event
        for event in supervisor._test_sink.events  # type: ignore[attr-defined]
        if event.get("skip_category") == "recent_no_progress_failure"
    ]
    assert len(skipped) == 1


def test_task_targeting_a_stuck_family_is_skipped(tmp_path, monkeypatch) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    _write_error_streak(project_root, "swebench-verified-full-canary")

    verdict_json = _flat_verdict_kv(
        (
            "Synchronize SWE canary handoff gate",
            "Resubmit the swebench-verified-full-canary run and refresh the handoff packet",
            "SWE-bench is still live at 150/500 with zero official rows",
        )
    )
    sup = _make_supervisor(tmp_path, monkeypatch, verdict_json, project_worktree=project_root)

    result = sup._plan_next_work()
    assert result == PLAN_RETRY
    assert sup._suggested_sleep_s > 0

    assert sup.memory.backlog.all() == []
    events = sup._test_sink.events  # type: ignore[attr-defined]
    skipped = [e for e in events if e["type"] == "life.planner.task_skipped"]
    assert len(skipped) == 1
    assert skipped[0]["skip_category"] == "recent_subagent_family_failure"
    assert skipped[0]["matched_family"] == "swebench-verified-full-canary"
    assert skipped[0]["matched_streak"] == 5

    verdict_event = next(e for e in events if e["type"] == "life.planner.verdict")
    assert verdict_event["skipped_subagent_family_failure_tasks"] == 1
    assert verdict_event["enqueued_tasks"] == 0
    assert verdict_event["stuck_subagent_families"] == {"swebench-verified-full-canary": 5}


def test_task_unrelated_to_any_stuck_family_still_enqueues(tmp_path, monkeypatch) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    _write_error_streak(project_root, "swebench-verified-full-canary")

    verdict_json = _flat_verdict_kv(
        (
            "Write the related-work section",
            "Draft paper/main.tex related work citing the grounded literature list",
            "literature review is complete; drafting is the next open task",
        )
    )
    sup = _make_supervisor(tmp_path, monkeypatch, verdict_json, project_worktree=project_root)

    result = sup._plan_next_work()
    assert result is True

    items = sup.memory.backlog.all()
    assert [it.title for it in items] == ["Write the related-work section"]
    events = sup._test_sink.events  # type: ignore[attr-defined]
    assert not [e for e in events if e["type"] == "life.planner.task_skipped"]


def test_no_stuck_families_means_no_circuit_breaker_activity(tmp_path, monkeypatch) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()  # no .argus_subagents at all

    verdict_json = _flat_verdict_kv(
        (
            "Run the swebench canary again",
            "Resubmit swebench-verified-full-canary",
            "first attempt, nothing has failed yet",
        )
    )
    sup = _make_supervisor(tmp_path, monkeypatch, verdict_json, project_worktree=project_root)

    assert sup._plan_next_work() is True
    items = sup.memory.backlog.all()
    assert [it.title for it in items] == ["Run the swebench canary again"]


def test_streak_below_limit_does_not_trip_the_breaker(tmp_path, monkeypatch) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    _write_error_streak(
        project_root, "swebench-verified-full-canary", count=2
    )  # < default limit of 3

    verdict_json = _flat_verdict_kv(
        (
            "Synchronize SWE canary handoff gate",
            "Resubmit the swebench-verified-full-canary run",
            "SWE-bench is still live at 150/500",
        )
    )
    sup = _make_supervisor(tmp_path, monkeypatch, verdict_json, project_worktree=project_root)

    assert sup._plan_next_work() is True
    items = sup.memory.backlog.all()
    assert [it.title for it in items] == ["Synchronize SWE canary handoff gate"]


def test_streak_limit_zero_disables_the_breaker(tmp_path, monkeypatch) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    _write_error_streak(project_root, "swebench-verified-full-canary", count=10)

    verdict_json = _flat_verdict_kv(
        (
            "Synchronize SWE canary handoff gate",
            "Resubmit the swebench-verified-full-canary run",
            "SWE-bench is still live at 150/500",
        )
    )
    sup = _make_supervisor(tmp_path, monkeypatch, verdict_json, project_worktree=project_root)
    sup.config.subagent_family_failure_streak_limit = 0

    assert sup._plan_next_work() is True
    items = sup.memory.backlog.all()
    assert [it.title for it in items] == ["Synchronize SWE canary handoff gate"]


def test_advisory_block_reaches_the_planner_prompt(tmp_path, monkeypatch) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    _write_error_streak(project_root, "swebench-verified-full-canary")

    verdict_json = _flat_verdict_kv(
        (
            "Write the related-work section",
            "Draft paper/main.tex related work",
            "unrelated to the stuck family",
        )
    )
    sup = _make_supervisor(tmp_path, monkeypatch, verdict_json, project_worktree=project_root)

    assert sup._plan_next_work() is True
    planner_runner = sup._test_planner_runner  # type: ignore[attr-defined]
    assert len(planner_runner.prompts) == 1
    prompt = planner_runner.prompts[0]
    assert "STUCK EXPERIMENT FAMILIES" in prompt
    assert "swebench-verified-full-canary" in prompt
    assert "5 consecutive error attempt(s)" in prompt


def test_underscore_and_hyphen_family_slugs_both_match(tmp_path, monkeypatch) -> None:
    """benchmark_family identifiers mix underscore/hyphen conventions; the
    match must not be defeated by that alone."""
    project_root = tmp_path / "project"
    project_root.mkdir()
    _write_error_streak(project_root, "swebench-verified-full-canary")

    verdict_json = _flat_verdict_kv(
        (
            "Retry swebench_verified full canary",
            "Resubmit the swebench_verified_full_canary experiment",
            "benchmark_family: swebench_verified",
        )
    )
    sup = _make_supervisor(tmp_path, monkeypatch, verdict_json, project_worktree=project_root)

    assert sup._plan_next_work() == PLAN_RETRY
    assert sup.memory.backlog.all() == []
    events = sup._test_sink.events  # type: ignore[attr-defined]
    skipped = [e for e in events if e["type"] == "life.planner.task_skipped"]
    assert len(skipped) == 1
    assert skipped[0]["skip_category"] == "recent_subagent_family_failure"
