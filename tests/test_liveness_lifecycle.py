"""Mission-lifecycle liveness: every wait has an exit and no handoff loses work."""

from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from argus.core.event_catalog import EventType
from argus.core.models import RunnerResult
from argus.life.event_log import JsonlEventSink
from argus.life.memory import BacklogItem, LifeMemory
from argus.life.runtime_failure_circuit import (
    CANARY_TAG,
    CIRCUIT_FILENAME,
    active_runtime_failure_circuit,
    claim_runtime_failure_half_open,
    clear_runtime_failure_circuit,
    record_runtime_failure_circuit,
    runtime_failure_canary_cooldown_seconds,
)
from argus.life.supervisor import LifeBudget, LifeSupervisor, LifeSupervisorConfig
from argus.life.supervisor._constants import PLAN_AWAITING, PLAN_RETRY
from argus.planner import PlannerConfig, parse_planner_text
from argus.skills.vertical_select import persist_vertical


class _MissionRunner:
    pass


class _PlannerBackend:
    def __init__(self, replies: list[str]) -> None:
        self.replies = list(replies)
        self.calls: list[dict] = []

    def run_exec(self, **kwargs):  # noqa: ANN003
        self.calls.append(kwargs)
        return RunnerResult(exit_code=0, agent_messages=[self.replies.pop(0)])


def _supervisor(
    project: Path, life: Path, planner: _PlannerBackend | None = None,
) -> LifeSupervisor:
    memory = LifeMemory.open(life)
    supervisor = LifeSupervisor(
        memory=memory,
        runner=_MissionRunner(),
        sink=JsonlEventSink(None, life_dir=memory.root, verbosity="full"),
        config=LifeSupervisorConfig(
            budget=LifeBudget(),
            poll_interval_seconds=0.01,
            continuous=True,
            continuous_objective="keep improving the solver",
            open_ended=False,
            project_worktree=project,
            artifact_root=project,
        ),
        planner_runner=planner if planner is not None else _PlannerBackend([]),
    )
    persist_vertical(project, "software", workflow_mode="direct")
    supervisor._vertical_resolved = True
    supervisor._planner_config = lambda: PlannerConfig(  # type: ignore[method-assign]
        working_dir=str(project),
    )
    return supervisor


def _events(supervisor: LifeSupervisor) -> list[dict]:
    path = supervisor.memory.root / "events.jsonl"
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _project(tmp_path: Path, monkeypatch) -> Path:
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.chdir(project)
    return project


# I2/I4: a park no automatic path resumes is the Planner's to retire.


def test_planner_sees_and_retires_a_wedging_park_and_its_dependents_are_released(
    tmp_path: Path, monkeypatch,
) -> None:
    project = _project(tmp_path, monkeypatch)
    planner = _PlannerBackend([])
    supervisor = _supervisor(project, tmp_path / "life", planner)
    backlog = supervisor.memory.backlog
    park = backlog.add(BacklogItem.new(title="Prove the bound", objective="prove it"))
    backlog.update(park.id, status="research_incomplete")
    child = backlog.add(
        BacklogItem.new(title="Write up the bound", objective="write", deps=[park.id])
    )
    question = backlog.add(BacklogItem.new(title="Pick a venue", objective="venue"))
    backlog.update(
        question.id, status="paused_operator", pending_question="Which venue?"
    )

    digest = supervisor._planner_current_reality_note()
    assert f"parked task {park.id} (research_incomplete)" in digest
    assert f"blocks pending: [{child.id}]" in digest
    assert f"parked task {question.id}" not in digest, "questions stay the operator's"

    planner.replies.append("\n".join([
        "PROJECT_DONE=false",
        "REASON=Retire the stuck proof and attempt it again with a new method.",
        f"RETIRE_TASK={park.id} | The current method is exhausted.",
        f"RETIRE_TASK={question.id} | Not mine to retire.",
        "TASK_KEY=again",
        "TASK_TITLE=Prove the bound",
        "TASK_OBJECTIVE=prove it",
    ]))
    assert supervisor._plan_next_work() is True

    rows = {item.id: item for item in backlog.history()}
    assert rows[park.id].status == "superseded"
    assert rows[question.id].status == "paused_operator"
    assert rows[question.id].pending_question == "Which venue?"
    # Retired in the same verdict, the park is not a duplicate of its replacement.
    replacement = [
        item for item in backlog.pending()
        if item.title == "Prove the bound" and item.id != park.id
    ]
    assert len(replacement) == 1
    backlog.next_pending()
    child_row = next(item for item in backlog.history() if item.id == child.id)
    assert child_row.status == "skipped"
    assert park.id in child_row.last_error


