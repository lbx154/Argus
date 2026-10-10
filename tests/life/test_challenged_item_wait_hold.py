"""A challenged item is not re-run while the Planner's answer to the challenge is a wait.

ab1009 freight-dispatch-shift, arms D2 and E: a mission ended asking for a
replan, the Planner answered with a wait ("preserve the existing CLI and
pending task without restarting work"), and the very next tick ran the
challenged item again. Every hold tested here also has its release tested.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from argus.core.models import RunnerResult
from argus.life.event_log import JsonlEventSink
from argus.life.memory import BacklogItem, LifeMemory, wait_hold_active
from argus.life.supervisor import LifeBudget, LifeSupervisor, LifeSupervisorConfig
from argus.life.supervisor._constants import PLAN_AWAITING
from argus.life.supervisor._wait_holds import (
    EVENT_HOLD_CEILING_SECONDS,
    FALLBACK_HOLD_SECONDS,
    WAIT_HOLD_RELEASED,
    WAIT_HOLD_SET,
)
from argus.planner import PlannerConfig
from argus.skills.vertical_select import persist_vertical

_WATCHED = "review-evidence/access-availability.txt"


class _MissionRunner:
    pass


class _PlannerBackend:
    def __init__(self, replies: list[str]) -> None:
        self.replies = list(replies)
        self.calls: list[dict] = []

    def run_exec(self, **kwargs):  # noqa: ANN003
        self.calls.append(kwargs)
        return RunnerResult(exit_code=0, agent_messages=[self.replies.pop(0)])


def _wait_reply(**fields: str) -> str:
    values = {
        "PROJECT_DONE": "false",
        "WAITING": "true",
        "REASON": "Preserve the existing CLI and pending task without restarting work.",
        "BLOCKER_FINGERPRINT": "dispatch-feed-access-unavailable",
        "RECHECK_CONDITION": "Authorized feed access is supplied.",
        "RECHECK_TOKEN": "access-round-4",
        "WAIT_MODE": "event",
        "WAKE_ON": "artifact_revision",
        "WATCHED_PATHS": _WATCHED,
    }
    values.update(fields)
    return "\n".join(f"{key}={value}" for key, value in values.items() if value != "")


def _supervisor(tmp_path: Path, planner: _PlannerBackend) -> tuple[LifeSupervisor, Path]:
    project = tmp_path / "project"
    (project / "review-evidence").mkdir(parents=True)
    (project / _WATCHED).write_text("token absent\n", encoding="utf-8")
    memory = LifeMemory.open(tmp_path / "life")
    supervisor = LifeSupervisor(
        memory=memory,
        runner=_MissionRunner(),
        sink=JsonlEventSink(None, life_dir=memory.root, verbosity="full"),
        config=LifeSupervisorConfig(
            budget=LifeBudget(),
            continuous=True,
            continuous_objective="Build the stateful dispatch CLI.",
            open_ended=False,
            final_certification_gate=False,
            project_worktree=project,
            artifact_root=project,
        ),
        planner_runner=planner,
    )
    persist_vertical(project, "software", workflow_mode="direct")
    supervisor._vertical_resolved = True
    supervisor._planner_config = lambda: PlannerConfig(  # type: ignore[method-assign]
        working_dir=str(project), open_ended=False,
    )
    return supervisor, project


def _challenge(supervisor: LifeSupervisor) -> tuple[BacklogItem, dict]:
    item = supervisor.memory.backlog.add(BacklogItem.new(
        title="Build the dispatch CLI",
        objective="Build the stateful dispatch CLI described by the packet.",
        item_id="dispatch-cli",
        plan_id="plan-1",
        plan_version=1,
        node_key="dispatch-cli",
    ))
    outcome = {
        "item_id": item.id,
        "status": "replan_requested",
        "review_status": "replan_requested",
        "review_reason": "Live verification still needs the feed token.",
        "expected_plan_id": "plan-1",
        "expected_plan_version": 1,
        "planner_report": {
            "plan_signal": "reconsider",
            "challenge": "Unchanged rounds cannot verify the live feed.",
            "alternative": "Wait for authorized access.",
            "authority_impact": "technical",
        },
    }
    return item, outcome


def _events(supervisor: LifeSupervisor) -> list[dict]:
    path = supervisor.memory.root / "events.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _row(supervisor: LifeSupervisor, item_id: str) -> BacklogItem:
    return next(row for row in supervisor.memory.backlog.all() if row.id == item_id)


def test_a_wait_answer_holds_the_challenged_item_until_its_watched_input_changes(tmp_path) -> None:
    supervisor, project = _supervisor(tmp_path, _PlannerBackend([_wait_reply()]))
    item, outcome = _challenge(supervisor)

    assert supervisor._plan_next_work(revision_request=outcome) == PLAN_AWAITING

    held = _row(supervisor, item.id)
    contract = supervisor._load_planner_waiting_contract_state()
    assert contract is not None and contract["active"]
    assert held.status == "pending"
    assert held.wait_hold["wait_id"] == contract["wait_id"]
    assert wait_hold_active(held)
    # Event wait with no recheck time: bounded by the silence ceiling.
    assert held.wait_hold["until"] <= time.time() + EVENT_HOLD_CEILING_SECONDS + 1
    assert any(e["type"] == WAIT_HOLD_SET and e["contract_persisted"] for e in _events(supervisor))
    # The next tick does not re-run it.
    assert supervisor.memory.backlog.next_pending() is None
    assert supervisor.tick() is None
    assert _row(supervisor, item.id).status == "pending"

    # Release path: the watched input changes, the event wait wakes, the hold ends.
    (project / _WATCHED).write_text("token supplied\n", encoding="utf-8")
    supervisor._planner_event_wait_outcome()  # spends the one granted turn
    supervisor._planner_event_wait_outcome()
    assert not supervisor._load_planner_waiting_contract_state()["active"]
    assert supervisor._release_wait_holds() == 1
    assert _row(supervisor, item.id).wait_hold == {}
    assert supervisor.memory.backlog.next_pending().id == item.id
    released = [e for e in _events(supervisor) if e["type"] == WAIT_HOLD_RELEASED]
    assert released[-1]["release_reason"] == "wait_ended"


def test_a_manager_or_operator_ending_the_wait_releases_the_item(tmp_path) -> None:
    supervisor, _project = _supervisor(tmp_path, _PlannerBackend([_wait_reply()]))
    item, outcome = _challenge(supervisor)
    supervisor._plan_next_work(revision_request=outcome)
    assert wait_hold_active(_row(supervisor, item.id))

    supervisor._resolve_planner_waiting_contract(
        manager_reason="Advance with a fixture-backed delivery.", target_stage="delivery",
    )
    assert supervisor._release_wait_holds() == 1
    assert supervisor.memory.backlog.next_pending().id == item.id


def test_the_planner_moving_on_releases_the_item(tmp_path) -> None:
    supervisor, _project = _supervisor(tmp_path, _PlannerBackend([_wait_reply()]))
    item, outcome = _challenge(supervisor)
    supervisor._plan_next_work(revision_request=outcome)

    supervisor._deactivate_planner_waiting_contract()
    assert supervisor._release_wait_holds() == 1
    assert not wait_hold_active(_row(supervisor, item.id))


def test_a_poll_wait_holds_only_until_its_timed_recheck(tmp_path, monkeypatch) -> None:
    reply = _wait_reply(WAIT_MODE="poll", WAKE_ON="", WATCHED_PATHS="", RECHECK_AFTER_SECONDS="600")
    supervisor, _project = _supervisor(tmp_path, _PlannerBackend([reply]))
    item, outcome = _challenge(supervisor)
    supervisor._plan_next_work(revision_request=outcome)

    hold = _row(supervisor, item.id).wait_hold
    assert hold["wait_id"]
    assert hold["until"] - hold["since"] == 600
    assert supervisor.memory.backlog.next_pending() is None

    # Release path: the timed recheck comes due while the contract is still active.
    later = hold["until"] + 1
    monkeypatch.setattr(time, "time", lambda: later)
    assert supervisor._load_planner_waiting_contract_state()["active"]
    assert supervisor.memory.backlog.next_pending().id == item.id
    assert supervisor._release_wait_holds() == 1
    released = [e for e in _events(supervisor) if e["type"] == WAIT_HOLD_RELEASED]
    assert released[-1]["release_reason"] == "recheck_due"


def test_a_wait_without_a_persisted_contract_falls_back_to_a_timed_recheck(tmp_path, monkeypatch) -> None:
    # No blocker fields: the Planner waits but there is no contract to persist.
    reply = _wait_reply(BLOCKER_FINGERPRINT="", RECHECK_TOKEN="", RECHECK_CONDITION="",
                        WAIT_MODE="", WAKE_ON="", WATCHED_PATHS="")
    supervisor, _project = _supervisor(tmp_path, _PlannerBackend([reply]))
    item, outcome = _challenge(supervisor)
    assert supervisor._plan_next_work(revision_request=outcome) == PLAN_AWAITING

    hold = _row(supervisor, item.id).wait_hold
    assert hold["wait_id"] == ""
    assert hold["until"] - hold["since"] == FALLBACK_HOLD_SECONDS
    assert supervisor.tick() is None
    # A tick before the recheck keeps the hold; there is no contract to end it.
    assert supervisor._release_wait_holds() == 0

    later = hold["until"] + 1
    monkeypatch.setattr(time, "time", lambda: later)
    assert supervisor.memory.backlog.next_pending().id == item.id


def test_the_scheduler_alone_never_strands_a_held_item(tmp_path, monkeypatch) -> None:
    """Even if the supervisor never reconciles, ``until`` lapses on its own."""
    memory = LifeMemory.open(tmp_path / "life")
    item = memory.backlog.add(BacklogItem.new(title="t", objective="o"))
    memory.backlog.update(item.id, wait_hold={"wait_id": "w", "since": time.time(), "until": time.time() + 60})
    assert memory.backlog.next_pending() is None
    later = time.time() + 61
    monkeypatch.setattr(time, "time", lambda: later)
    claimed = memory.backlog.claim_next()
    assert claimed is not None and claimed.id == item.id
    assert claimed.wait_hold == {}
    # A malformed hold never blocks.
    other = memory.backlog.add(BacklogItem.new(title="u", objective="o"))
    memory.backlog.update(other.id, wait_hold={"until": "soon"})
    assert memory.backlog.next_pending().id == other.id


def test_a_bounded_run_with_only_a_held_item_waits_instead_of_exiting(tmp_path) -> None:
    supervisor, _project = _supervisor(tmp_path, _PlannerBackend([_wait_reply()]))
    item, outcome = _challenge(supervisor)
    supervisor._plan_next_work(revision_request=outcome)
    assert supervisor._has_wait_held_items()
    # The hold survives a round trip through the backlog file.
    reopened = LifeMemory.open(tmp_path / "life")
    assert wait_hold_active(next(row for row in reopened.backlog.all() if row.id == item.id))
