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
from argus.life.supervisor._constants import (
    MANAGER_FEEDBACK_REPLAN_LIMIT,
    PLAN_AWAITING,
    PLAN_RETRY,
)
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
    backlog.update(park.id, park_reason="research_open")
    child = backlog.add(
        BacklogItem.new(title="Write up the bound", objective="write", deps=[park.id])
    )
    question = backlog.add(BacklogItem.new(title="Pick a venue", objective="venue"))
    backlog.update(
        question.id, status="paused_operator", pending_question="Which venue?"
    )

    digest = supervisor._planner_current_reality_note()
    assert f"parked task {park.id} (research_open)" in digest
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
    assert rows[question.id].status == "parked"
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
    backlog.update(park.id, park_reason="research_open")
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
    assert blocked is not None and blocked["status"] == "runtime_block"
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
    assert held is not None and held["status"] == "runtime_block"
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
    assert held is not None and held["status"] == "runtime_block"
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

    resumed, answered = backlog.resume_with_operator_reply(asked.id, "Yes.")

    assert answered
    assert CANARY_TAG not in resumed.tags and "planner" in resumed.tags


# Lossy handoffs.


def test_handoff_with_concrete_tasks_enqueues_the_tasks(
    tmp_path: Path, monkeypatch,
) -> None:
    project = _project(tmp_path, monkeypatch)
    planner = _PlannerBackend(["\n".join([
        "PROJECT_DONE=false",
        "REASON=The sweep is still running; write the analysis harness meanwhile.",
        "TASK_KEY=harness",
        "TASK_TITLE=Write the analysis harness",
        "TASK_OBJECTIVE=Write the harness that reads the sweep output schema.",
        "→ engineer",
    ])])
    supervisor = _supervisor(project, tmp_path / "life", planner)

    assert supervisor._plan_next_work() is True

    assert [item.title for item in supervisor.memory.backlog.pending()] == [
        "Write the analysis harness"
    ]


def _handoff_with_task(baton: str) -> str:
    return "\n".join([
        "PROJECT_DONE=false",
        "REASON=The release is held; write the changelog meanwhile.",
        "TASK_KEY=changelog",
        "TASK_TITLE=Write the changelog",
        "TASK_OBJECTIVE=Write the changelog for the held release.",
        baton,
    ])


def test_operator_question_with_tasks_is_asked_beside_them(
    tmp_path: Path, monkeypatch,
) -> None:
    monkeypatch.setenv("ARGUS_SKILL_OPERATOR_AVAILABLE", "true")
    project = _project(tmp_path, monkeypatch)
    life = tmp_path / "life"
    planner = _PlannerBackend([_handoff_with_task("→ operator: Is the release hold lifted?")])
    supervisor = _supervisor(project, life, planner)

    assert supervisor._plan_next_work() is True

    backlog = supervisor.memory.backlog
    assert [item.title for item in backlog.pending()] == ["Write the changelog"]
    [card] = [item for item in backlog.active() if item.status == "parked"]
    assert card.pending_question == "Is the release hold lifted?"
    assert not list(life.glob("planner-waiting-contract*.json"))


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
        "diagnostic": "intentional_hold",
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
    planner = _PlannerBackend([_handoff_with_task("→ manager")])
    supervisor = _supervisor(project, life, planner)
    manager = _StageManager(_stage_decision())
    supervisor._bound_manager = lambda: manager  # type: ignore[method-assign]

    assert supervisor._plan_next_work() == PLAN_RETRY

    assert len(manager.calls) == 1
    assert manager.calls[0]["open_ended"] is False
    assert manager.calls[0]["planner_verdict"].new_tasks == []
    assert supervisor.memory.backlog.pending() == []
    feedback = supervisor._load_manager_planner_feedback()
    assert feedback["diagnostic"] == "intentional_hold"