def test_retiring_a_park_while_reproposing_it_and_its_dependent_keeps_the_dependent(
    tmp_path: Path, monkeypatch,
) -> None:
    project = _project(tmp_path, monkeypatch)
    planner = _PlannerBackend([])
    supervisor = _supervisor(project, tmp_path / "life", planner)
    backlog = supervisor.memory.backlog
    park = backlog.add(BacklogItem.new(title="Prove the bound", objective="prove it"))
    backlog.update(park.id, status="research_incomplete")
    child = backlog.add(
        BacklogItem.new(title="Write up the bound", objective="write", deps=[park.id])
    )
    planner.replies.append("\n".join([
        "PROJECT_DONE=false",
        "REASON=Retire the stuck proof and redo it, then write it up.",
        f"RETIRE_TASK={park.id} | The current method is exhausted.",
        "TASK_KEY=again",
        "TASK_TITLE=Prove the bound",
        "TASK_OBJECTIVE=prove it",
        "TASK_KEY=writeup",
        "TASK_DEPS=again",
        "TASK_TITLE=Write up the bound",
        "TASK_OBJECTIVE=write",
    ]))

    assert supervisor._plan_next_work() is True
    backlog.next_pending()

    rows = {item.id: item for item in backlog.history()}
    # The old dependent is skipped by the retirement cascade, so it must not
    # have absorbed its re-proposal as a "duplicate".
    assert rows[child.id].status == "skipped"
    proofs = [
        item for item in rows.values()
        if item.title == "Prove the bound" and item.status == "pending"
    ]
    writeups = [
        item for item in rows.values()
        if item.title == "Write up the bound" and item.status == "pending"
    ]
    assert len(proofs) == 1 and proofs[0].id != park.id
    assert len(writeups) == 1 and writeups[0].id != child.id
    assert writeups[0].deps == [proofs[0].id]


def test_retire_never_touches_running_auto_resumed_or_question_rows(
    tmp_path: Path,
) -> None:
    backlog = LifeMemory.open(tmp_path / "life").backlog
    rows = {}
    for status in (
        "running", "paused_provider_cooldown", "paused_external_work",
        "paused_operator", "paused",
    ):
        item = backlog.add(BacklogItem.new(title=status, objective=status))
        backlog.update(item.id, status=status)
        rows[status] = item.id
    backlog.update(rows["paused"], pending_question="May I continue?")

    retired = backlog.supersede_items(
        item_ids=rows.values(), reason="r", superseded_by_plan_id="p",
        include_parked=True,
    )

    assert retired == ()


# I2: the runtime-failure circuit has a reachable reset.


def _open_circuit(root: Path, *, item_id: str = "boom") -> dict:
    try:
        raise RuntimeError("orchestration exploded")
    except RuntimeError as exc:
        return record_runtime_failure_circuit(root, exc, item_id=item_id)


def test_half_open_window_admits_one_canary_and_backs_off_after_a_failure(
    tmp_path: Path,
) -> None:
    root = tmp_path / "life"
    state = _open_circuit(root)
    opened = float(state["first_observed_at"])
    first_due = opened + runtime_failure_canary_cooldown_seconds(1)

    assert not claim_runtime_failure_half_open(
        root, slot="mission", item_id="a", now=first_due - 1
    )
    assert claim_runtime_failure_half_open(
        root, slot="mission", item_id="a", now=first_due
    )
    # One canary per window; the Planner keeps its own single turn.
    assert not claim_runtime_failure_half_open(
        root, slot="mission", item_id="b", expected_canary_item_id="a", now=first_due
    )
    assert claim_runtime_failure_half_open(
        root, slot="planner", expected_canary_item_id="a", now=first_due
    )
    assert not claim_runtime_failure_half_open(
        root, slot="planner", expected_canary_item_id="a", now=first_due
    )

    # The canary raised the same failure: the circuit stays open and keeps its
    # schedule, so the next window waits the doubled cooldown.
    _open_circuit(root, item_id="a")
    second_due = first_due + runtime_failure_canary_cooldown_seconds(2)
    assert not claim_runtime_failure_half_open(
        root, slot="mission", item_id="b", expected_canary_item_id="a",
        now=second_due - 1,
    )
    assert claim_runtime_failure_half_open(
        root, slot="mission", item_id="b", expected_canary_item_id="a", now=second_due
    )
    assert json.loads((root / CIRCUIT_FILENAME).read_text())["canary_window"] == 2


