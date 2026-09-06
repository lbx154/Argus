"""A resumed Planner session is sent only what it has not already read.

Prompt forensics measured the resume "delta" at 69% of the full prompt: every
cycle re-sent the whole journal window and the whole research plan to a session
whose earlier turns already contained both. The supervisor now remembers which
journal entries and which research-plan text the session was shown, hands
``plan_next`` only the newly settled entries plus an unchanged-plan marker, and
falls back to the full context whenever it cannot prove the session saw the
older material (first cycle, restart, or a failed call whose session rotates).
"""

from __future__ import annotations

from pathlib import Path

from argus_skill.life.event_log import JsonlEventSink
from argus_skill.life.memory import LifeMemory
from argus_skill.life.supervisor import LifeBudget, LifeSupervisor, LifeSupervisorConfig
from argus_skill.life.supervisor._constants import PLAN_AWAITING, PLAN_ERROR
from argus_skill.planner import PlannerVerdict


class _Runner:
    pass


def _supervisor(project: Path, life: Path) -> LifeSupervisor:
    memory = LifeMemory.open(life)
    config = LifeSupervisorConfig(
        budget=LifeBudget(),
        poll_interval_seconds=0.01,
        continuous=True,
        continuous_objective="keep improving the solver",
        open_ended=False,
        final_certification_gate=False,
        project_worktree=project,
        artifact_root=project,
    )
    supervisor = LifeSupervisor(
        memory=memory,
        runner=_Runner(),
        sink=JsonlEventSink(None, life_dir=memory.root, verbosity="full"),
        config=config,
        planner_runner=object(),
    )
    supervisor._vertical_resolved = True
    return supervisor


def _waiting_verdict(error: str = "") -> PlannerVerdict:
    return PlannerVerdict(
        project_done=False,
        reason="the long training run is still producing its result",
        waiting=not error,
        waiting_reason="the long training run is still producing its result",
        error=error,
    )


def _install_recording_planner(monkeypatch, verdicts=None):
    calls: list[dict] = []

    def _plan_next(_planner, **kwargs):
        calls.append(kwargs)
        if verdicts:
            return verdicts.pop(0)
        return _waiting_verdict()

    monkeypatch.setattr("argus_skill.planner.Planner.plan_next", _plan_next)
    return calls


def _settle_mission(supervisor: LifeSupervisor, item_id: str, title: str) -> None:
    supervisor.sink.handle_event({
        "type": "life.mission.completed",
        "item_id": item_id,
        "title": title,
        "objective": f"objective for {title}",
        "success": True,
        "status": "done",
        "summary": f"{title} finished cleanly.",
    })


def test_first_cycle_sends_the_full_journal_without_a_delta(
    tmp_path: Path, monkeypatch
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    supervisor = _supervisor(project, tmp_path / "life")
    _settle_mission(supervisor, "item-a", "Entry Alpha settled")
    calls = _install_recording_planner(monkeypatch)

    assert supervisor._plan_next_work() == PLAN_AWAITING

    assert "Entry Alpha settled" in calls[0]["journal_tail"]
    assert calls[0]["journal_delta"] is None
    assert calls[0]["research_plan_unchanged"] is False


def test_unchanged_journal_and_plan_shrink_to_an_empty_delta(
    tmp_path: Path, monkeypatch
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    supervisor = _supervisor(project, tmp_path / "life")
    _settle_mission(supervisor, "item-a", "Entry Alpha settled")
    calls = _install_recording_planner(monkeypatch)

    assert supervisor._plan_next_work() == PLAN_AWAITING
    # Disarm the whole-cycle skip so this test observes the second call's
    # context; the skip has its own coverage.
    supervisor._planner_unchanged_skip_signature = ""
    assert supervisor._plan_next_work() == PLAN_AWAITING

    assert calls[1]["journal_delta"] == ""
    assert calls[1]["research_plan_unchanged"] is True
    assert "Entry Alpha settled" in calls[1]["journal_tail"]


def test_delta_carries_only_the_newly_settled_entries(
    tmp_path: Path, monkeypatch
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    supervisor = _supervisor(project, tmp_path / "life")
    _settle_mission(supervisor, "item-a", "Entry Alpha settled")
    calls = _install_recording_planner(monkeypatch)

    assert supervisor._plan_next_work() == PLAN_AWAITING
    _settle_mission(supervisor, "item-b", "Entry Beta settled")
    assert supervisor._plan_next_work() == PLAN_AWAITING

    delta = calls[1]["journal_delta"]
    assert delta is not None
    assert "Entry Beta settled" in delta
    assert "Entry Alpha settled" not in delta
    assert "Entry Alpha settled" in calls[1]["journal_tail"]
    assert "Entry Beta settled" in calls[1]["journal_tail"]


def test_failed_call_keeps_the_older_entries_in_the_next_delta(
    tmp_path: Path, monkeypatch
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    supervisor = _supervisor(project, tmp_path / "life")
    calls = _install_recording_planner(
        monkeypatch,
        verdicts=[
            _waiting_verdict(),
            _waiting_verdict(error="backend exit 1"),
            _waiting_verdict(),
        ],
    )

    assert supervisor._plan_next_work() == PLAN_AWAITING
    _settle_mission(supervisor, "item-b", "Entry Beta settled")
    # The failed call rotates its session; what it was sent proves nothing
    # about what the next session has read.
    assert supervisor._plan_next_work() == PLAN_ERROR
    assert supervisor._plan_next_work() == PLAN_AWAITING

    assert calls[2]["journal_delta"] is not None
    assert "Entry Beta settled" in calls[2]["journal_delta"]