def test_stage_reconciliation_request_held_by_a_failsafe_keeps_tasks(
    tmp_path: Path, monkeypatch,
) -> None:
    project = _project(tmp_path, monkeypatch)
    life = tmp_path / "life"
    planner = _PlannerBackend([_handoff_with_task("→ manager")])
    supervisor = _supervisor(project, life, planner)
    manager = _StageManager(_stage_decision(source="no_review_hold", diagnostic=""))
    supervisor._bound_manager = lambda: manager  # type: ignore[method-assign]

    assert supervisor._plan_next_work() is True

    assert len(manager.calls) == 1
    assert [item.title for item in supervisor.memory.backlog.pending()] == [
        "Write the changelog"
    ]


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


# I2/I1: a Manager WAIT holds only the evidence it judged.


def test_a_manager_wait_is_no_planner_input_until_its_evidence_moves(
    tmp_path: Path, monkeypatch,
) -> None:
    """2026-10-20: the Manager answered a blocked task with WAIT and the Planner
    was called 25 seconds later only to say it was waiting too. The WAIT is the
    Manager's answer to the state it judged; once that state moves, the WAIT no
    longer holds and the Planner plans the work that does not need the answer."""
    project = _project(tmp_path, monkeypatch)
    waiting = {"now": True}
    monkeypatch.setattr(
        "argus.manager.supervision.waiting_for_evidence",
        lambda *_a, **_k: waiting["now"],
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

    assert supervisor._plan_next_work() == PLAN_AWAITING
    assert _supervisor(project, tmp_path / "life", planner)._plan_next_work() == PLAN_AWAITING
    assert planner.calls == []

    waiting["now"] = False
    assert supervisor._plan_next_work() is True
    assert len(planner.calls) == 1
    assert [item.title for item in backlog.pending()] == ["Benchmark the solver"]


# I2/I3: a Planner that hands the stage to the Manager is answered once.

_MANAGER_HANDOFF = "\n".join([
    "PROJECT_DONE=false",
    "REASON=The vendor has not published the dataset the stage needs.",
    "→ manager",
])


def test_a_handoff_the_managers_backend_left_unanswered_is_retried_as_a_failed_turn(
    tmp_path: Path, monkeypatch,
) -> None:
    """The Manager's backend gave no answer to the Planner's handoff. Argus
    launched that backend, so the turn failed: the Planner's failure streak
    paces the retry instead of the handoff hanging until something changes."""
    project = _project(tmp_path, monkeypatch)
    planner = _PlannerBackend([_MANAGER_HANDOFF] * 2)
    supervisor = _supervisor(project, tmp_path / "life", planner)
    manager = _StageManager(_stage_decision(source="failsafe_hold", diagnostic="exception"))
    supervisor._bound_manager = lambda: manager  # type: ignore[method-assign]

    supervisor._plan_next_work()
    assert supervisor._load_planner_failure_state()["kind"] == "backend"

    manager.decision = _stage_decision()
    assert supervisor._plan_next_work() == PLAN_RETRY
    assert len(planner.calls) == 2 and len(manager.calls) == 2
    assert supervisor._load_planner_failure_state() == {}
    assert supervisor._load_manager_planner_feedback()["reason"] == "The stage still owes its review."


@pytest.mark.parametrize("open_ended", [False, True])
def test_an_unchanged_handoff_costs_no_more_calls_than_the_ladder_is_high(
    tmp_path: Path, monkeypatch, open_ended: bool,
) -> None:
    """The Planner hands the stage to the Manager, the Manager holds it and
    says why, and a Planner that answers the same way again is bounded by the
    Manager-feedback limit -- then nothing is called while nothing changes."""
    project = _project(tmp_path, monkeypatch)
    planner = _PlannerBackend([_MANAGER_HANDOFF] * 10)
    supervisor = _supervisor(project, tmp_path / "life", planner)
    supervisor.config.open_ended = open_ended
    manager = _StageManager(_stage_decision())
    supervisor._bound_manager = lambda: manager  # type: ignore[method-assign]

    for _ in range(10):
        supervisor._plan_next_work()

    assert len(manager.calls) == 1
    assert len(planner.calls) == MANAGER_FEEDBACK_REPLAN_LIMIT


def _answered(backlog, question: str, answer: str, asked_at: float) -> BacklogItem:
    """A task that asked ``question`` at ``asked_at`` and was answered."""
    from argus.core.operator_decision import build_operator_decision

    row = backlog.add(BacklogItem.new(title=f"asked at {asked_at:.0f}", objective="wait for my choice"))
    card = build_operator_decision(item_id=row.id, title=row.title, reason="choice", question=question)
    card["asked_at"] = asked_at
    backlog.update(row.id, park_reason="operator_question", pending_question=question, operator_decision=card)
    resumed, _ = backlog.resume_with_operator_reply(row.id, answer, manager_decision=answer)
    return resumed


def test_an_answered_task_still_shows_the_operator_their_own_request(tmp_path: Path) -> None:
    """Trial, 2026-10-21: once answered, the map showed the roles' English
    decision brief as the task's objective in place of the operator's words."""
    from argus.webapi.map_view import read_map

    life = tmp_path / "projects" / "s-answered"
    asked = _answered(LifeMemory.open(life).backlog, "A or B?", "A.", 100.0)

    assert asked.objective.startswith("Authoritative Manager operator-answer decision")
    task = next(t for t in read_map("s-answered", tmp_path, life)["tasks"] if t["id"] == asked.id)
    assert task["objective"] == "wait for my choice"


def _answers_read(supervisor: LifeSupervisor, project: Path) -> tuple[str, str]:
    """What the Planner's digest and the Manager's stage decision say was answered."""
    from argus.manager import Manager

    manager = Manager(project, runner=None, manager_session_root=supervisor.memory.root)
    return supervisor._planner_current_reality_note(), manager._stage_operator_context()


def test_planner_and_manager_read_every_answer_given_since_the_objective_began(
    tmp_path: Path, monkeypatch,
) -> None:
    """2026-10-20: after the operator chose A, the Manager's stage decision
    still held for "your choice between A and B is missing": it read only the
    objective written before the question. Both now read the answers given
    since the objective began, all of them and none from before."""
    from argus.daemon.state import write_continuous_config

    project = _project(tmp_path, monkeypatch)
    supervisor = _supervisor(project, tmp_path / "life", _PlannerBackend([]))
    root, backlog = supervisor.memory.root, supervisor.memory.backlog
    write_continuous_config(root, enabled=True, objective="keep improving the solver")
    _answered(backlog, "Keep the old parser?", "Yes.", 100.0)
    JsonlEventSink(None, life_dir=root).append({
        "type": EventType.LIFE_MANAGER_INTENT_COMPLETED, "ts": 200.0, "intent_id": "i-1", "item_id": "i-1",
        "objective": "keep improving the solver", "execution_task": "keep improving the solver",
        "vertical": "software", "stages": ["delivery"],
    })
    for number in range(4):
        _answered(backlog, f"Choice {number}?", f"Option {number}.", 300.0 + number)

    digest, context = _answers_read(supervisor, project)
    answers = [f"Choice {number}? → Option {number}. (the operator)" for number in range(4)]
    assert "- operator_answers (their later word wherever the objective says otherwise): " + "; ".join(answers) in digest
    assert "## The operator's answers since the objective" in context
    assert all(f"- {answer}" in context for answer in answers)
    assert "Keep the old parser?" not in digest and "Keep the old parser?" not in context


def test_without_a_record_of_the_objective_s_start_the_live_backlog_bounds_the_answers(
    tmp_path: Path, monkeypatch,
) -> None:
    from argus.daemon.state import write_continuous_config

    project = _project(tmp_path, monkeypatch)
    supervisor = _supervisor(project, tmp_path / "life", _PlannerBackend([]))
    root, backlog = supervisor.memory.root, supervisor.memory.backlog
    write_continuous_config(root, enabled=True, objective="keep improving the solver")
    finished = _answered(backlog, "Keep the old parser?", "Yes.", 100.0)
    backlog.update(finished.id, status="done", finished_ts=time.time())
    _answered(backlog, "Which dataset?", "The small one.", 300.0)

    digest, context = _answers_read(supervisor, project)
    assert "Which dataset? → The small one. (the operator)" in digest and "Which dataset?" in context
    assert "Keep the old parser?" not in digest and "Keep the old parser?" not in context