def test_supervisor_tags_the_admitted_canary_and_holds_everything_else(
    tmp_path: Path, monkeypatch,
) -> None:
    project = _project(tmp_path, monkeypatch)
    supervisor = _supervisor(project, tmp_path / "life")
    root = supervisor.memory.root
    _open_circuit(root)
    state = json.loads((root / CIRCUIT_FILENAME).read_text())
    state["canary_due_at"] = time.time() - 1
    (root / CIRCUIT_FILENAME).write_text(json.dumps(state))
    backlog = supervisor.memory.backlog
    first = backlog.add(BacklogItem.new(title="first", objective="a"))
    second = backlog.add(BacklogItem.new(title="second", objective="b"))

    assert supervisor._runtime_failure_circuit_block(item=first) is None
    stored = next(item for item in backlog.active() if item.id == first.id)
    assert CANARY_TAG in stored.tags
    blocked = supervisor._runtime_failure_circuit_block(item=second)
    assert blocked is not None and blocked["status"] == "infra_blocked"
    assert any(
        event["type"] == "life.status" and "half-open" in event.get("text", "")
        for event in _events(supervisor)
    )
    # The circuit is still open until the canary's reviewed pass clears it.
    assert active_runtime_failure_circuit(root) is not None
    # The supervisor only tries the head of the queue, so the admitted canary
    # must stay there even behind an operator-priority row.
    urgent = BacklogItem.new(title="urgent", objective="c", priority=-5)
    backlog.add(urgent)
    assert backlog.next_pending().id == first.id


def _make_window_due(root: Path) -> None:
    path = root / CIRCUIT_FILENAME
    state = json.loads(path.read_text())
    state["canary_due_at"] = time.time() - 1
    path.write_text(json.dumps(state))


def test_a_failed_canary_tag_does_not_wedge_the_half_open_window(
    tmp_path: Path, monkeypatch,
) -> None:
    project = _project(tmp_path, monkeypatch)
    supervisor = _supervisor(project, tmp_path / "life")
    root = supervisor.memory.root
    _open_circuit(root)
    _make_window_due(root)
    backlog = supervisor.memory.backlog
    first = backlog.add(BacklogItem.new(title="first", objective="a"))
    real_update = backlog.update
    failures = {"left": 1}

    def flaky_update(item_id, **fields):
        if "tags" in fields and failures["left"]:
            failures["left"] -= 1
            raise OSError("transient disk error")
        return real_update(item_id, **fields)

    monkeypatch.setattr(backlog, "update", flaky_update)

    # The claim is recorded, then tagging fails: nothing is admitted.
    assert supervisor._runtime_failure_circuit_block(item=first) is not None
    disk = json.loads((root / CIRCUIT_FILENAME).read_text())
    assert disk["canary_item_id"] == first.id
    assert CANARY_TAG not in next(i for i in backlog.active() if i.id == first.id).tags

    # The untagged row never ran, so it is not a canary in flight: the next
    # window admits a new item instead of holding both slots forever.
    _make_window_due(root)
    second = backlog.add(BacklogItem.new(title="second", objective="b"))
    assert supervisor._runtime_failure_circuit_block(item=second) is None
    disk = json.loads((root / CIRCUIT_FILENAME).read_text())
    assert disk["canary_item_id"] == second.id
    assert CANARY_TAG in next(i for i in backlog.active() if i.id == second.id).tags


def test_a_canary_tag_on_a_row_the_circuit_did_not_admit_neither_passes_nor_clears(
    tmp_path: Path, monkeypatch,
) -> None:
    project = _project(tmp_path, monkeypatch)
    supervisor = _supervisor(project, tmp_path / "life")
    root = supervisor.memory.root
    backlog = supervisor.memory.backlog
    _open_circuit(root)
    _make_window_due(root)
    admitted = backlog.add(BacklogItem.new(title="admitted", objective="a"))
    assert supervisor._runtime_failure_circuit_block(item=admitted) is None
    # A tag this circuit did not hand out (left by an earlier circuit, or
    # copied onto another row) is not a pass once a canary was admitted.
    other = BacklogItem.new(title="other", objective="b", tags=[CANARY_TAG])
    backlog.add(other)

    held = supervisor._runtime_failure_circuit_block(item=other)
    assert held is not None and held["status"] == "infra_blocked"
    assert not clear_runtime_failure_circuit(
        root, reason="other passed", canary_item_id=other.id
    )
    assert active_runtime_failure_circuit(root) is not None
    assert clear_runtime_failure_circuit(
        root, reason="admitted canary passed", canary_item_id=admitted.id
    )


