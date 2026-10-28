"""A finite research request ends once its own Reviewer accepted it.

Hosted trial s-bbe14024: the operator asked for one CartPole study written up
as a Chinese report ("…最后写成一份中文研究报告…所有数字必须来自真实运行"). The
Manager filed it bounded on the staged research route; the task passed its
independent review. The Planner was never told the request itself was done,
and a ``to=done`` would have been turned back because the research stages
(idea, experiment, paper, review) were not closed, so it queued a "final
certification" and then a "stage package" the operator never asked for, and
spent the rest of the trial budget on them.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from argus.core.models import RunnerResult
from argus.life.delivery import build_delivery_receipt
from argus.life.memory import BacklogItem, LifeMemory
from argus.life.supervisor import LifeSupervisor, LifeSupervisorConfig
from argus.skills.vertical_select import persist_vertical
from tests.hand_off import plan, submit

OBJECTIVE = (
    "在 CartPole-v1 上比较 REINFORCE、A2C 和 DQN 的样本效率，"
    "交付图表在前的中文研究报告 REPORT_zh.md。"
)


class _Sink:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def handle_event(self, event: dict[str, Any]) -> None:
        self.events.append(event)


class _Planner:
    """A Planner that ends the project, unless told to plan an audit."""

    def __init__(self, action: tuple[str, dict]) -> None:
        self.action = action
        self.prompts: list[str] = []

    def run_exec(self, **kwargs: Any) -> RunnerResult:
        self.prompts.append(str(kwargs.get("prompt") or ""))
        submit(kwargs["options"], self.action)
        return RunnerResult(exit_code=0, stdout_lines=[], stderr_lines=[])


def _done() -> tuple[str, dict]:
    return plan(to="done", summary="报告已由独立审阅接受，操作者要求的内容都已交付。")


def _supervisor(tmp_path: Path, monkeypatch, planner: _Planner, *, open_ended: bool = False):
    project = tmp_path / "project"
    project.mkdir()
    persist_vertical(
        project, "research", workflow_mode="staged", research_target_level="exploratory",
    )
    sink = _Sink()
    supervisor = LifeSupervisor(
        memory=LifeMemory.open(tmp_path / "life"),
        runner=object(),
        sink=sink,
        config=LifeSupervisorConfig(
            continuous=True,
            continuous_objective=OBJECTIVE,
            open_ended=open_ended,
            final_certification_gate=True,
            project_worktree=project,
            artifact_root=project,
        ),
        planner_runner=planner,
    )
    monkeypatch.setattr(supervisor, "_maybe_idle_after_unchanged_open_ended_done", lambda: None)
    monkeypatch.setattr(supervisor, "_resolve_vertical_once", lambda: None)
    monkeypatch.setattr(supervisor, "_wiki_collect_task_if_due_under_blocker", lambda: None)
    monkeypatch.setattr(supervisor, "_manager_publish_project_report", lambda *_a, **_k: "reported")
    return supervisor, project, sink


def _accepted_request(supervisor: LifeSupervisor, project: Path) -> BacklogItem:
    """The operator's request as the front door files it, accepted by its Reviewer."""
    item = supervisor.memory.backlog.add(BacklogItem.new(
        title="CartPole 三算法样本效率与稳定性研究",
        objective=OBJECTIVE,
        original_objective=OBJECTIVE,
        tags=["manager", "scope:bounded", "review:required", "operator",
              "operator_priority", "stage_transition:skip"],
    ))
    supervisor.memory.backlog.mark_done(item.id, outcome={
        "execution_status": "completed", "review_status": "done",
        "stage_certification": "intentionally_skipped", "interruption_kind": "none",
        "resumable": False,
    })
    (project / "REPORT_zh.md").write_text("# CartPole 研究报告\n", encoding="utf-8")
    receipt = build_delivery_receipt(
        item_id=item.id, title=item.title, summary="15 次真实 CPU 实验与报告一致。",
        success=True, overall_complete=False, status="done",
        review_status="done", review_source="reviewer",
        final_submission_certified=False, workspace=project,
        state_root=supervisor._project_state_root(),
        deliverables=[("REPORT_zh.md", "reviewer_accepted")], stage_package=True,
    )
    with supervisor.memory.journal.path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({
            "type": "life.mission.completed", "ts": time.time(), "item_id": item.id,
            "title": item.title, "scope": "bounded", "operator_request": True,
            "success": True, "status": "done", "review_source": "reviewer",
            "execution_workdir": str(project), "delivery": receipt,
            "summary": "15 次真实 CPU 实验与报告一致。",
        }, ensure_ascii=False) + "\n")
    return item


def _project_done(sink: _Sink) -> bool:
    return any(
        event.get("completion_kind") == "project_completed" and event.get("project_done")
        for event in sink.events
    )


def test_an_accepted_bounded_request_ends_the_project_without_more_tasks(
    tmp_path: Path, monkeypatch,
) -> None:
    planner = _Planner(_done())
    supervisor, project, sink = _supervisor(tmp_path, monkeypatch, planner)
    _accepted_request(supervisor, project)

    assert supervisor._plan_next_work() is False

    assert _project_done(sink)
    assert supervisor.memory.backlog.pending() == []
    assert not any(e.get("type") == "life.planner.completion_rejected" for e in sink.events)
    # The Planner was told the request is done, and that later stages are not owed.
    assert "operator_request: done" in planner.prompts[-1]


def test_a_changed_report_needs_its_review_again(tmp_path: Path, monkeypatch) -> None:
    supervisor, project, sink = _supervisor(tmp_path, monkeypatch, _Planner(_done()))
    _accepted_request(supervisor, project)
    (project / "REPORT_zh.md").write_text("# CartPole 研究报告（改）\n", encoding="utf-8")

    assert supervisor._bounded_request_accepted() == ""
    supervisor._plan_next_work()
    assert not _project_done(sink)


def test_a_standing_campaign_is_not_ended_by_one_accepted_request(
    tmp_path: Path, monkeypatch,
) -> None:
    supervisor, project, sink = _supervisor(
        tmp_path, monkeypatch, _Planner(_done()), open_ended=True,
    )
    _accepted_request(supervisor, project)

    assert supervisor._bounded_request_accepted() == ""
    supervisor._plan_next_work()
    assert not _project_done(sink)


def test_a_paper_keeps_its_final_certification(tmp_path: Path, monkeypatch) -> None:
    supervisor, project, sink = _supervisor(tmp_path, monkeypatch, _Planner(_done()))
    _accepted_request(supervisor, project)
    (project / "paper").mkdir()
    (project / "paper" / "main.tex").write_text("\\documentclass{article}\n", encoding="utf-8")

    assert supervisor._bounded_request_accepted() == ""
    supervisor._plan_next_work()
    assert not _project_done(sink)
