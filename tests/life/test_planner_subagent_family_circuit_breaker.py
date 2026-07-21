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

from argus_skill.core.models import RunnerResult
from argus_skill.life.memory import LifeMemory
from argus_skill.life.supervisor._config import LifeSupervisorConfig
from argus_skill.life.supervisor._constants import PLAN_RETRY
from argus_skill.life.supervisor._core import LifeSupervisor


class _CapturingPlannerRunner:
    """Fake planner backend that returns a fixed JSON verdict and records
    every prompt it was called with, so tests can assert on advisory text."""

    def __init__(self, verdict_json: str) -> None:
        self._verdict_json = verdict_json
        self.prompts: list[str] = []

    def run_exec(self, *, prompt, options, run_label, resume_thread_id=None):
        self.prompts.append(prompt)
        return RunnerResult(
            exit_code=0,
            agent_messages=[self._verdict_json],
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
    tmp_path: Path, monkeypatch, verdict_json: str, *, project_worktree: Path,
) -> LifeSupervisor:
    memory = LifeMemory.open(tmp_path / "life")
    config = LifeSupervisorConfig(
        continuous=True,
        continuous_objective="keep improving the project",
        paper_mission=False,
        full_paper_gate=False,
        open_ended=False,
        project_worktree=project_worktree,
    )
    sink = _NullSink()
    planner_runner = _CapturingPlannerRunner(verdict_json)
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
    monkeypatch.setattr(sup, "_effective_full_paper_gate", lambda *_a, **_k: False)
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


def _flat_verdict_json(*tasks: tuple[str, str, str]) -> str:
    """Build a flat (non-DAG) verdict JSON from (title, objective, evidence)."""
    return json.dumps({
        "project_done": False,
        "reason": "keep pushing the pipeline forward",
        "waiting": False,
        "waiting_reason": "",
        "new_tasks": [
            {
                "title": title,
                "impact_score": 5,
                "impact_area": "reliability",
                "evidence": evidence,
                "scope": "bounded",
                "objective": objective,
            }
            for title, objective, evidence in tasks
        ],
    })


def test_task_targeting_a_stuck_family_is_skipped(tmp_path, monkeypatch) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    _write_error_streak(project_root, "swebench-verified-full-canary")

    verdict_json = _flat_verdict_json((
        "Synchronize SWE canary handoff gate",
        "Resubmit the swebench-verified-full-canary run and refresh the handoff packet",
        "SWE-bench is still live at 150/500 with zero official rows",
    ))
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

    verdict_json = _flat_verdict_json((
        "Write the related-work section",
        "Draft paper/main.tex related work citing the grounded literature list",
        "literature review is complete; drafting is the next open task",
    ))
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

    verdict_json = _flat_verdict_json((
        "Run the swebench canary again",
        "Resubmit swebench-verified-full-canary",
        "first attempt, nothing has failed yet",
    ))
    sup = _make_supervisor(tmp_path, monkeypatch, verdict_json, project_worktree=project_root)

    assert sup._plan_next_work() is True
    items = sup.memory.backlog.all()
    assert [it.title for it in items] == ["Run the swebench canary again"]


def test_streak_below_limit_does_not_trip_the_breaker(tmp_path, monkeypatch) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    _write_error_streak(project_root, "swebench-verified-full-canary", count=2)  # < default limit of 3

    verdict_json = _flat_verdict_json((
        "Synchronize SWE canary handoff gate",
        "Resubmit the swebench-verified-full-canary run",
        "SWE-bench is still live at 150/500",
    ))
    sup = _make_supervisor(tmp_path, monkeypatch, verdict_json, project_worktree=project_root)

    assert sup._plan_next_work() is True
    items = sup.memory.backlog.all()
    assert [it.title for it in items] == ["Synchronize SWE canary handoff gate"]


def test_streak_limit_zero_disables_the_breaker(tmp_path, monkeypatch) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    _write_error_streak(project_root, "swebench-verified-full-canary", count=10)

    verdict_json = _flat_verdict_json((
        "Synchronize SWE canary handoff gate",
        "Resubmit the swebench-verified-full-canary run",
        "SWE-bench is still live at 150/500",
    ))
    sup = _make_supervisor(tmp_path, monkeypatch, verdict_json, project_worktree=project_root)
    sup.config.subagent_family_failure_streak_limit = 0

    assert sup._plan_next_work() is True
    items = sup.memory.backlog.all()
    assert [it.title for it in items] == ["Synchronize SWE canary handoff gate"]


def test_advisory_block_reaches_the_planner_prompt(tmp_path, monkeypatch) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    _write_error_streak(project_root, "swebench-verified-full-canary")

    verdict_json = _flat_verdict_json((
        "Write the related-work section",
        "Draft paper/main.tex related work",
        "unrelated to the stuck family",
    ))
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

    verdict_json = _flat_verdict_json((
        "Retry swebench_verified full canary",
        "Resubmit the swebench_verified_full_canary experiment",
        "benchmark_family: swebench_verified",
    ))
    sup = _make_supervisor(tmp_path, monkeypatch, verdict_json, project_worktree=project_root)

    assert sup._plan_next_work() == PLAN_RETRY
    assert sup.memory.backlog.all() == []
    events = sup._test_sink.events  # type: ignore[attr-defined]
    skipped = [e for e in events if e["type"] == "life.planner.task_skipped"]
    assert len(skipped) == 1
    assert skipped[0]["skip_category"] == "recent_subagent_family_failure"