def test_a_canary_tag_before_any_admission_waits_for_a_window_and_cannot_clear(
    tmp_path: Path, monkeypatch,
) -> None:
    project = _project(tmp_path, monkeypatch)
    supervisor = _supervisor(project, tmp_path / "life")
    root = supervisor.memory.root
    _open_circuit(root)
    stale = BacklogItem.new(title="stale", objective="a", tags=[CANARY_TAG])
    supervisor.memory.backlog.add(stale)

    held = supervisor._runtime_failure_circuit_block(item=stale)
    assert held is not None and held["status"] == "infra_blocked"
    assert not clear_runtime_failure_circuit(
        root, reason="stale tag passed", canary_item_id=stale.id
    )
    assert active_runtime_failure_circuit(root) is not None


def test_a_changed_fact_opens_a_half_open_window_without_waiting(
    tmp_path: Path,
) -> None:
    root = tmp_path / "life"
    opened = float(_open_circuit(root)["first_observed_at"])

    # The first claim records what it saw; the same facts are not news.
    assert not claim_runtime_failure_half_open(
        root, slot="mission", item_id="a", facts={"backend": "a"}, now=opened + 1
    )
    assert not claim_runtime_failure_half_open(
        root, slot="mission", item_id="a", facts={"backend": "a"}, now=opened + 2
    )
    assert claim_runtime_failure_half_open(
        root, slot="mission", item_id="a", facts={"backend": "b"}, now=opened + 3
    )
    # One canary per window, and the window has now seen these facts.
    assert not claim_runtime_failure_half_open(
        root, slot="mission", item_id="b", expected_canary_item_id="a",
        facts={"backend": "b"}, now=opened + 4,
    )
    assert json.loads((root / CIRCUIT_FILENAME).read_text())["canary_facts"] == {
        "backend": "b"
    }


def test_the_timed_canary_window_is_a_knob_that_can_leave_windows_to_facts(
    tmp_path: Path, monkeypatch,
) -> None:
    assert runtime_failure_canary_cooldown_seconds(1) == 1800.0
    # Each failed window doubles the wait; there is no fixed ceiling, since a
    # changed fact opens the next window at once.
    assert runtime_failure_canary_cooldown_seconds(8) == 1800.0 * 2**7
    monkeypatch.setenv("ARGUS_SKILL_RUNTIME_CANARY_COOLDOWN_MIN", "10")
    assert runtime_failure_canary_cooldown_seconds(2) == 1200.0

    monkeypatch.setenv("ARGUS_SKILL_RUNTIME_CANARY_COOLDOWN_MIN", "0")
    root = tmp_path / "life"
    opened = float(_open_circuit(root)["first_observed_at"])
    assert not claim_runtime_failure_half_open(
        root, slot="mission", item_id="a", facts={"model": "m1"}, now=opened + 30 * 86400
    )
    assert claim_runtime_failure_half_open(
        root, slot="mission", item_id="a", facts={"model": "m2"}, now=opened + 30 * 86400
    )
    # No timed window is configured, so none is scheduled.
    assert json.loads((root / CIRCUIT_FILENAME).read_text())["canary_due_at"] == 0.0


def test_supervisor_admits_a_canary_when_its_watched_facts_change(
    tmp_path: Path, monkeypatch,
) -> None:
    project = _project(tmp_path, monkeypatch)
    supervisor = _supervisor(project, tmp_path / "life")
    root = supervisor.memory.root
    _open_circuit(root)
    assert supervisor._runtime_failure_canary_facts()
    item = supervisor.memory.backlog.add(BacklogItem.new(title="work", objective="a"))

    assert supervisor._runtime_failure_circuit_block(item=item) is not None
    monkeypatch.setattr(
        supervisor, "_runtime_failure_canary_facts", lambda: {"operator_context_revision": 99}
    )
    assert supervisor._runtime_failure_circuit_block(item=item) is None
    stored = next(row for row in supervisor.memory.backlog.active() if row.id == item.id)
    assert CANARY_TAG in stored.tags


def test_rows_derived_from_a_canary_do_not_inherit_its_tag(tmp_path: Path) -> None:
    backlog = LifeMemory.open(tmp_path / "life").backlog
    asked = BacklogItem.new(title="asked", objective="a", tags=["planner", CANARY_TAG])
    backlog.add(asked)
    backlog.update(asked.id, status="paused_operator", pending_question="Go on?")
    ended = BacklogItem.new(title="ended", objective="b", tags=["planner", CANARY_TAG])
    backlog.add(ended)
    backlog.mark_failed(ended.id, error="replan after progress")

    _blocked, continuation = backlog.continue_with_operator_reply(asked.id, "Yes.")
    holder = backlog.park_terminal_operator_question(
        ended.id,
        holder_id=BacklogItem.new_id(),
        question="May the gate change?",
        operator_decision={},
    )

    assert continuation is not None and holder is not None
    assert CANARY_TAG not in continuation.tags and "planner" in continuation.tags
    assert CANARY_TAG not in holder.tags and "planner" in holder.tags


# Lossy handoffs.


def test_waiting_verdict_with_concrete_tasks_enqueues_the_tasks(
    tmp_path: Path, monkeypatch,
) -> None:
    project = _project(tmp_path, monkeypatch)
    planner = _PlannerBackend(["\n".join([
        "PROJECT_DONE=false",
        "REASON=Wait for the sweep but write the analysis harness meanwhile.",
        "WAITING=true",
        "WAITING_REASON=The sweep is still running.",
        "TASK_KEY=harness",
        "TASK_TITLE=Write the analysis harness",
        "TASK_OBJECTIVE=Write the harness that reads the sweep output schema.",
    ])])
    supervisor = _supervisor(project, tmp_path / "life", planner)

    assert supervisor._plan_next_work() is True

    assert [item.title for item in supervisor.memory.backlog.pending()] == [
        "Write the analysis harness"
    ]


def _wait_with_task(*fields: str) -> str:
    return "\n".join([
        "PROJECT_DONE=false",
        "REASON=The release waits; write the changelog meanwhile.",
        "WAITING=true",
        "WAITING_REASON=The release is held.",
        "WAIT=The release hold is lifted",
        *fields,
        "TASK_KEY=changelog",
        "TASK_TITLE=Write the changelog",
        "TASK_OBJECTIVE=Write the changelog for the held release.",
    ])


def _active_wait(life: Path) -> dict:
    paths = list(life.glob("planner-waiting-contract*.json"))
    if not paths:
        return {}
    state = json.loads(paths[0].read_text(encoding="utf-8"))
    return state if state.get("active") else {}


def test_operator_wait_declared_with_tasks_is_kept_beside_them(
    tmp_path: Path, monkeypatch,
) -> None:
    monkeypatch.setenv("ARGUS_SKILL_OPERATOR_AVAILABLE", "true")
    project = _project(tmp_path, monkeypatch)
    life = tmp_path / "life"
    planner = _PlannerBackend([_wait_with_task("OPERATOR_ACTION_REQUIRED=true")])
    supervisor = _supervisor(project, life, planner)

    assert supervisor._plan_next_work() is True

    assert [item.title for item in supervisor.memory.backlog.pending()] == [
        "Write the changelog"
    ]
    wait = _active_wait(life)
    assert wait["operator_action_required"] is True
    assert wait["recheck_condition"] == "The release hold is lifted"


def test_operator_wait_with_tasks_and_no_operator_is_left_to_the_next_bare_wait(
    tmp_path: Path, monkeypatch,
) -> None:
    monkeypatch.setenv("ARGUS_SKILL_OPERATOR_AVAILABLE", "false")
    project = _project(tmp_path, monkeypatch)
    life = tmp_path / "life"
    planner = _PlannerBackend([_wait_with_task("OPERATOR_ACTION_REQUIRED=true")])
    supervisor = _supervisor(project, life, planner)

    assert supervisor._plan_next_work() is True

    assert [item.title for item in supervisor.memory.backlog.pending()] == [
        "Write the changelog"
    ]
    assert _active_wait(life) == {}


class _StageManager:
    def __init__(self, decision: SimpleNamespace) -> None:
        self.decision = decision
        self.calls: list[dict] = []

    def decide_stage_transition(self, **kwargs):  # noqa: ANN003
        self.calls.append(kwargs)
        return self.decision


def _stage_decision(**overrides) -> SimpleNamespace:  # noqa: ANN003
    fields = {
        "action": "hold",
        "target_stage": "",
        "reason": "The stage still owes its review.",
        "current_stage": "",
        "source": "manager_llm",
        "diagnostic": "",
        "resolves_wait": False,
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


def test_stage_reconciliation_request_with_tasks_reaches_the_manager_and_replans(
    tmp_path: Path, monkeypatch,
) -> None:
    """85235d97: a bounded campaign reconciles an explicit stage request. Tasks
    beside it do not swallow the request; the Manager's repair is planned."""
    project = _project(tmp_path, monkeypatch)
    life = tmp_path / "life"
    planner = _PlannerBackend([_wait_with_task("STAGE_RECONCILIATION_REQUIRED=true")])
    supervisor = _supervisor(project, life, planner)
    manager = _StageManager(_stage_decision())
    supervisor._bound_manager = lambda: manager  # type: ignore[method-assign]

    assert supervisor._plan_next_work() == PLAN_RETRY

    assert len(manager.calls) == 1
    assert manager.calls[0]["open_ended"] is False
    assert manager.calls[0]["planner_verdict"].new_tasks == []
    assert supervisor.memory.backlog.pending() == []
    feedback = supervisor._load_manager_planner_feedback()
    assert feedback["diagnostic"] == "manager_hold_requires_stage_repair"


def test_stage_reconciliation_request_held_by_the_manager_keeps_tasks_and_wait(
    tmp_path: Path, monkeypatch,
) -> None:
    project = _project(tmp_path, monkeypatch)
    life = tmp_path / "life"
    planner = _PlannerBackend([_wait_with_task("STAGE_RECONCILIATION_REQUIRED=true")])
    supervisor = _supervisor(project, life, planner)
    manager = _StageManager(_stage_decision(source="no_review_hold"))
    supervisor._bound_manager = lambda: manager  # type: ignore[method-assign]

    assert supervisor._plan_next_work() is True

    assert len(manager.calls) == 1
    assert [item.title for item in supervisor.memory.backlog.pending()] == [
        "Write the changelog"
    ]
    assert _active_wait(life)["stage_reconciliation_required"] is True


def test_child_of_a_filtered_in_batch_parent_is_held_not_run_without_it(
    tmp_path: Path, monkeypatch,
) -> None:
    project = _project(tmp_path, monkeypatch)
    planner = _PlannerBackend(["\n".join([
        "PROJECT_DONE=false",
        "REASON=Build the parser, then use it.",
        "TASK_KEY=parser",
        "TASK_VERTICAL=no_such_vertical",
        "TASK_TITLE=Build the parser",
        "TASK_OBJECTIVE=Build the parser.",
        "TASK_KEY=use",
        "TASK_DEPS=parser",
        "TASK_TITLE=Use the parser",
        "TASK_OBJECTIVE=Run the parser over the corpus.",
        "TASK_KEY=report",
        "TASK_DEPS=use",
        "TASK_TITLE=Report the parse",
        "TASK_OBJECTIVE=Summarize the parse results.",
        "TASK_KEY=solo",
        "TASK_DEPS=imagined-key",
        "TASK_TITLE=Independent cleanup",
        "TASK_OBJECTIVE=Tidy the fixtures.",
    ])])
    supervisor = _supervisor(project, tmp_path / "life", planner)

    assert supervisor._plan_next_work() is True

    pending = supervisor.memory.backlog.pending()
    # A key naming nothing at all is still dropped; a filtered sibling is not.
    assert [item.title for item in pending] == ["Independent cleanup"]
    held = [
        event for event in _events(supervisor)
        if event["type"] == EventType.LIFE_PLANNER_TASK_SKIPPED
        and event.get("skip_category") == "filtered_dependency"
    ]
    assert sorted(event["title"] for event in held) == [
        "Report the parse", "Use the parser",
    ]


def test_operator_question_on_an_ended_node_is_held_and_answerable(
    tmp_path: Path, monkeypatch,
) -> None:
    monkeypatch.setenv("ARGUS_SKILL_OPERATOR_AVAILABLE", "true")
    project = _project(tmp_path, monkeypatch)
    supervisor = _supervisor(project, tmp_path / "life")
    backlog = supervisor.memory.backlog
    source = backlog.add(BacklogItem.new(title="Plan production", objective="plan"))
    dependent = backlog.add(
        BacklogItem.new(title="Ship", objective="ship", deps=[source.id])
    )
    backlog.mark_failed(source.id, error="completed work requires a new plan")
    assert all(item.id != source.id for item in backlog.active())

    action = supervisor._adjudicate_mission_challenge({
        "item_id": source.id,
        "status": "replan_requested",
        "plan_challenge": {
            "manager_action": "ask_operator",
            "challenge": "The work order is held by an unreleased gate.",
        },
    })

    assert action == "ask_operator"
    holder = next(
        item for item in backlog.active() if item.status == "paused_operator"
    )
    assert holder.id != source.id and holder.title == source.title
    assert "unreleased gate" in holder.pending_question
    assert holder.operator_decision["item_id"] == holder.id
    rewired = next(item for item in backlog.active() if item.id == dependent.id)
    assert rewired.deps == [holder.id]
    backlog.next_pending()
    assert next(
        item for item in backlog.history() if item.id == dependent.id
    ).status == "pending", "the dependent waits for the answer, not the cascade"

    blocked, continuation = backlog.continue_with_operator_reply(holder.id, "Yes.")
    assert blocked is not None and blocked.status == "failed"
    assert continuation is not None and continuation.status == "pending"


def test_a_question_about_finished_work_neither_holds_nor_skips_its_dependents(
    tmp_path: Path,
) -> None:
    backlog = LifeMemory.open(tmp_path / "life").backlog
    source = backlog.add(BacklogItem.new(title="Build", objective="build"))
    dependent = backlog.add(
        BacklogItem.new(title="Ship", objective="ship", deps=[source.id])
    )
    backlog.update(source.id, status="done", finished_ts=time.time())

    holder = backlog.park_terminal_operator_question(
        source.id,
        holder_id=BacklogItem.new_id(),
        question="May the gate change?",
        operator_decision={},
    )

    assert holder is not None and holder.status == "paused_operator"
    row = next(item for item in backlog.active() if item.id == dependent.id)
    assert row.deps == [source.id]
    assert backlog.stop_for_operator_decision(holder.id) is not None
    assert backlog.next_pending().id == dependent.id


def test_a_question_whose_mission_left_no_row_does_not_raise(
    tmp_path: Path, monkeypatch,
) -> None:
    monkeypatch.setenv("ARGUS_SKILL_OPERATOR_AVAILABLE", "true")
    project = _project(tmp_path, monkeypatch)
    supervisor = _supervisor(project, tmp_path / "life")

    action = supervisor._adjudicate_mission_challenge({
        "item_id": "never-existed",
        "status": "replan_requested",
        "plan_challenge": {
            "manager_action": "ask_operator",
            "challenge": "The work order is held by an unreleased gate.",
        },
    })

    assert action == "ask_operator"
    assert supervisor.memory.backlog.active() == []


def test_structured_planner_decision_carries_retirements() -> None:
    verdict = parse_planner_text(json.dumps({
        "project_done": False,
        "reason": "Close the refuted line.",
        "retire_tasks": [
            {"id": "abc123", "reason": "refuted"},
            "def456 | superseded by the new design",
            {"id": "noreason"},
        ],
    }))

    assert not verdict.error
    assert verdict.retire_tasks == (
        ("abc123", "refuted"),
        ("def456", "superseded by the new design"),
    )
    assert any("retirement 3 skipped" in note for note in verdict.diagnostics)


# I2/I1: a Manager WAIT parks its own tasks, never the whole project.


def test_manager_wait_grants_the_planner_one_turn_per_input_state(
    tmp_path: Path, monkeypatch,
) -> None:
    project = _project(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "argus.manager.supervision.waiting_for_evidence", lambda *_a, **_k: True
    )
    planner = _PlannerBackend(["\n".join([
        "PROJECT_DONE=false",
        "REASON=Benchmark the solver while the operator decides the venue.",
        "TASK_KEY=bench",
        "TASK_TITLE=Benchmark the solver",
        "TASK_OBJECTIVE=Run the benchmark suite.",
    ])])
    supervisor = _supervisor(project, tmp_path / "life", planner)
    backlog = supervisor.memory.backlog
    question = backlog.add(BacklogItem.new(title="Pick a venue", objective="venue"))
    backlog.update(question.id, status="paused_operator", pending_question="Venue?")

    # Independent work is planned instead of the whole campaign freezing.
    assert supervisor._plan_next_work() is True
    assert len(planner.calls) == 1
    assert [item.title for item in backlog.pending()] == ["Benchmark the solver"]

    # Nothing the Planner reads has moved: the WAIT holds without a call.
    assert supervisor._plan_next_work() == PLAN_AWAITING
    assert len(planner.calls) == 1

    # A restart (a crash-looping daemon, say) reads what the Planner last saw
    # back from disk: unchanged inputs still hold without a call.
    restarted = _supervisor(project, tmp_path / "life", planner)
    assert restarted._plan_next_work() == PLAN_AWAITING
    assert len(planner.calls) == 1


def test_an_unreadable_last_turn_record_allows_one_planner_call(
    tmp_path: Path, monkeypatch,
) -> None:
    project = _project(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "argus.manager.supervision.waiting_for_evidence", lambda *_a, **_k: True
    )
    wait = "\n".join([
        "PROJECT_DONE=false",
        "REASON=Nothing to do until the operator picks the venue.",
        "WAITING=true",
        "WAITING_REASON=The venue is undecided.",
    ])
    planner = _PlannerBackend([wait, wait])
    life = tmp_path / "life"
    supervisor = _supervisor(project, life, planner)
    assert supervisor._plan_next_work() == PLAN_AWAITING
    assert len(planner.calls) == 1
    record = life / "handoffs" / "lineage-planning" / "holders.json"
    assert json.loads(record.read_text(encoding="utf-8"))["planner"]["facts"]

    record.write_text("{not json", encoding="utf-8")
    restarted = _supervisor(project, life, planner)
    assert restarted._planner_last_turn_signature == ""
    assert restarted._plan_next_work() == PLAN_AWAITING
    assert len(planner.calls) == 2
    # That call is recorded again, so the next restart holds.
    assert _supervisor(project, life, planner)._plan_next_work() == PLAN_AWAITING
    assert len(planner.calls) == 2



# I2/I3: a Planner wait is bound by the host and climbs instead of being re-asked.

_UNOBSERVED_WAIT = "\n".join([
    "PROJECT_DONE=false",
    "REASON=The vendor has not published the dataset.",
    "WAITING=true",
    "WAIT=the vendor publishes the new dataset",
])


@pytest.mark.parametrize("open_ended", [False, True])
def test_an_unchanged_wait_costs_no_more_calls_than_the_ladder_is_high(
    tmp_path: Path, monkeypatch, open_ended: bool,
) -> None:
    """Planner waits, the Manager confirms the wait stands, the Planner sees
    that ruling once -- and then nothing is called while nothing changes."""
    project = _project(tmp_path, monkeypatch)
    planner = _PlannerBackend([_UNOBSERVED_WAIT] * 10)
    supervisor = _supervisor(project, tmp_path / "life", planner)
    supervisor.config.open_ended = open_ended
    manager = _StageManager(_stage_decision(resolves_wait=True))
    supervisor._bound_manager = lambda: manager  # type: ignore[method-assign]

    for _ in range(10):
        supervisor._plan_next_work()

    assert len(manager.calls) == 1
    assert len(planner.calls) == 2


def test_a_file_wait_that_outlives_its_deadline_climbs_to_the_manager(
    tmp_path: Path, monkeypatch,
) -> None:
    from argus.life.supervisor._constants import FILE_WAIT_CLIMB_SECONDS
    from argus.planner import WaitingContract

    project = _project(tmp_path, monkeypatch)
    (project / "results").mkdir()
    (project / "results" / "sweep.json").write_text("{}", encoding="utf-8")
    supervisor = _supervisor(project, tmp_path / "life")
    manager = _StageManager(_stage_decision())
    supervisor._bound_manager = lambda: manager  # type: ignore[method-assign]
    assert supervisor._persist_planner_waiting_contract(
        WaitingContract(recheck_condition="results/sweep.json gets the final row")
    ) is not None

    supervisor._planner_event_wait_outcome()  # the one idle-capacity turn
    assert supervisor._planner_event_wait_outcome() == PLAN_AWAITING
    assert manager.calls == []

    later = time.time() + FILE_WAIT_CLIMB_SECONDS + 1
    monkeypatch.setattr(time, "time", lambda: later)
    supervisor._planner_event_wait_outcome()

    assert len(manager.calls) == 1
    assert manager.calls[0]["planner_verdict"].waiting_contract.blocker_fingerprint == (
        "unobserved"
    )
    assert any(
        event.get("wake_reason") == "expired" for event in _events(supervisor)
    )


def test_a_wait_persisted_again_keeps_the_authorization_bound_to_it(
    tmp_path: Path, monkeypatch,
) -> None:
    """The operator reads the wait, the Planner declares it again, and the
    authorization issued against what the operator read still lands."""
    from argus.manager.control_state import CampaignControlStore
    from argus.planner import WaitingContract

    project = _project(tmp_path, monkeypatch)
    supervisor = _supervisor(project, tmp_path / "life")
    wait = WaitingContract(
        recheck_condition="the operator approves the extra GPU budget",
        operator_action_required=True,
    )
    first = supervisor._persist_planner_waiting_contract(wait)
    store = CampaignControlStore(supervisor.memory.root, project_root=project)
    head = store.read_head()
    read_by_operator = store.read_snapshot(head)["active_wait"]

    second = supervisor._persist_planner_waiting_contract(wait)

    assert first is not None and second is not None
    for key in ("blocker_fingerprint", "recheck_token", "wait_id", "state_revision"):
        assert second[key] == first[key]
    assert read_by_operator["wait_id"] == second["wait_id"]
    assert supervisor._load_planner_waiting_contract_state() is not None
    authorization = store.issue_authorization(
        identity=store.campaign_identity(objective=supervisor.config.continuous_objective),
        blocker_fingerprint=read_by_operator["blocker_fingerprint"],
        allowed_actions=["resume_blocked_work"],
        scope="active_blocker",
        evidence_paths=[],
        source_channel="web",
        source_message_id="turn-1",
        expected_state_revision=head.state_revision,
        expected_wait_id=read_by_operator["wait_id"],
    )
    assert authorization.blocker_fingerprint == "operator"
